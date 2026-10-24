"""她自己的安卓手机：常驻一条 scrcpy 画面流用来截图，点按、滑动、按键、打字走 adb。

WHY: 维护者 2026-10-24：这是专门给她的备用机，登的是她自己的 QQ，支付宝没绑卡也没余额；
她自己想动就动，不设 op 门槛，维护者相信她不会被别人随便骗。手机因此和代码、shell 工具
同属宿主机信任域；以后要收紧，先问维护者。

## 为什么截图走画面流

WHY: dsh 2026-10-24 在这台手机上实测：`screencap -p` 端到端约 5 秒，大头是手机上编码 PNG
（有壁纸时 4.5 秒），传输只占一两秒，所以省网络没用。scrcpy-server 用手机的硬件编码器出
H.264，流常驻之后每张 0.24–0.33 秒；可起一次流要 3–5 秒，游戏满载时 20–40 秒。所以第一次
要看时才起，之后留着，`IDLE` 秒没人看才关。流由后台线程持有，不跟着某一次工具调用走。

## 为什么只攒最近一个关键帧以来的包、看的时候才解码

WHY: 这台手机画面不动时编码器什么都不发（scrcpy 设的"100ms 没变就重发上一帧"在它上面
不生效，dsh 实测），所以收到的最后一帧就是当前画面，一直解码是白费。看的时候把关键帧以来
的包交给 ffmpeg，留下最后一帧。开着游戏时画面一直在动，关键帧 10 秒一个，一次要解几十到
上百帧；嫌慢再考虑缩短关键帧间隔或换成进程内解码。

## 协议：scrcpy 1.25，版本写死

server 是配置里给的那份 1.25 的 jar（它会核对这边报的版本，不对就退出）。参数里关掉控制
连接（`control=false`）和开头那个探测字节（`send_dummy_byte=false`）：接上先是 68 字节设备
信息（64 字节名字，大端 2 字节宽、2 字节高），之后每包 12 字节头（8 字节 pts，最高位表示
SPS/PPS，次高位表示关键帧；4 字节长度）加 H.264 数据。2.x 的格式不一样，换版本要改这里。

## 配置

`data/device/phone.json`：`serial`（adb 序列号，走网络的写 `地址:端口`）、`server`（本机上
1.25 的 scrcpy-server 文件），可选 `adb`、`ffmpeg`、`max_size`、`bit_rate`、`max_fps`。设备
地址是私有部署值，只放这里。
"""

from __future__ import annotations

import base64
import json
import math
import os
import re
import socket
import struct
import subprocess
import threading
import time

from mods import FEATURE, log


PHASE = FEATURE

CONFIG_PATH = os.path.join("data", "device", "phone.json")
ROOT = os.path.abspath(os.path.join("data", "phone"))
SHOT_DIR = os.path.join(ROOT, "shots")
SERVER_LOG = os.path.join(ROOT, "server.log")
SHOT_KEEP = 30

REMOTE_JAR = "/data/local/tmp/scrcpy-server-1.25.jar"
SERVER_CLASS = "com.genymobile.scrcpy.Server"
PORT = 27183
# WHY: dsh 用的是 720，竖屏出来宽只有 328，给模型认字太小；先用 1280，不够再在配置里调。
MAX_SIZE = 1280
MAX_FPS = 10
BIT_RATE = 4_000_000
# 多久没人看就关流（秒）。
# WHY: 冷启动要 5 秒上下（起 server + 等编码器，dsh 实测 5.3 秒），留得比一次对话长，
# 半小时内再操作就不用重新起。想省电、或者不想让它一直看着屏幕，就把这个数调小。
IDLE = 1800
# 整张图的平均亮度（0–255）低于这个数，就当屏幕灭着。灭屏时流里只有黑帧。
DARK = 10
# WHY: 游戏满载时 server 要 20–40 秒才接得上（dsh 实测 40 秒）。等得比这短，就会在它快好
# 的时候判失败，下一轮开头 pkill 把它杀掉、再起一个又超时，一直循环，日志里只有 EOF。
CONNECT_TIMEOUT = 60
# WHY: 刚亮屏时编码器要约 1 秒才出新画面，这之前解出来的是息屏时的黑帧（dsh 实测）。
WAKE_SETTLE = 1.2
# 攒的包超过这么多字节就丢掉，等下一个关键帧。
GOP_MAX = 16 << 20
META_LENGTH = 68
HEADER = struct.Struct(">QI")
KEYBOARD = "com.android.adbkeyboard/.AdbIME"
# keyboard() 切换的两个输入法：设置页上的名字、默认输入法里认它的那段。
_KEYBOARDS = {"adb": ("ADB Keyboard", "adbkeyboard"), "sogou": ("搜狗", "sogou")}
_DUMP = "/sdcard/yuzu-ui.xml"
MAX_TEXT = 2000
# 连拍拼图里每一格的长边（像素）。
CELL = 400
MAX_OUTPUT = 4000

