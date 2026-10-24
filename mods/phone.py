"""她自己的安卓手机：常驻一条 scrcpy 画面流用来截图，点、滑、按住走这条流的控制连接，按键、打字走 adb。

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

## 为什么点、滑、按住走控制连接

WHY: `input swipe` 从按下到抬起是一条命令，按着的时候什么都干不了，也只有一根手指；维护者
2026-10-24 提出让她在游戏里推着摇杆走、边走边连拍看。scrcpy 的控制连接把触摸事件直接交给已经在跑
的 server：按下、移动、抬起各是一条消息，中间可以截图，另一根手指可以去点。点和滑也走它：server
只把经它按下的手指算成同一组，按着摇杆时点别处要从这里发才是第二根手指，混着 `input tap` 就不是了。
代价是点之前流得开着、她看的那张图得是当前这个方向的（见 `_fresh`）。按键、打字仍走 adb：不常用，
中文也只有 ADB Keyboard 打得出来。

## 协议：scrcpy 1.25，版本写死

server 是配置里给的那份 1.25 的 jar（它会核对这边报的版本，不对就退出）。`tunnel_forward` 下
先后接两条连接：第一条收画面，server 接下它先发一个探测字节（`send_dummy_byte=true`），第二条
是控制连接。两条都接上后，画面那条先来 68 字节设备信息（64 字节名字，大端 2 字节宽、2 字节高），
之后每包 12 字节头（8 字节 pts，最高位表示 SPS/PPS，次高位表示关键帧；4 字节长度）加 H.264
数据。控制连接这边只写 28 字节的触摸消息（`TOUCH`），坐标按画面尺寸算，server 自己换算到屏幕上。
2.x 的格式不一样，换版本要改这里。

## 配置

`data/device/phone.json`：`serial`（adb 序列号，走网络的写 `地址:端口`）、`server`（本机上
1.25 的 scrcpy-server 文件），可选 `adb`、`ffmpeg`、`max_size`、`bit_rate`、`max_fps`。设备
地址是私有部署值，只放这里。她定的键位在 `data/phone/marks.json`，维护者也可以直接改。
"""

from __future__ import annotations

import base64
import itertools
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
# WHY: 她这一段上下文里引用着之前的截图，按原路径读；删早了，下次请求那张就成了一行占位字，前缀
# 缓存也从那儿断开。一段里图多了就会交接，几百张够盖住一段。后台连拍的单帧不进这个数（见
# `screenshot` 的 *shown*）。
SHOT_KEEP = 300

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
# 控制连接上的触摸消息：类型 2、动作、手指编号、x、y、画面宽、高、压力（16 位定点）、鼠标键。
TOUCH = struct.Struct(">BBqiiHHHI")
DOWN, UP, MOVE = 0, 1, 2
# 点一下按多久、按住以后推到位用多久（秒）。
TAP_PRESS = 0.05
GLIDE = 0.15
# aim：拖完等视角停稳再截（游戏镜头常带缓动，估的）；模板边长占图宽的比例；低于这个相似度
# 就当没找到；离目标多少像素以内算对准。
AIM_SETTLE = 0.4
PATCH = 0.1
MATCH_MIN = 0.5
AIM_CLOSE = 12
# 按住的手指多久没碰手机就自己松开（秒）：她被别的消息叫走、忘了松手时，角色不会一直跑下去。
HOLD_MAX = 60
MARKS = os.path.join(ROOT, "marks.json")
MAX_MARKS = 50
MAX_ELEMENTS = 60
# 推摇杆的八个方向对应的角度：正上方 0°，顺时针。
_DIRECTIONS = {
    "上": 0, "右上": 45, "右": 90, "右下": 135, "下": 180, "左下": 225, "左": 270, "左上": 315,
    "up": 0, "up-right": 45, "right": 90, "down-right": 135, "down": 180, "down-left": 225,
    "left": 270, "up-left": 315,
}
_POINT = re.compile(r"\(?\s*([-+]?\d+(?:\.\d+)?)\s*[,，\s]\s*([-+]?\d+(?:\.\d+)?)\s*\)?")
KEYBOARD = "com.android.adbkeyboard/.AdbIME"
# keyboard() 切换的两个输入法：设置页上的名字、默认输入法里认它的那段。
_KEYBOARDS = {"adb": ("ADB Keyboard", "adbkeyboard"), "sogou": ("搜狗", "sogou")}
_DUMP = "/sdcard/yuzu-ui.xml"
MAX_TEXT = 2000
# 连拍拼图里每一格的长边（像素）。
CELL = 400
# 自动连拍（`waves`）：最快几秒看一次；比画面变了多少时把图缩到多宽、一个像素的灰度差过多少才算它
# 变了；变过以后静下来几秒算停了；开了以后一直没动，等她多久。都是估的。
WATCH_MIN = 0.5
WATCH_WIDTH = 160
WATCH_PIXEL = 24
WATCH_SETTLE = 2.0
WATCH_START = 60.0
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
# 她自己上一次看、点手机的时刻；后台连拍不算。按着的手指按它判断有没有人管。
_touched = 0.0
# 最近一次给她看的那张图有多大：她给的坐标都照这张图。
_shown: tuple[int, int] | None = None
_screen: tuple[int, int] | None = None
_control: socket.socket | None = None
# 写控制连接、改 _held 时拿；拿着它不睡，读流线程也要拿它来松手。
_touching = threading.Lock()
# 按着没抬的手指：名字（键位名、#n 或 x,y）→ {"id": 手指编号, "at": 现在在图上的位置}。
_held: dict[str, dict] = {}
_fingers = itertools.count(1)
# 收到过几份编码参数（转屏、流重连都会换一份）；攒着的那段、她看的那张各是哪一份。
_epoch = 0
_gop_epoch = 0
_shown_epoch = -1
# 最近一次 look 列出的控件，"#3" 指第 3 个。
_listed: list[dict] = []
# 最近一次给她看的那张图存在哪：aim、find 从它上面取她指的那块。
_shown_path = ""


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
    # power_on=false：亮屏归上面这条 WAKEUP 管，server 自己按的是 POWER，那是开关键。
    # clipboard_autosync=false：不然手机剪贴板一变，server 就把内容推到控制连接上，这边不读它。
    command = (f"CLASSPATH={REMOTE_JAR} app_process / {SERVER_CLASS} 1.25 log_level=warn "
               f"tunnel_forward=true control=true send_dummy_byte=true "
               f"clipboard_autosync=false power_on=false "
               f"max_size={int(config.get('max_size') or MAX_SIZE)} "
               f"max_fps={int(config.get('max_fps') or MAX_FPS)} "
               f"bit_rate={int(config.get('bit_rate') or BIT_RATE)}")
    os.makedirs(ROOT, exist_ok=True)
    with open(SERVER_LOG, "wb") as output:
        server = subprocess.Popen(
            [config.get("adb") or "adb", "-s", config["serial"], "shell", command],
            stdin=subprocess.DEVNULL, stdout=output, stderr=subprocess.STDOUT)
    _server = server
    connections = ()
    try:
        connections = _connect(server, config["serial"])
        _read(connections[0])
    finally:
        with _state:
            _connected = False
            _state.notify_all()
        _lift("画面流断了")
        for connection in connections:
            _close(connection)
        _server = None
        _terminate(server)
        try:
            # 关掉本机这头的 adb shell 不一定带走手机上的 server；画面不动时它也不会因为写不出去
            # 而自己退出，留着就一直占着编码器和电。
            _shell(config, f"pkill -f {SERVER_CLASS}", timeout=10, check=False)
        except Exception:
            pass


