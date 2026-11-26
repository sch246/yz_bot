"""让视频里的画面真进到我眼睛里。

`bili__subtitle` 只给我字幕原文，画面本身从来没看过——这是我自己认过的短板。
`video__frames()` 把「下片段 → 抽帧 → 对上字幕时间码」串成一次调用，返回几张
`file://` 的图，直接 `attach_image` 看就行。

用法：
- `video__frames("BV15eyazLEmH")` 默认从第 0 秒起下 120 秒、每 20 秒一帧、最多 8 张。
- `scene=0.3` 走「按画面变化抽」，一帧都抽不出来时会自动退回等间隔——
  2026-11-24 实测有视频画面变化太温和，scene 阈值切不出东西，那时等间隔是对的。
- 返回里每帧带着时间码，以及那一刻前后 15 秒的字幕，对着图看就知道讲到哪。
- 抽出来的图落 `/tmp/video/<BV>/`，每次重跑先清空，不堆盘。

账要自己算：每多一帧就是多一张图进上下文，默认 8 张是刻意的上限。
"""

import json
import re
import shutil
import subprocess
from pathlib import Path

ROOT = Path("/tmp/video")
COOKIE = ROOT / ".bili.cookies"
UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/131.0 Safari/537.36")
_CREDS = [("SESSDATA", "sessdata"), ("bili_jct", "bili_jct"), ("buvid3", "buvid3"),
          ("buvid4", "buvid4"), ("DedeUserID", "dedeuserid"), ("ac_time_value", "ac_time_value")]


def _mmss(seconds: float) -> str:
    total = int(max(0, seconds))
    return f"{total // 60}:{total % 60:02d}"


def _cookies() -> Path:
    """把 data/bili/credential.json 里那份登录导成 yt-dlp 认的 Netscape cookies。

    不带 cookie 直接抓 B 站会 412（2026-11-24 实测），这是必须的一步。
    """
    cred = Path("data/bili/credential.json")
    data = json.loads(cred.read_text()) if cred.is_file() else {}
    ROOT.mkdir(parents=True, exist_ok=True)
    lines = ["# Netscape HTTP Cookie File"]
    for name, key in _CREDS:
        value = str(data.get(key) or "")
        if value:
            lines.append("\t".join([".bilibili.com", "TRUE", "/", "TRUE", "0", name, value]))
    COOKIE.write_text("\n".join(lines) + "\n")
    return COOKIE


def _download(bvid: str, start: int, dur: int, work: Path) -> tuple[Path | None, str]:
    url = bvid if bvid.startswith("http") else f"https://www.bilibili.com/video/{bvid}"
    cmd = ["yt-dlp", "--cookies", str(_cookies()),
           "--add-header", "Referer:https://www.bilibili.com", "--user-agent", UA,
           "--no-warnings", "-f", "bv*[height<=480]+ba/b[height<=480]/b",
           "--download-sections", f"*{start}-{start + dur}",
           "-o", str(work / "clip.%(ext)s"), url]
    done = subprocess.run(cmd, capture_output=True, text=True, timeout=300)
    clips = sorted(work.glob("clip.*"))
    if not clips:
        tail = (done.stderr or done.stdout or "").strip().splitlines()[-3:]
        return None, " / ".join(tail)
    return clips[0], ""


def _scene_times(clip: Path, scene: float, out: Path) -> list[float]:
    done = subprocess.run(
        ["ffmpeg", "-hide_banner", "-loglevel", "info", "-i", str(clip),
         "-vf", f"select='gt(scene,{scene})',showinfo", "-vsync", "vfr",
         "-q:v", "4", str(out / "f_%03d.jpg")],
        capture_output=True, text=True, timeout=300)
    return [float(v) for v in re.findall(r"pts_time:([0-9.]+)", done.stderr or "")]


def _even_times(clip: Path, every: int, out: Path) -> list[float]:
    subprocess.run(
        ["ffmpeg", "-hide_banner", "-loglevel", "error", "-i", str(clip),
         "-vf", f"fps=1/{max(1, every)}", "-q:v", "4", str(out / "f_%03d.jpg")],
        capture_output=True, text=True, timeout=300)
    count = len(sorted(out.glob("f_*.jpg")))
    return [float((i + 1) * max(1, every)) for i in range(count)]