_KEYS = {
    "back": "BACK", "home": "HOME", "recent": "APP_SWITCH", "recents": "APP_SWITCH",
    "enter": "ENTER", "delete": "DEL", "backspace": "DEL", "power": "POWER",
    "wakeup": "WAKEUP", "menu": "MENU", "volume_up": "VOLUME_UP", "volume_down": "VOLUME_DOWN",
}

_stream = log.stream("phone")

# 起停流的那一把；_state 管下面这些由读流线程写、截图时读的值。
_lock = threading.Lock()
_state = threading.Condition()
_stop = threading.Event()
_thread: threading.Thread | None = None
_server: subprocess.Popen | None = None
_socket: socket.socket | None = None
_connected = False
_failures = 0
_error = ""
_device = ""
_parameters: bytes = b""
_gop = bytearray()
_gop_frames = 0
# 连拍期间读流线程把每个包另抄一份到这里。
_tap: list[bytes] | None = None
_tap_frames = 0
_packets = 0
_bytes = 0
_last_packet = 0.0
_used = 0.0
# 最近一次给她看的那张图有多大：她给的坐标都照这张图。
_shown: tuple[int, int] | None = None
_screen: tuple[int, int] | None = None


# --------------------------------------------------------------------------- 配置与 adb


def _config() -> dict:
    try:
        with open(CONFIG_PATH, encoding="utf-8") as handle:
            value = json.load(handle)
    except FileNotFoundError:
        raise RuntimeError(f"还没配置手机：缺 {CONFIG_PATH}") from None
    if not isinstance(value, dict):
        raise TypeError(f"{CONFIG_PATH} 必须是对象")
    for key in ("serial", "server"):
        if not isinstance(value.get(key), str) or not value[key]:
            raise TypeError(f"{CONFIG_PATH} 缺字符串 {key}")
    return value


def _adb(config: dict, *args: str, timeout: float = 20.0, check: bool = True) -> subprocess.CompletedProcess:
    adb, serial = config.get("adb") or "adb", config["serial"]
    command = [adb, "-s", serial, *args]
    try:
        result = subprocess.run(command, capture_output=True, text=True, timeout=timeout, check=False)
        if result.returncode and ":" in serial and re.search(r"not found|offline", result.stderr):
            # WHY: adb server 重启会丢掉走网络的设备，不重连就是 device not found（dsh 踩过）；
            # 重连是幂等的，所以哪条命令撞上了就重连一次再试。
            subprocess.run([adb, "connect", serial], capture_output=True, text=True, timeout=15, check=False)
            result = subprocess.run(command, capture_output=True, text=True, timeout=timeout, check=False)
    except subprocess.TimeoutExpired:
        # 超时异常的说明里带着整条命令，包括设备地址。
        raise TimeoutError(f"adb {' '.join(args)[:80]} 超过 {timeout:g} 秒没回来") from None
    if check and result.returncode:
        # 报错会进她的上下文、可能被她转述出去，设备地址换掉。
        output = (result.stdout + result.stderr).strip().replace(serial, "<手机>")
        raise RuntimeError(f"adb {' '.join(args)[:80]} 失败（{result.returncode}）：{output[-300:]}")
    return result


def _shell(config: dict, command: str, timeout: float = 20.0, check: bool = True) -> str:
    """在手机上跑一条 shell；*command* 由手机上的 shell 解析，调用方自己负责引号。"""
    return _adb(config, "shell", command, timeout=timeout, check=check).stdout


def _tail(path: str, serial: str, limit: int = 600) -> str:
    try:
        with open(path, "rb") as handle:
            handle.seek(max(0, os.path.getsize(path) - limit))
            return handle.read().decode("utf-8", "replace").strip().replace(serial, "<手机>")
    except OSError:
        return ""


# --------------------------------------------------------------------------- 读流