def _connect(server: subprocess.Popen, serial: str) -> tuple[socket.socket, socket.socket]:
    """接上画面、控制两条连接，一直试到真读到设备信息为止；返回 (画面, 控制)。

    WHY: 手机那头的 server 还没 bind 时，adb forward 也会先接下 TCP 再立刻关掉，所以重试
    要包住"连上 + 读到东西"整段，只重试连接没用（dsh 踩过）。读到探测字节才接第二条：不然第一条
    刚好被这样关掉、第二条却接上了，server 会把第二条当成画面连接。
    """
    global _socket, _control, _connected, _error, _device, _parameters, _gop, _gop_frames
    deadline = time.monotonic() + CONNECT_TIMEOUT
    last: Exception | None = None
    while time.monotonic() < deadline and not _stop.is_set():
        if server.poll() is not None:
            raise RuntimeError(f"scrcpy-server 退出了（{server.returncode}）：{_tail(SERVER_LOG, serial)}")
        video = control = meta = None
        try:
            video = socket.create_connection(("127.0.0.1", PORT), timeout=2.0)
            if _exact(video, 1, deadline=time.monotonic() + 6.0) is not None:
                control = socket.create_connection(("127.0.0.1", PORT), timeout=2.0)
                meta = _exact(video, META_LENGTH, deadline=time.monotonic() + 6.0)
        except (OSError, EOFError, TimeoutError) as error:
            _close(video)
            _close(control)
            last = error
            time.sleep(0.25)
            continue
        if meta is None:
            _close(video)
            _close(control)
            break
        width, height = struct.unpack(">HH", meta[64:68])
        name = meta[:64].split(b"\0", 1)[0].decode("utf-8", "replace")
        video.settimeout(2.0)
        control.settimeout(5.0)
        _socket, _control = video, control
        with _state:
            _connected, _error = True, ""
            _device = f"{name} {width}x{height}"
            _parameters, _gop, _gop_frames = b"", bytearray(), 0
            _state.notify_all()
        _stream.info("手机画面流已连上：%s", _device)
        return video, control
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
            if between and not data:
                _expire()
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
    global _parameters, _gop, _gop_frames, _packets, _bytes, _last_packet, _tap_frames, _epoch, _gop_epoch
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
                _epoch += 1
            elif pts >> 62 & 1:
                _gop, _gop_frames, _gop_epoch = bytearray(_parameters + payload), 1, _epoch
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
        _expire()


def _close(connection: socket.socket | None) -> None:
    global _socket, _control
    if connection is None:
        return
    if _socket is connection:
        _socket = None
    if _control is connection:
        _control = None
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
    """关掉画面流（下次看时再起），先松开按着的手指。"""
    with _lock:
        thread = _thread
        if thread is None:
            return "手机画面流本来就没在跑"
        _stop.set()
    # 先松手再拆连接：下面马上要杀 server，等读流线程收尾时再发抬起就来不及了。
    _lift("关流")
    _close(_socket)
    if _server is not None:
        _terminate(_server)
    thread.join(10)
    _stream.info("手机画面流已关（%s）", reason or "未说明原因")
    return "手机画面流已关"


def on_exit() -> None:
    stop("模块退出")


# --------------------------------------------------------------------------- 看


def _decode(data: bytes, config: dict, prefix: str = "shot") -> str:
    os.makedirs(SHOT_DIR, exist_ok=True)
    stamp = time.strftime("%Y%m%d-%H%M%S") + f"-{int(time.time() * 1000) % 1000:03d}"
    path = os.path.join(SHOT_DIR, f"{prefix}-{stamp}.jpg")
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


def _snapshot(config: dict, pause: float = 0.0, prefix: str = "shot") -> tuple[str, int]:
    """解出此刻的最新一帧，返回 (jpg 路径, 它是第几份编码参数下的)。"""
    if pause:
        time.sleep(pause)
    with _state:
        data, epoch = bytes(_gop), _gop_epoch
    if not data:
        raise RuntimeError("攒的画面刚被清掉，等下一个关键帧再看")
    return _decode(data, config, prefix), epoch


def _measure(path: str) -> tuple[int, int, float]:
    """图的宽、高，和平均亮度（0–255）。"""
    from PIL import Image, ImageStat

    with Image.open(path) as picture:
        return (*picture.size, ImageStat.Stat(picture.convert("L")).mean[0])