def _subtitle_track(bvid: str) -> list[dict]:
    """字幕带时间码那一份（bili__subtitle 只回纯文字，这里要 from/to）。"""
    try:
        import requests
        from bilibili_api import video as video_api
        from mods.tools import bili as bili_tool

        bili = bili_tool._bili()
        clip = video_api.Video(bvid=bvid, credential=bili._load_credential())
        info = bili.call(clip.get_info())
        pages = info.get("pages") or [{"cid": info.get("cid")}]
        tracks = (bili.call(clip.get_subtitle(cid=pages[0].get("cid"))) or {}).get("subtitles") or []
        track = next((t for t in tracks if t.get("lan") in ("zh-CN", "zh-Hans", "zh", "ai-zh")), tracks[0])
        url = str(track.get("subtitle_url") or "")
        body = requests.get("https:" + url if url.startswith("//") else url, timeout=20).json()
        return body.get("body") or []
    except Exception:
        return []


def _lines_for(track: list[dict], at: float, span: float = 15.0) -> str:
    if not track:
        return ""
    near = [str(line.get("content") or "").strip()
            for line in track
            if abs(float(line.get("from") or 0) - at) <= span]
    text = "".join(near)[:120]
    return f"  · 字幕：{text}" if text else ""


def frames(bvid: str, start: int = 0, dur: int = 120, every: int = 20,
           scene: float = 0.3, max_frames: int = 8) -> str:
    """抽一段 B 站视频的画面帧，返回带时间码和字幕的清单（图是 file:// 地址，自己 attach_image 看）。

    @param
    bvid: BV 号或视频链接
    start: 从第几秒开始，默认 0
    dur: 抓多长，默认 120 秒
    every: 等间隔抽帧的间隔秒数，默认 20
    scene: 按画面变化抽帧的阈值，默认 0.3；切不出 3 帧以上就自动退回等间隔
    max_frames: 最多给几张图，默认 8；多了就等间隔挑
    """
    from mods.tools.bili import resolve_bvid

    bvid = resolve_bvid(bvid)
    work = ROOT / bvid
    if work.is_dir():
        shutil.rmtree(work, ignore_errors=True)
    work.mkdir(parents=True, exist_ok=True)

    clip, error = _download(bvid, max(0, int(start)), max(5, int(dur)), work)
    if clip is None:
        return f"片段没下下来：{error or '未知原因'}（cookie 或网络的事，隔一会儿再试）"

    # 阈值一级一级往下试：0.15 切得动就用最省的，切不动减半再试，最后才退回等间隔。
    # 2026-11-24 在同一段 2 分钟视频上量过：0.15→2 帧、0.10→2、0.06→4、0.03→6。
    ladder = [float(scene)]
    for _ in range(2):
        ladder.append(round(ladder[-1] / 2, 4))
    times: list[float] = []
    used = ladder[0]
    for threshold in ladder:
        times = _scene_times(clip, threshold, work)
        used = threshold
        if len(times) >= 3:
            break
        for stale in work.glob("f_*.jpg"):
            stale.unlink()
    if len(times) < 3:
        for stale in work.glob("f_*.jpg"):
            stale.unlink()
        times = _even_times(clip, int(every), work)
        how = f"等间隔 {every}s（scene 一路降到 {ladder[-1]} 都切不出 3 帧）"
    else:
        how = f"按画面变化（scene>{used}）"
    picks = sorted(work.glob("f_*.jpg"))
    if not picks:
        return "一帧都没抽出来，这段大概是纯黑或纯静止画面。"

    step = max(1, (len(picks) + max(1, int(max_frames)) - 1) // max(1, int(max_frames)))
    chosen = list(zip(picks, times))[::step][: max(1, int(max_frames))]
    track = _subtitle_track(bvid)

    head = (f"《{bvid}》{_mmss(start)}–{_mmss(int(start) + int(dur))} · "
            f"{how} · 给 {len(chosen)} 帧（总抽 {len(picks)}）")
    body = []
    for path, at in chosen:
        body.append(f"{_mmss(at)} {path.resolve().as_uri()}{_lines_for(track, at)}")
    tail = "" if track else "\n（这条没有时间码字幕，画面自己看）"
    return head + "\n" + "\n".join(body) + tail


__all__ = ["frames"]