def _idle() -> bool:
    # WHY: `_used` 是 0 表示这一进程还没看过一眼，不能当成"闲置很久"——否则单独调
    # `_ensure()`（预热、查状态）起的流线程会立刻自己退出，再等就是等到超时（dsh 实测 90 秒）。
    if not _used:
        return False
    return time.time() - _used > IDLE


def _run() -> None:
    global _thread, _failures, _error
    delay = 1.0
    while True:
        with _lock:
            if _stop.is_set() or _idle():
                _thread = None
                break
        try:
            _session()
            delay = 1.0
        except Exception as error:
            if _stop.is_set():
                continue
            with _state:
                _failures += 1
                _error = f"{type(error).__name__}: {error}"
                _state.notify_all()
            _stream.warning("手机画面流断了：%s", _error)
            _stop.wait(delay)
            delay = min(delay * 2, 30.0)
    _stream.info("手机画面流已关")


def _session() -> None:
    global _server, _connected
    config = _config()
    if _adb(config, "shell", f"test -f {REMOTE_JAR}", check=False).returncode:
        # WHY: 这台 ROM 会周期性清空 /data/local/tmp（dsh 实测），所以每次起流前都看一眼。
        if not os.path.isfile(config["server"]):
            raise RuntimeError(f"本机上没有 scrcpy-server：{config['server']}")
        _adb(config, "push", config["server"], REMOTE_JAR, timeout=60)
    # WHY: 上一次残留的 server 占着 localabstract:scrcpy，新的 bind 不上，表现为连上立刻 EOF。
    _shell(config, f"pkill -f {SERVER_CLASS}", check=False)
    time.sleep(0.15)
    _adb(config, "forward", f"tcp:{PORT}", "localabstract:scrcpy")
    # WHY: 息屏时新起的 server 拿不到画面、起不来（dsh 实测）。
    _shell(config, "input keyevent KEYCODE_WAKEUP")
    command = (f"CLASSPATH={REMOTE_JAR} app_process / {SERVER_CLASS} 1.25 log_level=warn "
               f"tunnel_forward=true control=false send_dummy_byte=false "
               f"max_size={int(config.get('max_size') or MAX_SIZE)} "
               f"max_fps={int(config.get('max_fps') or MAX_FPS)} "
               f"bit_rate={int(config.get('bit_rate') or BIT_RATE)}")
    os.makedirs(ROOT, exist_ok=True)
    with open(SERVER_LOG, "wb") as output:
        server = subprocess.Popen(
            [config.get("adb") or "adb", "-s", config["serial"], "shell", command],
            stdin=subprocess.DEVNULL, stdout=output, stderr=subprocess.STDOUT)
    _server = server
    connection = None
    try:
        connection = _connect(server, config["serial"])
        _read(connection)
    finally:
        with _state:
            _connected = False
            _state.notify_all()
        _close(connection)
        _server = None
        _terminate(server)
        try:
            # 关掉本机这头的 adb shell 不一定带走手机上的 server；画面不动时它也不会因为写不出去
            # 而自己退出，留着就一直占着编码器和电。
            _shell(config, f"pkill -f {SERVER_CLASS}", timeout=10, check=False)
        except Exception:
            pass


def _connect(server: subprocess.Popen, serial: str) -> socket.socket:
    """一直试到真读到设备信息为止。

    WHY: 手机那头的 server 还没 bind 时，adb forward 也会先接下 TCP 再立刻关掉，所以重试
    要包住"连上 + 读 68 字节"整段，只重试连接没用（dsh 踩过）。
    """
    global _socket, _connected, _error, _device, _parameters, _gop, _gop_frames
    deadline = time.monotonic() + CONNECT_TIMEOUT
    last: Exception | None = None
    while time.monotonic() < deadline and not _stop.is_set():
        if server.poll() is not None:
            raise RuntimeError(f"scrcpy-server 退出了（{server.returncode}）：{_tail(SERVER_LOG, serial)}")
        try:
            connection = socket.create_connection(("127.0.0.1", PORT), timeout=2.0)
        except OSError as error:
            last = error
            time.sleep(0.25)
            continue
        try:
            meta = _exact(connection, META_LENGTH, deadline=time.monotonic() + 6.0)
        except (OSError, EOFError, TimeoutError) as error:
            _close(connection)
            last = error
            time.sleep(0.25)
            continue
        if meta is None:
            _close(connection)
            break
        width, height = struct.unpack(">HH", meta[64:68])
        name = meta[:64].split(b"\0", 1)[0].decode("utf-8", "replace")
        connection.settimeout(2.0)
        _socket = connection
        with _state:
            _connected, _error = True, ""
            _device = f"{name} {width}x{height}"
            _parameters, _gop, _gop_frames = b"", bytearray(), 0
            _state.notify_all()
        _stream.info("手机画面流已连上：%s", _device)
        return connection
    raise TimeoutError(f"等了 {CONNECT_TIMEOUT} 秒没接上 scrcpy-server（{last}）：{_tail(SERVER_LOG, serial)}")