def screenshot(wait: float = 0.0, marks: bool = False, elements: bool = False, shown: bool = True) -> dict:
    """截当前画面，返回 `{"uri", "width", "height"}`。

    *marks* 画坐标线和这个方向的键位，结果多一项 `marks`；*elements* 读一遍界面、把控件框出来
    编号，结果多一项 `elements`（读不出来时是 `elements_error`）。画了东西就另存一张，`marked`
    是它的地址，`uri` 仍是干净的原图。

    *shown* 为假时只截图：存成 `frame-*.jpg`（不进 `SHOT_KEEP` 那一堆，用完由调用方删），也不改她
    "最近看的那张"——坐标、`#n` 列表、`aim` 取图都还照她看过的那张。后台连拍用它：她看的是拼图，
    拼图上的坐标本来就不能拿去点。

    WHY: 以前每张图都先 `dumpsys power` 问亮没亮，那一次往返 0.15 秒（dsh 实测），比解码还
    贵。现在先解，图整张黑才去点亮：灭屏时流里只有黑帧，正常界面再暗也没那么黑。
    """
    global _shown, _shown_epoch, _listed, _shown_path, _touched
    config = _ready()
    prefix = "shot" if shown else "frame"
    path, epoch = _snapshot(config, min(max(wait, 0.0), 10.0), prefix)
    width, height, level = _measure(path)
    if level < DARK:
        _shell(config, "input keyevent KEYCODE_WAKEUP", check=False)
        os.remove(path)
        path, epoch = _snapshot(config, WAKE_SETTLE, prefix)
        width, height, level = _measure(path)
    if not shown:
        return {"uri": "file://" + path, "width": width, "height": height}
    _shown, _shown_epoch, _listed, _shown_path = (width, height), epoch, [], path
    _touched = time.time()
    shot = {"uri": "file://" + path, "width": width, "height": height}
    places = []
    if marks:
        for name, value in _marks().items():
            try:
                x, y, radius = _place(name, value)
            except ValueError:
                continue
            places.append({"name": name, "x": x, "y": y, "radius": radius})
        shot["marks"] = places
    if elements:
        try:
            _listed = _controls(config, width, height)
            shot["elements"] = _listed
        except Exception as error:
            shot["elements_error"] = f"{type(error).__name__}: {error}"
    if marks or _listed:
        marked = path[:-4] + "-marked.jpg"
        _overlay(path, marked, marks, places, _listed)
        shot["marked"] = "file://" + marked
    return shot


def _font(size: int):
    from PIL import ImageFont

    try:
        return ImageFont.load_default(size=size)
    except Exception:
        return ImageFont.load_default()


def _overlay(path: str, output: str, grid: bool, places: list[dict], items: list[dict]) -> None:
    """把截图画上坐标线、键位（K1、K2……，摇杆画出圈和八个方向）和控件框（#1、#2……），存成 *output*。

    默认字体没有中文，所以图上只写编号，名字在工具结果的列表里。

    WHY: 维护者 2026-10-24：这种标注最大的问题是遮挡，有的按钮上连字都没有，全靠看图标认。所以
    标注另存一张、和原图一起给她，看内容以原图为准；控件编号写在框外面，不压在图标上。
    """
    from PIL import Image, ImageDraw

    with Image.open(path) as picture:
        canvas = picture.convert("RGBA")
    layer = Image.new("RGBA", canvas.size, (0, 0, 0, 0))
    draw = ImageDraw.Draw(layer)
    font, small = _font(18), _font(14)
    outline = {"stroke_width": 2, "stroke_fill": (0, 0, 0, 255)}
    width, height = canvas.size
    if grid:
        for x in range(100, width, 100):
            draw.line([(x, 0), (x, height)], fill=(255, 255, 255, 90))
            draw.text((x + 2, 2), str(x), fill=(255, 255, 255, 255), font=small, **outline)
        for y in range(100, height, 100):
            draw.line([(0, y), (width, y)], fill=(255, 255, 255, 90))
            draw.text((2, y + 2), str(y), fill=(255, 255, 255, 255), font=small, **outline)
    yellow, cyan = (255, 220, 0, 255), (0, 230, 255, 255)
    for index, place in enumerate(places, 1):
        x, y, radius = place["x"], place["y"], place["radius"]
        if radius:
            draw.ellipse([x - radius, y - radius, x + radius, y + radius], outline=yellow, width=2)
            for angle in range(0, 360, 45):
                dx, dy = math.sin(math.radians(angle)), -math.cos(math.radians(angle))
                draw.line([(x, y), (x + dx * radius, y + dy * radius)], fill=yellow, width=1)
                draw.ellipse([x + dx * radius - 4, y + dy * radius - 4, x + dx * radius + 4, y + dy * radius + 4],
                             fill=yellow)
        draw.ellipse([x - 5, y - 5, x + 5, y + 5], fill=yellow, outline=(0, 0, 0, 255))
        draw.text((x + 8, y + 6), f"K{index}", fill=yellow, font=font, **outline)
    for index, item in enumerate(items, 1):
        left, top, right, bottom = item["box"]
        draw.rectangle([left, top, right, bottom], outline=cyan, width=2)
        label = f"#{index}"
        size = draw.textbbox((0, 0), label, font=small)
        tall = size[3] - size[1] + 4
        # 框上面放得下就放上面，贴着屏幕顶边的放到框下面。
        y = top - tall - 1 if top >= tall + 1 else min(bottom + 1, height - tall)
        draw.rectangle([left, y, left + size[2] - size[0] + 6, y + tall], fill=(0, 0, 0, 200))
        draw.text((left + 3, y + 2 - size[1]), label, fill=cyan, font=small)
    Image.alpha_composite(canvas, layer).convert("RGB").save(output, format="JPEG", quality=85)