def _exact(connection: socket.socket, size: int, deadline: float = 0.0, between: bool = False) -> bytes | None:
    """读满 *size* 字节；要停时返回 None。

    超时只是醒来看一眼：要停了、或者（*between*，正在两包之间）没人看太久了就返回 None。
    画面不动时流本来就安静，安静不算断，断了是 EOF。
    """
    data = bytearray()
    while len(data) < size:
        if deadline:
            connection.settimeout(max(0.05, deadline - time.monotonic()))
        try:
            chunk = connection.recv(size - len(data))
        except socket.timeout:
            if _stop.is_set() or (between and not data and _idle()):
                return None
            if deadline and time.monotonic() >= deadline:
                raise TimeoutError("没等到数据") from None
            continue
        if not chunk:
            raise EOFError("流断了（EOF）")
        data += chunk
    return bytes(data)


def _read(connection: socket.socket) -> None:
    global _parameters, _gop, _gop_frames, _packets, _bytes, _last_packet, _tap_frames
    while True:
        header = _exact(connection, HEADER.size, between=True)
        if header is None:
            return
        pts, size = HEADER.unpack(header)
        if size > 8 << 20:
            raise ValueError(f"包大小不对：{size}")
        payload = _exact(connection, size)
        if payload is None:
            return
        with _state:
            if pts >> 63:
                _parameters = payload
            elif pts >> 62 & 1:
                _gop, _gop_frames = bytearray(_parameters + payload), 1
            elif _gop:
                # 关键帧丢了（刚连上、或攒太多被清掉）时后面的帧解不出来，不攒，等下一个关键帧。
                _gop += payload
                _gop_frames += 1
            if len(_gop) > GOP_MAX:
                _gop, _gop_frames = bytearray(), 0
            if _tap is not None:
                _tap.append(payload)
                _tap_frames += 0 if pts >> 63 else 1
            _packets += 1
            _bytes += size
            _last_packet = time.time()
            _state.notify_all()


def _close(connection: socket.socket | None) -> None:
    global _socket
    if connection is None:
        return
    if _socket is connection:
        _socket = None
    try:
        connection.shutdown(socket.SHUT_RDWR)
    except OSError:
        pass
    connection.close()


def _terminate(process: subprocess.Popen) -> None:
    try:
        if process.poll() is None:
            process.terminate()
            try:
                process.wait(5)
            except subprocess.TimeoutExpired:
                process.kill()
    except Exception:
        _stream.info("关 scrcpy-server 的 adb shell 失败", exc_info=True)


def _ensure() -> None:
    """流没在跑就起一条，等到连上；起不来抛出原因。"""
    global _thread
    with _lock:
        if _thread is None:
            _stop.clear()
            _thread = threading.Thread(target=_run, name="phone-stream", daemon=True)
            _thread.start()
    with _state:
        failures = _failures
        # 起流前那几条 adb 也要时间，多给一点。
        deadline = time.monotonic() + CONNECT_TIMEOUT + 30
        while not _connected:
            if _failures != failures:
                raise RuntimeError(f"手机画面流没起来：{_error}")
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise TimeoutError("手机画面流还没起来")
            _state.wait(remaining)


def warm() -> str:
    """提前把画面流起好，下一次 look 就不用等冷启动那几秒；不截图、不阻塞。"""
    global _used, _thread
    _used = time.time()
    with _lock:
        if _thread is not None:
            return "画面流已经在跑"
        _stop.clear()
        thread = threading.Thread(target=_run, name="phone-stream", daemon=True)
        _thread = thread
        thread.start()
    return "画面流开始预热"


def stop(reason: str = "") -> str:
    """关掉画面流（下次看时再起）。"""
    with _lock:
        thread = _thread
        if thread is None:
            return "手机画面流本来就没在跑"
        _stop.set()
    _close(_socket)
    if _server is not None:
        _terminate(_server)
    thread.join(10)
    _stream.info("手机画面流已关（%s）", reason or "未说明原因")
    return "手机画面流已关"