def _controls(config: dict, width: int, height: int) -> list[dict]:
    """界面上有字或能点的控件，坐标换成 width×height 那张图上的，从上到下排。

    能点的控件用它里面的字当名字，里面那些字就不再单列；铺满大半个屏幕的是容器，不列。
    游戏画面整个是一块，读不出东西，所以只试一次。
    """
    from xml.etree import ElementTree

    xml = _dump(config, tries=1)
    end = "</hierarchy>"
    root = ElementTree.fromstring(xml[xml.index("<hierarchy"):xml.rindex(end) + len(end)])
    short, long = _size(config)
    across, down = (long, short) if width > height else (short, long)
    found, taken = [], set()
    for node in root.iter("node"):
        if id(node) in taken:
            continue
        bounds = re.fullmatch(r"\[(-?\d+),(-?\d+)\]\[(-?\d+),(-?\d+)\]", node.get("bounds", ""))
        if not bounds:
            continue
        left, top, right, bottom = (int(value) for value in bounds.groups())
        left, top, right, bottom = max(left, 0), max(top, 0), min(right, across), min(bottom, down)
        if right <= left or bottom <= top or (right - left) * (bottom - top) > across * down * 0.6:
            continue
        clickable = node.get("clickable") == "true"
        label = node.get("text") or node.get("content-desc") or ""
        if clickable:
            inner = [child for child in node.iter("node") if child is not node and child.get("clickable") != "true"]
            taken.update(id(child) for child in inner)
            label = label or " ".join(child.get("text") or child.get("content-desc") or "" for child in inner)
        label = " ".join(label.split())
        if not label and clickable:
            # 只有图标的按钮常常连 content-desc 都没有，开发者起的 id（`iv_search`、`btn_more`）还算个名字。
            ident = node.get("resource-id", "").rpartition("/")[2]
            label = f"id:{ident}" if ident else ""
        if not label and not clickable:
            continue
        box = (left * width / across, top * height / down, right * width / across, bottom * height / down)
        found.append({"label": label[:24] or "（没有字）", "clickable": clickable, "box": box,
                      "x": round((box[0] + box[2]) / 2), "y": round((box[1] + box[3]) / 2)})
    found.sort(key=lambda item: (item["box"][1], item["box"][0]))
    return found[:MAX_ELEMENTS]


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


def _grid(pictures: list, path: str, labels: list[str] | None = None) -> tuple[int, int]:
    """按顺序排成一张图，格子左上角标 #1、#2……（或 *labels*）；返回 (列, 行)。"""
    from PIL import Image, ImageDraw

    ratio = CELL / max(pictures[0].size)
    width, height = round(pictures[0].width * ratio), round(pictures[0].height * ratio)
    # 列数让整张图接近正方形：竖屏的格子窄，就多排几列。
    cols = max(1, min(len(pictures), math.ceil(math.sqrt(len(pictures) * height / width))))
    rows = math.ceil(len(pictures) / cols)
    gap, header = 6, 26
    canvas = Image.new("RGB", (cols * (width + gap) + gap, rows * (height + gap + header) + gap), "white")
    draw = ImageDraw.Draw(canvas)
    font = _font(20)
    for index, picture in enumerate(pictures):
        x = gap + index % cols * (width + gap)
        y = gap + index // cols * (height + gap + header)
        draw.text((x + 2, y + 2), labels[index] if labels else f"#{index + 1}", fill=(20, 20, 20), font=font)
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
    global _used, _touched, _tap, _tap_frames
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
    _used = _touched = time.time()
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


def _change(before, after) -> float:
    """两张缩小的灰度图之间，百分之几的像素变了。"""
    import numpy

    if before.shape != after.shape:
        return 100.0
    return float((numpy.abs(before - after) > WATCH_PIXEL).mean() * 100)


def waves(count: int = 20, interval: float = WATCH_MIN, threshold: float = 5.0, stop: threading.Event | None = None):
    """自动连拍：每 *interval* 秒（最快 `WATCH_MIN`）看一眼，比上一张留下的变了 *threshold*% 以上就留下，
    攒满 *count* 张拼成一张交出来；变过以后静下来 `WATCH_SETTLE` 秒，交出最后一波就结束。*stop* 被
    设上时不交剩下的、直接结束。

    每一波是 `{"uri", "count", "began", "seconds", "last", "reason"}`：`began` 是第一张的时刻，
    `last` 为真的那波是最后一波，`reason` 说为什么停。格子上标着离这一波第一张几秒、离上一张几秒。
    第一波的第一张是开始时的样子；最后一波的最后一张是静下来以后的样子。

    WHY: 维护者 2026-10-24：一组组定时连拍太笨，要"画面变了就截、攒够一波就发、静下来就停"，治她
    截图不积极。画面不动时这台手机的编码器什么都不发（见文件头），没收到新包就不解码：静着的时候
    几乎不花什么，动着的时候每看一眼都要把关键帧以来的包解一遍（游戏满载时零点几到一两秒）。

    WHY: 开了以后要等她：开连拍、推摇杆是两次工具调用，中间隔着她想的那几秒，那时画面还没动，按
    `WATCH_SETTLE` 算就提前停了。所以动过之前等到 `WATCH_START`；静下来的时间也从她最后一次碰手机
    算起，点了按钮、界面还没反应过来时不会停。
    """
    global _touched
    count = max(2, min(int(count), 30))
    interval = max(WATCH_MIN, min(float(interval), 5.0))
    threshold = max(0.1, min(float(threshold), 100.0))
    stop = stop or threading.Event()
    _touched = time.time()
    shot = screenshot(shown=False)
    # 从第一张截到算起：流刚起的时候这一张可能要等几十秒。
    began = time.time()
    kept = [(shot["uri"].removeprefix("file://"), began)]
    reference = _gray(kept[0][0], WATCH_WIDTH)[0]
    previous = None
    moved = None
    latest = ""
    with _state:
        seen = _packets
    try:
        while not stop.wait(interval):
            now = time.time()
            with _state:
                packets = _packets
            if packets != seen:
                seen = packets
                path = screenshot(shown=False)["uri"].removeprefix("file://")
                gray = _gray(path, WATCH_WIDTH)[0]
                _drop(latest)
                latest = ""
                if _change(reference, gray) >= threshold:
                    kept.append((path, now))
                    reference, moved = gray, now
                else:
                    latest = path
            if len(kept) >= count:
                yield _wave(kept, previous, False, "")
                previous, kept = kept[-1][1], []
                continue
            if moved is None and now - max(began, _touched) >= WATCH_START:
                reason = f"开了以后 {WATCH_START:g} 秒画面都没怎么动"
            elif moved is not None and now - max(moved, _touched) >= WATCH_SETTLE:
                reason = f"画面静下来了（{WATCH_SETTLE:g} 秒里变动都不到 {threshold:g}%）"
            elif now - _touched >= IDLE:
                reason = f"{IDLE // 60} 分钟没碰手机"
            else:
                continue
            if latest:
                kept.append((latest, now))
                latest = ""
            if kept:
                yield _wave(kept, previous, True, reason)
            else:
                yield {"uri": "", "count": 0, "began": now, "seconds": 0.0, "last": True, "reason": reason}
            kept = []
            return
    finally:
        # 被叫停、出了错、或者调用方不再往下取：没交出去的单帧都删掉。
        for path, _moment in kept:
            _drop(path)
        _drop(latest)