def on_exit() -> None:
    stop("模块退出")


# --------------------------------------------------------------------------- 看


def _decode(data: bytes, config: dict) -> str:
    os.makedirs(SHOT_DIR, exist_ok=True)
    stamp = time.strftime("%Y%m%d-%H%M%S") + f"-{int(time.time() * 1000) % 1000:03d}"
    path = os.path.join(SHOT_DIR, f"shot-{stamp}.jpg")
    source = path[:-4] + ".h264"
    with open(source, "wb") as handle:
        handle.write(data)
    try:
        # `-update 1` 让每一帧都覆盖同一个文件，解完留下的就是最后一帧（dsh 跑通的写法）。
        result = subprocess.run(
            [config.get("ffmpeg") or "ffmpeg", "-y", "-v", "error", "-f", "h264", "-i", source,
             "-update", "1", "-q:v", "4", path],
            capture_output=True, text=True, timeout=30, check=False)
    finally:
        os.remove(source)
    if result.returncode or not os.path.isfile(path):
        raise RuntimeError(f"ffmpeg 解码失败（{result.returncode}）：{result.stderr.strip()[-300:]}")
    _prune()
    return path


def _prune() -> None:
    try:
        shots = sorted(name for name in os.listdir(SHOT_DIR) if name.startswith("shot-"))
    except OSError:
        return
    for name in shots[:-SHOT_KEEP]:
        try:
            os.remove(os.path.join(SHOT_DIR, name))
        except OSError:
            pass


def _ready() -> dict:
    """确保流在跑、等到有画面，返回配置。"""
    global _used
    _used = time.time()
    config = _config()
    _ensure()
    with _state:
        # WHY: server 先发设备信息、再去配编码器，所以连上以后第一个关键帧还要等。平时几百毫秒，
        # 游戏满载时能拖很久：dsh 实测原先等 5 秒时撞上过"还没收到关键帧"。所以等得和连接一样久。
        _state.wait_for(lambda: bool(_gop) or not _connected, timeout=CONNECT_TIMEOUT)
        if not _gop:
            raise RuntimeError(f"还没有画面：{_error or '流连上了但还没收到关键帧'}")
    return config


def _snapshot(config: dict, pause: float = 0.0) -> str:
    """解出此刻的最新一帧，返回 jpg 路径。"""
    if pause:
        time.sleep(pause)
    with _state:
        data = bytes(_gop)
    if not data:
        raise RuntimeError("攒的画面刚被清掉，等下一个关键帧再看")
    return _decode(data, config)


def _measure(path: str) -> tuple[int, int, float]:
    """图的宽、高，和平均亮度（0–255）。"""
    from PIL import Image, ImageStat

    with Image.open(path) as picture:
        return (*picture.size, ImageStat.Stat(picture.convert("L")).mean[0])


def screenshot(wait: float = 0.0) -> dict:
    """截当前画面，返回 `{"uri", "width", "height"}`。

    WHY: 以前每张图都先 `dumpsys power` 问亮没亮，那一次往返 0.15 秒（dsh 实测），比解码还
    贵。现在先解，图整张黑才去点亮：灭屏时流里只有黑帧，正常界面再暗也没那么黑。
    """
    global _shown
    config = _ready()
    path = _snapshot(config, min(max(wait, 0.0), 10.0))
    width, height, level = _measure(path)
    if level < DARK:
        _shell(config, "input keyevent KEYCODE_WAKEUP", check=False)
        path = _snapshot(config, WAKE_SETTLE)
        width, height, level = _measure(path)
    _shown = (width, height)
    return {"uri": "file://" + path, "width": width, "height": height}


def _frames(data: bytes, wanted: list[int], config: dict, folder: str) -> list[str]:
    """从 *data* 里解出第 *wanted* 帧（从 0 数），一帧一个文件，按帧号排好。"""
    pick = "+".join(f"eq(n\\,{index})" for index in wanted)
    source = os.path.join(folder, "in.h264")
    with open(source, "wb") as handle:
        handle.write(data)
    result = subprocess.run(
        [config.get("ffmpeg") or "ffmpeg", "-y", "-v", "error", "-f", "h264", "-i", source,
         "-vf", f"select={pick}", "-fps_mode", "passthrough", "-q:v", "3", os.path.join(folder, "%03d.jpg")],
        capture_output=True, text=True, timeout=60, check=False)
    found = sorted(name for name in os.listdir(folder) if name.endswith(".jpg"))
    if result.returncode or not found:
        raise RuntimeError(f"ffmpeg 解码失败（{result.returncode}）：{result.stderr.strip()[-300:]}")
    return [os.path.join(folder, name) for name in found]