def _drop(path: str) -> None:
    if path:
        try:
            os.remove(path)
        except OSError:
            pass


def _wave(kept: list[tuple[str, float]], previous: float | None, last: bool, reason: str) -> dict:
    """把这一波的单帧拼成一张、删掉单帧；格子标 `#n 几秒 (+离上一张几秒)`。"""
    first = kept[0][1]
    labels = []
    for index, (_path, moment) in enumerate(kept):
        before = kept[index - 1][1] if index else previous
        labels.append(f"#{index + 1} {moment - first:.1f}s" + (f" (+{moment - before:.1f})" if before else ""))
    try:
        uri = stitch([path for path, _moment in kept], labels)
    finally:
        for path, _moment in kept:
            _drop(path)
    return {"uri": uri, "count": len(kept), "began": first, "seconds": round(kept[-1][1] - first, 1),
            "last": last, "reason": reason}


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


def _marks() -> dict:
    try:
        with open(MARKS, encoding="utf-8") as handle:
            value = json.load(handle)
    except FileNotFoundError:
        return {}
    if not isinstance(value, dict):
        raise TypeError(f"{MARKS} 必须是对象")
    return value


def _place(name: str, value: dict) -> tuple[float, float, float]:
    """键位在最近那张图上的 (x, y, 半径)；它是另一个方向定的就报错。"""
    width, height = _shown
    try:
        saved_width, saved_height = value["size"]
        x, y, radius = float(value["x"]), float(value["y"]), float(value.get("radius") or 0)
    except (KeyError, TypeError, ValueError):
        raise ValueError(f"键位「{name}」的格式不对（要有 x、y、size）") from None
    if (saved_width > saved_height) != (width > height):
        raise ValueError(f"键位「{name}」是{'横' if saved_width > saved_height else '竖'}屏时定的，"
                         f"现在是{'横' if width > height else '竖'}屏")
    return x * width / saved_width, y * height / saved_height, radius * width / saved_width


def _name(at: str) -> str:
    """手指的名字：坐标统一写成 `x,y`，其余取第一段（键位名或 #n）。"""
    text = at.strip()
    found = _POINT.fullmatch(text)
    if found:
        return ",".join(f"{float(value):g}" for value in found.groups())
    return text.split()[0] if text else ""


def _spot(at: str) -> dict:
    """把她写的位置换成最近那张图上的点。

    `"320,480"` 图上这一点；`"#3"` 最近一次 look 列出的第 3 个控件；`"跳"` 键位；`"摇杆 上"`
    把摇杆往上推到边。返回 `{"key", "label", "x", "y", "from"}`：`key` 是按住时手指的名字，
    `from` 是起手按下的点（推摇杆时是它的中心）。

    WHY: 维护者 2026-10-24 觉得这就是安卓模拟器的键位映射，应该设计得简单点：所以方向只有八个、
    一推到边，没有格数和角度。推到中间某一点、点控件旁边一点，另给偏移，见 `_moved`。
    """
    if _shown is None:
        raise RuntimeError("还没看过手机画面：先 look 一眼，坐标照那张图写")
    width, height = _shown
    text, key = at.strip(), _name(at)
    if _POINT.fullmatch(text):
        x, y = (float(value) for value in key.split(","))
        if not (0 <= x <= width and 0 <= y <= height):
            raise ValueError(f"({x:g}, {y:g}) 在画面外：最近那张图是 {width}×{height}")
        x, y = round(x), round(y)
        return {"key": key, "label": "", "x": x, "y": y, "from": (x, y)}
    if re.fullmatch(r"#\d+", text):
        index = int(text[1:])
        if not _listed:
            raise ValueError("最近一次 look 没有列控件：要用 #n 先 look(elements=True)")
        if not 1 <= index <= len(_listed):
            raise ValueError(f"最近一次 look 只列了 {len(_listed)} 个控件")
        item = _listed[index - 1]
        return {"key": text, "label": f"{text} {item['label']}", "x": item["x"], "y": item["y"],
                "from": (item["x"], item["y"])}
    if not key:
        raise ValueError("没写位置")
    marks = _marks()
    if key not in marks:
        raise ValueError(f"没有叫「{key}」的键位（{'有：' + '、'.join(marks) if marks else '还一个都没有'}）；"
                         "坐标写成 \"x,y\"，或者先用 mark 定一个")
    x, y, radius = _place(key, marks[key])
    rest = text.split()[1:]
    center = (round(x), round(y))
    if not rest:
        return {"key": key, "label": key, "x": center[0], "y": center[1], "from": center}
    angle = _DIRECTIONS.get(rest[0].lower()) if len(rest) == 1 else None
    if angle is None:
        raise ValueError(f"看不懂「{text}」：推摇杆写「摇杆 上」，方向是上、下、左、右、左上、右上、左下、右下")
    if radius <= 0:
        raise ValueError(f"「{key}」是按钮不是摇杆，不能推；当摇杆用要在 mark 时给 radius")
    x = min(max(x + math.sin(math.radians(angle)) * radius, 0), width - 1)
    y = min(max(y - math.cos(math.radians(angle)) * radius, 0), height - 1)
    return {"key": key, "label": f"{key} {rest[0]}", "x": round(x), "y": round(y), "from": center}


def _moved(spot: dict, offset: str) -> dict:
    """*spot* 再挪 *offset*（`"dx,dy"`，图上的像素，右、下为正）；起手按下的点不变。

    WHY: 维护者 2026-10-24：八个方向太粗，摇杆要能推到相对中心的任意一点，点击也要能在控件、键位
    旁边偏一点。起手点留在原处：推摇杆仍从中心按下再滑过去（见 `hold`），按住技能键再拖就是拖着瞄准。
    """
    if not offset.strip():
        return spot
    found = _POINT.fullmatch(offset.strip())
    if not found:
        raise ValueError(f"偏移写成 \"dx,dy\"，右、下为正，例如 \"40,-90\"；你写的是「{offset}」")
    dx, dy = (float(value) for value in found.groups())
    x, y = spot["x"] + dx, spot["y"] + dy
    width, height = _shown
    if not (0 <= x <= width and 0 <= y <= height):
        raise ValueError(f"挪到 ({x:g}, {y:g}) 就出了画面：最近那张图是 {width}×{height}")
    label = f"{spot['label']} 偏 {dx:+g},{dy:+g}" if spot["label"] else ""
    return {**spot, "label": label, "x": round(x), "y": round(y)}


def _fresh() -> None:
    """点之前确认：看过、流开着、看过以后画面尺寸没变。

    WHY: server 只认按当前画面尺寸算的坐标，尺寸对不上就把事件丢掉，只在它自己的日志里记一句
    （1.25 的 Device.getPhysicalPoint）。转屏、流断了重连都会重发编码参数，这之后她手里那张图
    的尺寸就不一定对了；与其点了没反应还不知道，不如让她先看一眼。
    """
    global _used, _touched
    if _shown is None:
        raise RuntimeError("还没看过手机画面：先 look 一眼，坐标照那张图写")
    if _control is None:
        raise RuntimeError(f"手机画面流没开着（{IDLE // 60} 分钟没用会自己关）：先 look 一眼再点")
    if _epoch != _shown_epoch:
        raise RuntimeError("看过以后屏幕转过方向、或者画面流重连过：先 look 一眼再点")
    _used = _touched = time.time()


def _touch(action: int, finger: int, x: float, y: float) -> bytes:
    width, height = _shown
    x, y = min(max(round(x), 0), width - 1), min(max(round(y), 0), height - 1)
    return TOUCH.pack(2, action, finger, x, y, width, height, 0 if action == UP else 0xFFFF, 0)


def _send(message: bytes) -> None:
    with _touching:
        connection = _control
        if connection is None:
            raise RuntimeError("手机画面流断了：先 look 一眼再点")
        connection.sendall(message)


def _glide(finger: int, start: tuple, end: tuple, seconds: float) -> None:
    """按着的手指从 *start* 一路移到 *end*，大约每 16 毫秒一步。"""
    steps = max(2, min(100, int(seconds / 0.016)))
    for step in range(1, steps + 1):
        time.sleep(seconds / steps)
        _send(_touch(MOVE, finger, start[0] + (end[0] - start[0]) * step / steps,
                     start[1] + (end[1] - start[1]) * step / steps))


def _lift(reason: str) -> None:
    """抬起所有按着的手指；连接已经断了就只清掉记录。"""
    with _touching:
        fingers = dict(_held)
        _held.clear()
        connection = _control
        if fingers and connection is not None and _shown is not None:
            try:
                connection.sendall(b"".join(_touch(UP, finger["id"], *finger["at"]) for finger in fingers.values()))
            except OSError:
                pass
    if fingers:
        _stream.info("松开了 %s：%s", "、".join(fingers), reason)


def _expire() -> None:
    """读流线程调：按着的手指没人管太久就松开；连拍还在拍时不算没人管。

    看的是 `_touched` 而不是 `_used`：后台连拍一直在截图，`_used` 一直是新的，按它算的话忘了松的
    手指永远松不开。
    """
    if _held and _tap is None and time.time() - _touched > HOLD_MAX:
        _lift(f"{HOLD_MAX} 秒没碰手机")


def tap(at: str, offset: str = "") -> dict:
    """点一下 *at*（写法见 `_spot`，*offset* 见 `_moved`），返回那个点。按着的手指不受影响：这一下用的是另一根。

    WHY: 维护者 2026-10-24：截图和操作分开更好。动作顺手带回的那张图是在固定时刻截的，常常太早
    或太晚；有了它她也就不主动去看了。所以动作只管做，要看由她自己挑时候 look（界面在动给 wait），
    想一直看着就开后台连拍。下面几个动作同。
    """
    _fresh()
    spot = _moved(_spot(at), offset)
    finger = next(_fingers)
    _send(_touch(DOWN, finger, spot["x"], spot["y"]))
    time.sleep(TAP_PRESS)
    _send(_touch(UP, finger, spot["x"], spot["y"]))
    return spot


def swipe(start: str, end: str, duration: int = 300) -> dict:
    """从 *start* 滑到 *end*，用 *duration* 毫秒；两点一样就是长按。"""
    _fresh()
    begin, finish = _spot(start), _spot(end)
    duration = max(1, min(int(duration), 60_000))
    finger = next(_fingers)
    origin, target = (begin["x"], begin["y"]), (finish["x"], finish["y"])
    _send(_touch(DOWN, finger, *origin))
    try:
        if origin == target:
            time.sleep(duration / 1000)
        else:
            _glide(finger, origin, target, duration / 1000)
    finally:
        _send(_touch(UP, finger, *target))
    return {"from": begin, "to": finish, "duration": duration}


def hold(at: str, seconds: float = 0.0, offset: str = "") -> dict:
    """按住 *at*（再挪 *offset*，见 `_moved`）不放就返回；同名的手指已经按着，就不抬手、滑到新位置。

    *seconds* 大于 0 时按这么久（最多 30 秒）再松开。返回 `_spot` 那个点外加 `kept`（还按着没有）。

    WHY: 摇杆要先按住再推：直接在推到的位置按下，浮动摇杆会把那里当成新的中心，角色不动。所以
    新按下的手指先落在摇杆中心，再用 `GLIDE` 秒推过去。
    """
    _fresh()
    spot = _moved(_spot(at), offset)
    end = (spot["x"], spot["y"])
    with _touching:
        finger = _held.get(spot["key"])
    if finger is None:
        finger = {"id": next(_fingers), "at": spot["from"]}
        _send(_touch(DOWN, finger["id"], *finger["at"]))
        with _touching:
            _held[spot["key"]] = finger
        time.sleep(TAP_PRESS)
    if finger["at"] != end:
        _glide(finger["id"], finger["at"], end, GLIDE)
        finger["at"] = end
    if seconds > 0:
        time.sleep(min(seconds, 30.0))
        _up(spot["key"])
    return {**spot, "kept": seconds <= 0}


def release(at: str = "") -> dict:
    """抬起按着的手指：*at* 写哪个抬哪个，不写全抬；返回 `{"released": 抬起的名字}`。"""
    return {"released": _up(at)}