def _grid(pictures: list, path: str) -> tuple[int, int]:
    """按时间顺序排成一张图，格子左上角标 #1、#2……；返回 (列, 行)。"""
    from PIL import Image, ImageDraw, ImageFont

    ratio = CELL / max(pictures[0].size)
    width, height = round(pictures[0].width * ratio), round(pictures[0].height * ratio)
    # 列数让整张图接近正方形：竖屏的格子窄，就多排几列。
    cols = max(1, min(len(pictures), math.ceil(math.sqrt(len(pictures) * height / width))))
    rows = math.ceil(len(pictures) / cols)
    gap, header = 6, 26
    canvas = Image.new("RGB", (cols * (width + gap) + gap, rows * (height + gap + header) + gap), "white")
    draw = ImageDraw.Draw(canvas)
    try:
        font = ImageFont.load_default(size=20)
    except Exception:
        font = ImageFont.load_default()
    for index, picture in enumerate(pictures):
        x = gap + index % cols * (width + gap)
        y = gap + index // cols * (height + gap + header)
        draw.text((x + 2, y + 2), f"#{index + 1}", fill=(20, 20, 20), font=font)
        canvas.paste(picture.convert("RGB").resize((width, height)), (x, y + header))
    canvas.save(path, format="JPEG", quality=85, optimize=True)
    return cols, rows


def burst(count: int = 20, interval: float = 0.4) -> dict:
    """每隔 *interval* 秒取一帧，取 *count* 帧，拼成一张图。

    返回 `{"uri", "count", "seconds", "cols", "rows"}`。拼图上的坐标不能拿去点，所以不改
    `_shown`。

    WHY: 维护者 2026-10-24：看视频这种极端情况，每秒两三张、攒二三十张拼成一张给她看也可以。
    一张张 look 做不到这么密，每次都要等模型来回一趟。所以这段时间里读流线程把收到的包
    另抄一份，最后只调一次 ffmpeg，解出要的那几帧。
    """
    global _used, _tap, _tap_frames
    count, interval = max(2, min(int(count), 30)), max(0.2, min(float(interval), 5.0))
    config = _ready()
    with _state:
        start, base = bytes(_gop), _gop_frames
        if not start:
            raise RuntimeError("攒的画面刚被清掉，等下一个关键帧再看")
        _tap, _tap_frames = [], 0
    marks = []
    began = time.monotonic()
    try:
        for index in range(count):
            time.sleep(max(0.0, began + index * interval - time.monotonic()))
            with _state:
                marks.append(base + _tap_frames - 1)
    finally:
        with _state:
            recorded, _tap = _tap or [], None
    _used = time.time()
    seconds = round(time.monotonic() - began, 1)
    os.makedirs(SHOT_DIR, exist_ok=True)
    stamp = time.strftime("%Y%m%d-%H%M%S") + f"-{int(time.time() * 1000) % 1000:03d}"
    folder = os.path.join(SHOT_DIR, f"burst-{stamp}")
    os.makedirs(folder)
    try:
        wanted = sorted(set(marks))
        files = dict(zip(wanted, _frames(start + b"".join(recorded), wanted, config, folder)))
        from PIL import Image

        pictures = []
        for mark in marks:
            if mark in files:
                with Image.open(files[mark]) as picture:
                    pictures.append(picture.copy())
        if not pictures:
            raise RuntimeError("一帧也没解出来")
        path = os.path.join(SHOT_DIR, f"shot-{stamp}-burst.jpg")
        cols, rows = _grid(pictures, path)
    finally:
        for name in os.listdir(folder):
            os.remove(os.path.join(folder, name))
        os.rmdir(folder)
    _prune()
    return {"uri": "file://" + path, "count": len(pictures), "seconds": seconds, "cols": cols, "rows": rows}


# --------------------------------------------------------------------------- 动


def _size(config: dict) -> tuple[int, int]:
    """手机屏幕竖着时的宽高（`wm size`，有改过的以改过的为准）。"""
    global _screen
    if _screen is None:
        found = re.findall(r"size:\s*(\d+)x(\d+)", _shell(config, "wm size"))
        if not found:
            raise RuntimeError("读不出手机屏幕尺寸（wm size）")
        _screen = tuple(sorted(map(int, found[-1])))
    return _screen


def _point(config: dict, x: float, y: float) -> tuple[int, int]:
    """把最近那张图上的坐标换成手机上的；横屏看图宽大于高。"""
    if _shown is None:
        raise RuntimeError("还没看过手机画面：先 look 一眼，坐标照那张图写")
    width, height = _shown
    if not (0 <= x <= width and 0 <= y <= height):
        raise ValueError(f"({x:g}, {y:g}) 在画面外：最近那张图是 {width}×{height}")
    short, long = _size(config)
    across, down = (long, short) if width > height else (short, long)
    return round(x * across / width), round(y * down / height)


def _see(result: dict) -> dict:
    """动作之后顺手把这一刻的画面并进结果；看不成也不让动作本身失败。

    WHY: 动作一返回画面就是最新的（dsh 实测：下拉通知栏那 7 帧在命令返回时已经全到了），
    所以这里不用等；动作和看合成一次调用，省掉"做完再 look 一眼"的那一轮模型往返。
    """
    try:
        shot = screenshot()
    except Exception as error:
        result["shot_error"] = f"{type(error).__name__}: {error}"
        return result
    result.update(uri=shot["uri"], width=shot["width"], height=shot["height"])
    return result


def tap(x: float, y: float) -> dict:
    config = _config()
    left, top = _point(config, x, y)
    _shell(config, f"input tap {left} {top}")
    return _see({"at": (left, top)})


def swipe(x1: float, y1: float, x2: float, y2: float, duration: int = 300) -> dict:
    config = _config()
    start, end = _point(config, x1, y1), _point(config, x2, y2)
    duration = max(1, min(int(duration), 60_000))
    _shell(config, f"input swipe {start[0]} {start[1]} {end[0]} {end[1]} {duration}",
           timeout=duration / 1000 + 20)
    return _see({"from": start, "to": end, "duration": duration})


def key(name: str) -> dict:
    config = _config()
    code = name.strip().lower()
    code = _KEYS.get(code, code.upper().removeprefix("KEYCODE_"))
    if not re.fullmatch(r"[A-Z0-9_]+", code):
        raise ValueError(f"不认识的按键：{name!r}")
    _shell(config, f"input keyevent KEYCODE_{code}")
    return _see({"code": code})


def type_text(text: str, clear: bool = False, enter: bool = False) -> dict:
    """往当前输入框打字，返回 `{"count": 字数, ...动作后的画面}`。

    WHY: `input text` 只打得出 ASCII，中文走 ADB Keyboard 输入法的广播（dsh 2026-10-24 装好并
    设成了默认输入法）。它必须是当前输入法，否则广播照样成功、什么也没打出来，所以先查一眼。
    用 base64 那条广播，正文里的引号和换行不用过手机上那层 shell。
    """
    if len(text) > MAX_TEXT:
        raise ValueError(f"一次最多 {MAX_TEXT} 字")
    config = _config()
    current = _shell(config, "settings get secure default_input_method").strip()
    if current != KEYBOARD:
        raise RuntimeError(f"当前输入法是 {current or '（读不到）'}，不是 ADB Keyboard，打不了字；"
                           "先 keyboard('adb') 切过来")
    if clear:
        _shell(config, "am broadcast -a ADB_CLEAR_TEXT")
    if text:
        encoded = base64.b64encode(text.encode("utf-8")).decode("ascii")
        _shell(config, f"am broadcast -a ADB_INPUT_B64 --es msg {encoded}")
    if enter:
        _shell(config, "input keyevent KEYCODE_ENTER")
    return _see({"count": len(text)})


def _dump(config: dict) -> str:
    """当前界面的 uiautomator XML。先删旧文件：dump 偶尔失败，直接 cat 会读到上一次的。"""
    for _ in range(3):
        xml = _shell(config, f"rm -f {_DUMP}; uiautomator dump {_DUMP} >/dev/null 2>&1; cat {_DUMP} 2>/dev/null",
                     timeout=40, check=False)
        if "<hierarchy" in xml:
            return xml
        time.sleep(1)
    raise RuntimeError("uiautomator dump 连着三次没读到界面")