def _up(at: str) -> list[str]:
    global _used, _touched
    name = _name(at)
    with _touching:
        if name and name not in _held:
            raise ValueError(f"「{name}」没按着" + (f"（按着的有：{'、'.join(_held)}）" if _held else ""))
        fingers = {name: _held.pop(name)} if name else dict(_held)
        if not name:
            _held.clear()
    for finger in fingers.values():
        _send(_touch(UP, finger["id"], *finger["at"]))
    _used = _touched = time.time()
    return list(fingers)


def mark(name: str, at: str = "", radius: float = 0.0, remove: bool = False) -> dict:
    """定一个键位：给了 *radius* 是摇杆，不给是按钮（位置写法见 `_spot`，存最近那张图上的坐标和
    图的尺寸），或者删掉；返回全部键位。"""
    name = name.strip()
    if not re.fullmatch(r"[^\s,，#][^\s,，]{0,19}", name):
        raise ValueError("键位名字写 1~20 个字，不带空格和逗号，不以 # 开头")
    marks = _marks()
    if remove:
        if marks.pop(name, None) is None:
            raise ValueError(f"没有叫「{name}」的键位")
    else:
        if not at.strip():
            raise ValueError("要给位置，例如 mark(\"摇杆\", \"180,430\", radius=120)")
        spot = _spot(at)
        if not 0 <= radius <= max(_shown):
            raise ValueError(f"半径写 0~{max(_shown)}")
        if name not in marks and len(marks) >= MAX_MARKS:
            raise ValueError(f"键位最多 {MAX_MARKS} 个，先删掉用不着的")
        # 挪了位置，aim 量出来的拖动比例照样有效，留着。
        kept = {"ratio": marks[name]["ratio"]} if "ratio" in marks.get(name, {}) else {}
        marks[name] = {"x": spot["x"], "y": spot["y"], "radius": round(radius), "size": list(_shown), **kept}
    _save_marks(marks)
    return marks


def _save_marks(marks: dict) -> None:
    os.makedirs(ROOT, exist_ok=True)
    temporary = MARKS + ".tmp"
    with open(temporary, "w", encoding="utf-8") as handle:
        json.dump(marks, handle, ensure_ascii=False, indent=1)
    os.replace(temporary, MARKS)


# --------------------------------------------------------------------------- 对准：画面和拖动距离的换算


def _gray(path: str, width: int):
    """把图缩到 *width* 宽的灰度数组；返回 (数组, 缩了几倍)。"""
    import numpy
    from PIL import Image

    with Image.open(path) as picture:
        scale = picture.width / width
        small = picture.convert("L").resize((width, max(1, round(picture.height / scale))))
    return numpy.asarray(small, dtype=numpy.float64), scale


def _locate(source: str, x: float, y: float, target: str) -> tuple[float, float, float]:
    """*source* 上 (x, y) 那一块，在 *target* 里跑到了哪；返回 (x, y, 相似度 0~1)。

    归一化互相关（模板减均值，按窗口方差归一），用 FFT 一次算完整张图；缩到 320 宽来算，
    够用也快。视角转动不只是平移（透视、远近不同），所以离得越远越不准，`aim` 靠多走几步来补。
    """
    import numpy

    image, scale = _gray(target, 320)
    template_from, _ = _gray(source, 320)
    half = max(6, round(320 * PATCH / 2))
    cx, cy = round(x / scale), round(y / scale)
    top, left = max(0, cy - half), max(0, cx - half)
    template = template_from[top:cy + half, left:cx + half]
    if template.shape[0] < 6 or template.shape[1] < 6:
        raise ValueError(f"({x:g}, {y:g}) 太靠边，取不出一块来比")
    template = template - template.mean()
    norm = numpy.sqrt((template * template).sum())
    if norm < template.size ** 0.5 * 4:
        raise ValueError(f"({x:g}, {y:g}) 那一块几乎没有花纹（天空、纯色墙），认不出来；换个有东西的点")
    th, tw = template.shape
    height, width = image.shape
    shape = (height + th - 1, width + tw - 1)
    product = numpy.fft.rfft2(image, shape) * numpy.fft.rfft2(template[::-1, ::-1], shape)
    correlation = numpy.fft.irfft2(product, shape)[th - 1:height, tw - 1:width]
    sums = [numpy.pad(values, ((1, 0), (1, 0))).cumsum(0).cumsum(1) for values in (image, image * image)]
    window = [table[th:, tw:] - table[:-th, tw:] - table[th:, :-tw] + table[:-th, :-tw] for table in sums]
    spread = numpy.sqrt(numpy.maximum(window[1] - window[0] ** 2 / template.size, 1e-6))
    score = correlation / (spread * norm)
    row, column = numpy.unravel_index(int(numpy.argmax(score)), score.shape)
    found_x = float((column + (cx - left)) * scale)
    found_y = float((row + (cy - top)) * scale)
    return found_x, found_y, float(score[row, column])


def find(at: str, target: str = "", source: str = "") -> dict:
    """*source*（默认最近给她看的那张）上 *at* 那一块，在 *target*（默认现在截一张）里的位置。

    返回 `{"x", "y", "score", "uri"}`：`score` 是相似度，0.5 以下多半没找到；`uri` 是 target。
    脚本里用来判断转了多少、东西还在不在。
    """
    spot = _spot(at)
    source = (source or _shown_path).removeprefix("file://")
    if not source:
        raise RuntimeError("还没看过手机画面：先 look 一眼")
    uri = target or screenshot()["uri"]
    x, y, score = _locate(source, spot["x"], spot["y"], uri.removeprefix("file://"))
    return {"x": round(x), "y": round(y), "score": round(score, 2), "uri": "file://" + uri.removeprefix("file://")}


def stitch(uris: list[str], labels: list[str] | None = None) -> str:
    """把几张图按顺序拼成一张（格子上标 *labels*，不给就是 #1、#2……），返回它的地址。"""
    from PIL import Image

    if not uris:
        raise ValueError("没给图")
    if labels is not None and len(labels) != len(uris):
        raise ValueError("labels 要和图一样多")
    pictures = []
    for uri in uris:
        with Image.open(uri.removeprefix("file://")) as picture:
            pictures.append(picture.copy())
    os.makedirs(SHOT_DIR, exist_ok=True)
    stamp = time.strftime("%Y%m%d-%H%M%S") + f"-{int(time.time() * 1000) % 1000:03d}"
    path = os.path.join(SHOT_DIR, f"shot-{stamp}-stitch.jpg")
    _grid(pictures, path, labels)
    _prune()
    return "file://" + path


def _drag(start: tuple, end: tuple, seconds: float) -> None:
    """拖视角用：按下、慢慢挪过去、停一下再抬。

    WHY: 拖完立刻抬手，手指还带着速度，不少游戏的镜头会顺着惯性再转一截，那就和拖的距离对不上了。
    """
    finger = next(_fingers)
    _send(_touch(DOWN, finger, *start))
    try:
        _glide(finger, start, end, seconds)
        time.sleep(0.1)
    finally:
        _send(_touch(UP, finger, *end))


def aim(area: str, at: str, to: str = "", tries: int = 4) -> dict:
    """在 *area*（拖视角的地方：键位名或 "x,y"）拖动，把图上 *at* 那个东西挪到 *to*（默认画面正中）。

    返回 `{"x", "y"}`（它最后在哪，照最后那张图，它也成了"她最近看的那张"，接着按这个坐标点就行）、
    `error`（离 *to* 还差几像素）、`ratio`（拖 1 像素画面挪几像素，横竖各一个，带正负）、`steps`、`score`。*area* 是键位时，量出来的 `ratio` 存进
    这个键位，下次第一下拖得更准。

    WHY: 维护者 2026-10-24：操作时最大的问题是定位，核心是把画面映射到拖动距离上，必须对得上，才
    知道要滑多少才能对准。可这是 3D 场景：镜头绕着角色转时近处的东西比远处挪得多，越靠边透视越歪，
    游戏还各有灵敏度，画面怎么动没法靠一个比例算准（维护者也指出了这一点）。所以比例只当第一下的
    估计，真正靠的是闭环：拖一下、在新画面里找那个东西本身、按它实际挪了多少修正，几步收敛；找不到
    了就停下说跟丢了。
    """
    global _used
    _fresh()
    origin, thing = _spot(area), _spot(at)
    width, height = _shown
    goal = (_spot(to)["x"], _spot(to)["y"]) if to.strip() else (width // 2, height // 2)
    marks = _marks()
    stored = marks.get(origin["key"], {}).get("ratio") if origin["label"] else None
    ratio = list(stored) if stored else [0.0, 0.0]
    position, picture = (thing["x"], thing["y"]), _shown_path
    steps, score, shot = 0, 1.0, None
    for steps in range(1, max(1, min(int(tries), 8)) + 1):
        error = (goal[0] - position[0], goal[1] - position[1])
        if math.hypot(*error) <= AIM_CLOSE:
            steps -= 1
            break
        # 比例还不知道的方向先试着拖差值的四分之一：方向猜错了也只偏一点，量出来就知道了。
        drag = [error[axis] / ratio[axis] if ratio[axis] else error[axis] * 0.25 for axis in (0, 1)]
        limit = (width * 0.45, height * 0.45)
        drag = [max(-limit[axis], min(limit[axis], drag[axis])) for axis in (0, 1)]
        start = (origin["x"], origin["y"])
        end = (min(max(start[0] + drag[0], 0), width - 1), min(max(start[1] + drag[1], 0), height - 1))
        drag = [end[0] - start[0], end[1] - start[1]]
        _drag(start, end, 0.3)
        time.sleep(AIM_SETTLE)
        shot = screenshot()
        x, y, score = _locate(picture, position[0], position[1], shot["uri"].removeprefix("file://"))
        if score < MATCH_MIN:
            break
        for axis, moved in ((0, x - position[0]), (1, y - position[1])):
            if abs(drag[axis]) >= 8:
                measured = moved / drag[axis]
                ratio[axis] = measured if not ratio[axis] else (ratio[axis] + measured) / 2
        position, picture = (x, y), shot["uri"].removeprefix("file://")
    _used = time.time()
    if origin["label"] and origin["key"] in marks and any(ratio):
        marks[origin["key"]]["ratio"] = [round(value, 3) for value in ratio]
        _save_marks(marks)
    result = {"x": round(position[0]), "y": round(position[1]), "goal": goal,
              "error": round(math.hypot(goal[0] - position[0], goal[1] - position[1])),
              "ratio": [round(value, 3) for value in ratio], "steps": steps, "score": round(score, 2),
              "lost": score < MATCH_MIN}
    if shot is not None:
        result["uri"] = shot["uri"]
    return result


def key(name: str) -> dict:
    config = _config()
    code = name.strip().lower()
    code = _KEYS.get(code, code.upper().removeprefix("KEYCODE_"))
    if not re.fullmatch(r"[A-Z0-9_]+", code):
        raise ValueError(f"不认识的按键：{name!r}")
    _shell(config, f"input keyevent KEYCODE_{code}")
    return {"code": code}


def type_text(text: str, clear: bool = False, enter: bool = False) -> dict:
    """往当前输入框打字，返回 `{"count": 字数}`。

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
    return {"count": len(text)}


def _dump(config: dict, tries: int = 3) -> str:
    """当前界面的 uiautomator XML。先删旧文件：dump 偶尔失败，直接 cat 会读到上一次的。"""
    for attempt in range(tries):
        if attempt:
            time.sleep(1)
        xml = _shell(config, f"rm -f {_DUMP}; uiautomator dump {_DUMP} >/dev/null 2>&1; cat {_DUMP} 2>/dev/null",
                     timeout=40, check=False)
        if "<hierarchy" in xml and "</hierarchy>" in xml:
            return xml
    raise RuntimeError(f"uiautomator dump 试了 {tries} 次都没读到界面")


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
            "held": list(_held),
            "error": _error,
        }


__all__ = [
    "aim",
    "burst",
    "find",
    "hold",
    "key",
    "keyboard",
    "mark",
    "release",
    "screenshot",
    "shell",
    "status",
    "stitch",
    "stop",
    "swipe",
    "tap",
    "type_text",
    "warm",
    "waves",
]