def _center(tag: str) -> tuple[int, int] | None:
    found = re.search(r'bounds="\[(\d+),(\d+)\]\[(\d+),(\d+)\]"', tag)
    if not found:
        return None
    left, top, right, bottom = map(int, found.groups())
    return (left + right) // 2, (top + bottom) // 2


def _switches(xml: str) -> list[tuple[str, bool, tuple[int, int]]]:
    """设置页上的开关：`(它前面最近的那行字, 开着没有, 中心点)`。"""
    found, title = [], ""
    for tag in re.findall(r"<node[^>]*>", xml):
        text = re.search(r'text="([^"]*)"', tag)
        center = _center(tag)
        if not text or center is None or not text.group(1).strip():
            continue
        value = text.group(1).strip()
        if value in ("开启", "关闭") and "Switch" in (re.search(r'class="([^"]*)"', tag) or ["", ""])[1]:
            found.append((title, value == "开启", center))
        else:
            title = value
    return found


def keyboard(name: str) -> dict:
    """切输入法：`adb` 是 ADB Keyboard（她打字靠它），`sogou` 是搜狗（人在手机上打字用）。

    返回 `{"current": 切完的默认输入法, "switched": 这次有没有去设置页点}`。

    WHY: 手机上的 shell 没有 WRITE_SECURE_SETTINGS，`ime enable/set` 被拒（dsh 实测），只能打开
    「输入法设置」页去点开关；只剩一个可用的输入法时系统会自动把它设成默认，所以开一个、关另一个。
    读界面靠 uiautomator，一次就要好几秒，切一次十几二十秒。照的是维护者 2026-10-24 给的
    脚本，认的是这台手机设置页上的字（开关写"开启/关闭"，启用时弹窗点"使用"）。
    """
    if name not in _KEYBOARDS:
        raise ValueError(f"只认 {'、'.join(_KEYBOARDS)}")
    config = _config()
    want, expect = _KEYBOARDS[name]
    other = next(label for key, (label, _) in _KEYBOARDS.items() if key != name)
    current = _shell(config, "settings get secure default_input_method").strip()
    if expect in current:
        return {"current": current, "switched": False}
    _shell(config, "am start -a android.settings.INPUT_METHOD_SETTINGS")
    time.sleep(2)
    rows = [row for row in _switches(_dump(config)) if want in row[0]]
    if not rows:
        raise RuntimeError(f"输入法设置页里没找到{want}")
    if not rows[0][1]:
        _shell(config, "input tap %d %d" % rows[0][2])
        time.sleep(1.5)
        # 启用输入法时系统会问"要使用 xxx 吗"，点"使用"。
        for _ in range(2):
            dialog = re.search(r'<node[^>]*text="使用"[^>]*>', _dump(config))
            if not dialog or _center(dialog.group(0)) is None:
                break
            _shell(config, "input tap %d %d" % _center(dialog.group(0)))
            time.sleep(1.5)
    for title, enabled, center in _switches(_dump(config)):
        if other in title and enabled:
            _shell(config, "input tap %d %d" % center)
            time.sleep(1.5)
            break
    current = _shell(config, "settings get secure default_input_method").strip()
    if expect not in current:
        raise RuntimeError(f"没切过去：默认输入法还是 {current or '（读不到）'}")
    return {"current": current, "switched": True}


def shell(command: str, timeout: float = 30.0) -> dict:
    """在手机上跑一条 shell 命令，返回退出码和输出。"""
    result = _adb(_config(), "shell", command, timeout=max(1.0, min(timeout, 300.0)), check=False)
    output = (result.stdout + result.stderr).strip()
    if len(output) > MAX_OUTPUT:
        output = output[:MAX_OUTPUT] + f"\n…（共 {len(output)} 字）"
    return {"code": result.returncode, "output": output}


def status() -> dict:
    with _state:
        return {
            "configured": os.path.isfile(CONFIG_PATH),
            "running": _thread is not None,
            "connected": _connected,
            "device": _device,
            "packets": _packets,
            "bytes": _bytes,
            "since_packet": round(time.time() - _last_packet, 1) if _last_packet else None,
            "since_look": round(time.time() - _used, 1) if _used else None,
            "error": _error,
        }


__all__ = [
    "burst",
    "key",
    "keyboard",
    "screenshot",
    "shell",
    "status",
    "stop",
    "swipe",
    "tap",
    "type_text",
    "warm",
]
