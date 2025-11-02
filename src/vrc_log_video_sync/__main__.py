import argparse
import contextlib
import datetime as dt
import json
import os
import re
import sys
import threading
import time
import webbrowser
from collections.abc import Iterator
from dataclasses import dataclass
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any, TextIO
from urllib.parse import parse_qs, urlparse

CLIENT_HTML = r"""
<!doctype html>
<html>
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>VRChat Log Sync</title>
<style>
  body { font-family: system-ui, sans-serif; margin: 16px; }
  #info { margin: 8px 0 12px; font-size: 14px; }
  #player { width: 100%; max-width: 960px; aspect-ratio: 16/9; }
  .row { margin: 4px 0; }
  .label { color: #666; margin-right: 4px; }
  code { background: #f6f8fa; padding: 2px 4px; border-radius: 4px; }
</style>
</head>
<body>
<h1 style="margin:0 0 8px">VRChat → Browser Video Sync</h1>
<div id="info">
  <div class="row"><span class="label">Source:</span><code id="src">-</code></div>
  <div class="row"><span class="label">Video ID:</span><code id="vid">-</code></div>
  <div class="row"><span class="label">Position:</span><code id="pos">0</code></div>
  <div class="row"><span class="label">Duration:</span><code id="dur">-</code></div>
  <div class="row"><span class="label">Status:</span><code id="st">-</code></div>
</div>

<div id="player"></div>

<script>

let player = null;
let ytReady = false;
let lastVideoId = null;
const SEEK_EPS = 1.2;

function onYouTubeIframeAPIReady() {
  ytReady = true;
  player = new YT.Player('player', {
    width: '960',
    height: '540',
    playerVars: { 'playsinline': 1, 'origin': location.origin },
    events: {}
  });
}

(function inject() {
  const s = document.createElement('script');
  s.src = "https://www.youtube.com/iframe_api";
  document.head.appendChild(s);
})();

function fmtSec(x) {
  if (x == null || isNaN(x)) return "-";
  return x.toFixed(2) + "s";
}

async function fetchState() {
  try {
    const res = await fetch('/state');
    const s = await res.json();
    document.getElementById('src').textContent = s.source || '-';
    document.getElementById('vid').textContent = s.video_id || '-';
    document.getElementById('pos').textContent = fmtSec(s.estimated_position_sec);
    document.getElementById('dur').textContent = fmtSec(s.duration_sec);
    document.getElementById('st').textContent = s.status || '-';

    // Only stop when status is explicitly idle (real stop) or video id is gone.
    const shouldStop = (s.status === 'idle') || !s.video_id;

    if (!shouldStop && s.source === 'youtube' && s.video_id && ytReady && player) {
      const want = Math.max(0, s.estimated_position_sec || 0);
      if (lastVideoId !== s.video_id) {
        lastVideoId = s.video_id;
        if (player.loadVideoById) {
          player.loadVideoById({ videoId: s.video_id, startSeconds: want });
        }
      } else {
        const cur = player.getCurrentTime ? player.getCurrentTime() : 0;
        if (!isNaN(cur) && want > 0.8 && Math.abs(cur - want) > SEEK_EPS) {
          player.seekTo(want, true);
        }
      }
    } else {
      if (shouldStop && lastVideoId !== null) {
        lastVideoId = null;
        if (player && player.stopVideo) player.stopVideo();
      }
    }
  } catch (e) {}
  setTimeout(fetchState, 800);
}

setTimeout(fetchState, 800);
</script>
</body>
</html>
"""

LOG_TS = re.compile(r"^(?P<ts>\d{4}\.\d{2}\.\d{2} \d{2}:\d{2}:\d{2})\s+\w+\s+-\s+")
ATTEMPT = re.compile(r"\[Video Playback\]\s+Attempting to resolve URL '(?P<url>[^']+)'")
RESOLVED = re.compile(
    r"\[Video Playback\]\s+URL '(?P<url>[^']+)' resolved to '(?P<resolved>[^']+)'"
)
AVPRO_OPEN = re.compile(
    r"\[AVProVideo\]\s+Opening\s+(?P<url>https?://\S+)(?:\s+\(offset\s+(?P<offset>\d+)\))?"
)
ANY_URL = re.compile(r"(https?://\S+)")
GENERIC_ERR = re.compile(
    r"(?:PlayerError|Video player error|Video error|\[AVProVideo\]\s+Error)",
    re.IGNORECASE,
)
GENERIC_STOP = re.compile(r"Video stop", re.IGNORECASE)
DUR_PARAM = re.compile(r"[?&]dur=(?P<dur>\d+(?:\.\d+)?)")


def parse_log_ts(line: str) -> float | None:
    m = LOG_TS.match(line)
    if not m:
        return None
    try:
        d = dt.datetime.strptime(m.group("ts"), "%Y.%m.%d %H:%M:%S")
        return d.timestamp()
    except Exception:
        return None


def extract_youtube_id(url: str) -> str | None:
    try:
        u = urlparse(url)
    except Exception:
        return None
    host = (u.netloc or "").lower()
    if "youtu.be" in host:
        parts = [p for p in u.path.split("/") if p]
        if parts:
            return parts[0]
    if "youtube.com" in host:
        qs = parse_qs(u.query)
        if "v" in qs and qs["v"]:
            return qs["v"][0]
        parts = [p for p in u.path.split("/") if p]
        if parts and parts[0] in ("shorts", "embed") and len(parts) >= 2:
            return parts[1]
    return None


def ensure_watch_url(url: str) -> str:
    vid = extract_youtube_id(url) or ""
    return f"https://www.youtube.com/watch?v={vid}" if vid else url


def parse_duration_in_line(line: str) -> float | None:
    murl = ANY_URL.search(line)
    if not murl:
        return None
    url = murl.group(1)
    m = DUR_PARAM.search(url)
    if not m:
        return None
    try:
        return float(m.group("dur"))
    except Exception:
        return None


@dataclass
class PlayState:
    video_id: str | None = None
    source: str = "other"
    original_url: str = ""
    resolved_url: str = ""
    watch_url: str = ""
    pending_id: str | None = None
    pending_url: str = ""
    status: str = "idle"
    started_epoch: float | None = None
    accepted_zero_start: bool = False
    last_event_epoch: float = 0.0
    last_event_text: str = ""
    duration_sec: float | None = None

    def to_dict(self, fudge: float) -> dict:
        now = time.time()
        pos = 0.0
        if self.started_epoch is not None:
            pos = max(0.0, now - self.started_epoch - max(fudge, 0.0))
        # Minimal fix: treat temporary "error" as non-stopping. Only "idle" is a stop.
        playing = self.video_id is not None and self.status != "idle"
        return {
            "playing": bool(playing),
            "source": self.source,
            "video_id": self.video_id,
            "watch_url": self.watch_url,
            "status": self.status,
            "estimated_position_sec": pos,
            "duration_sec": self.duration_sec,
            "last_event": self.last_event_text,
        }


class StateManager:
    def __init__(self) -> None:
        self._state = PlayState()
        self._lock = threading.Lock()

    def snapshot(self, fudge: float) -> dict:
        with self._lock:
            return self._state.to_dict(fudge)

    def on_attempt(self, ts: float, url: str) -> None:
        vid = extract_youtube_id(url)
        with self._lock:
            state = self._state
            state.source = "youtube" if vid else "other"
            state.pending_id = vid
            state.pending_url = url
            state.last_event_epoch = ts
            state.last_event_text = "Attempting"
            if vid and vid != state.video_id:
                state.accepted_zero_start = False
                state.duration_sec = None
            state.status = "loading"

    def on_resolved(self, ts: float, url: str, resolved: str) -> None:
        pid = extract_youtube_id(url)
        duration = parse_duration_in_line(resolved)
        with self._lock:
            state = self._state
            state.pending_id = pid or state.pending_id
            state.pending_url = url
            if pid:
                state.source = "youtube"
            state.resolved_url = resolved
            if duration:
                state.duration_sec = duration
            if state.pending_id and (state.video_id != state.pending_id):
                state.video_id = state.pending_id
                state.watch_url = ensure_watch_url(state.pending_url or state.original_url)
            state.last_event_epoch = ts
            state.last_event_text = "Resolved"
            state.status = "loading"

    def on_opening(self, ts: float, url: str, offset: float | None) -> None:
        duration = parse_duration_in_line(url)
        off = float(offset) if offset is not None else 0.0
        with self._lock:
            state = self._state
            vid_changed = state.pending_id and state.pending_id != state.video_id
            if vid_changed:
                state.video_id = state.pending_id
                state.watch_url = ensure_watch_url(state.pending_url or state.original_url)
                state.accepted_zero_start = False
            if state.video_id is None and state.pending_id:
                state.video_id = state.pending_id
                state.watch_url = ensure_watch_url(state.pending_url or state.original_url)
            if off <= 0.05:
                if not state.accepted_zero_start:
                    state.started_epoch = ts
                    state.accepted_zero_start = True
                    state.status = "playing"
            else:
                state.started_epoch = ts - off
                state.status = "playing"
            if state.duration_sec is None and duration:
                state.duration_sec = duration
            state.last_event_epoch = ts
            state.last_event_text = f"Opening offset={int(off)}"

    def on_error(self, ts: float, text: str) -> None:
        with self._lock:
            state = self._state
            state.status = "error"
            state.last_event_epoch = ts
            state.last_event_text = text

    def on_stop(self, ts: float) -> None:
        with self._lock:
            state = self._state
            state.status = "idle"
            state.started_epoch = None
            state.last_event_epoch = ts
            state.last_event_text = "Stop"

    def remember_duration(self, duration: float | None) -> None:
        if duration is None:
            return
        with self._lock:
            state = self._state
            if state.duration_sec is None:
                state.duration_sec = duration


STATE = StateManager()


def parse_line(line: str) -> None:
    ts = parse_log_ts(line) or time.time()
    m = ATTEMPT.search(line)
    if m:
        STATE.on_attempt(ts, m.group("url"))
        return
    m = RESOLVED.search(line)
    if m:
        STATE.on_resolved(ts, m.group("url"), m.group("resolved"))
        return
    m = AVPRO_OPEN.search(line)
    if m:
        off = float(m.group("offset")) if m.group("offset") else 0.0
        STATE.on_opening(ts, m.group("url"), off)
        return
    # Unity Video Player explicit stop event
    if "Send Event _OnStop" in line:
        STATE.on_stop(ts)
        return
    if GENERIC_STOP.search(line):
        STATE.on_stop(ts)
        return
    if GENERIC_ERR.search(line):
        STATE.on_error(ts, "Video error")
        return
    STATE.remember_duration(parse_duration_in_line(line))


class Handler(BaseHTTPRequestHandler):
    server_version = "VRCLogSync/2.1-minfix"

    def do_GET(self):
        parsed = urlparse(self.path)
        if parsed.path in ("/", "/client"):
            self.send_response(HTTPStatus.OK)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.end_headers()
            self.wfile.write(CLIENT_HTML.encode("utf-8"))
            return
        if parsed.path == "/state":
            qs = parse_qs(parsed.query or "")
            fudge = 1.5
            try:
                if "fudge" in qs and qs["fudge"]:
                    fudge = float(qs["fudge"][0])
            except Exception:
                pass
            payload = STATE.snapshot(fudge)
            blob = json.dumps(payload).encode("utf-8")
            self.send_response(HTTPStatus.OK)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Cache-Control", "no-store")
            self.send_header("Content-Length", str(len(blob)))
            self.end_headers()
            self.wfile.write(blob)
            return
        self.send_error(HTTPStatus.NOT_FOUND, "Not Found")

    def log_message(self, format: str, *args: Any) -> None:
        del format, args
        return


def find_latest_log_in_dir(log_dir: str | Path) -> Path | None:
    directory = Path(log_dir)
    candidates = sorted(directory.glob("output_log_*.txt"))
    return candidates[-1] if candidates else None


def tail_follow(path: str | Path) -> Iterator[tuple[str, str]]:
    base_path = Path(path)
    watch_dir = base_path.parent
    pattern = "output_log_*.txt"
    file: TextIO | None = None
    current_path: Path | None = None
    pos = 0

    def open_latest() -> bool:
        nonlocal file, current_path, pos
        candidates = sorted(watch_dir.glob(pattern))
        if not candidates:
            return False
        latest = candidates[-1]
        if latest != current_path:
            if file:
                file.close()
            current_path = latest
            file = latest.open(encoding="utf-8", errors="ignore")
            file.seek(0, os.SEEK_END)
            pos = file.tell()
        return True

    while True:
        if file is None or current_path is None:
            if not open_latest():
                time.sleep(0.5)
            continue
        line = file.readline()
        if not line:
            try:
                if not current_path.exists() or current_path.stat().st_size < pos:
                    file.close()
                    file = None
                    current_path = None
                    continue
                if open_latest():
                    continue
            except Exception:
                file = None
                current_path = None
                continue
            time.sleep(0.2)
            continue
        pos = file.tell()
        yield str(current_path), line.rstrip("\n")


def run_server(host: str, port: int):
    srv = ThreadingHTTPServer((host, port), Handler)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    return srv


def _enable_windows_ansi() -> None:
    if os.name != "nt":
        return
    try:
        import ctypes
        import msvcrt  # noqa: F401  # ensure Windows console present

        kernel32 = ctypes.windll.kernel32
        h = kernel32.GetStdHandle(-11)  # STD_OUTPUT_HANDLE
        mode = ctypes.c_uint32()
        if kernel32.GetConsoleMode(h, ctypes.byref(mode)):
            enable_virtual_terminal_processing = 0x0004
            kernel32.SetConsoleMode(h, mode.value | enable_virtual_terminal_processing)
    except Exception:
        pass


def _fmt_sec(x: float | None) -> str:
    try:
        if x is None:
            return "-"
        return f"{float(x):.2f}s"
    except Exception:
        return "-"


def _open_browser(url: str) -> None:
    with contextlib.suppress(Exception):
        webbrowser.open(url, new=1, autoraise=True)


def run_tui(url: str, refresh_sec: float = 0.5) -> None:
    _enable_windows_ansi()
    hint_quit_shown = False
    while True:
        s = STATE.snapshot(1.5)
        try:
            sys.stdout.write("\x1b[2J\x1b[H")  # clear + home
        except Exception:
            print("\n" * 3, end="")
        print("VRChat Log Video Sync — Console View")
        print("".ljust(60, "-"))
        print(f" Source   : {s.get('source') or '-'}")
        print(f" Video ID : {s.get('video_id') or '-'}")
        print(f" Position : {_fmt_sec(s.get('estimated_position_sec'))}")
        print(f" Duration : {_fmt_sec(s.get('duration_sec'))}")
        print(f" Status   : {s.get('status') or '-'}")
        print(f" URL      : {s.get('watch_url') or '-'}")
        print()
        print(" Keys: [O]pen in browser   [Ctrl+C] Exit")
        print(f" Open: {url}")

        # lightweight key handling on Windows (optional)
        if os.name == "nt":
            try:
                import msvcrt

                if msvcrt.kbhit():
                    ch = msvcrt.getwch()
                    if ch in ("o", "O"):
                        _open_browser(url)
                    elif ch in ("q", "Q") and not hint_quit_shown:
                        print("Press Ctrl+C to exit.")
                        hint_quit_shown = True
            except Exception:
                pass
        time.sleep(refresh_sec)


def run_watch(log_file: str | None, log_dir: str | None, replay: str | None):
    if replay:
        with Path(replay).open(encoding="utf-8", errors="ignore") as f:
            for raw in f:
                line = raw.rstrip("\n")
                parse_line(line)
                time.sleep(0.01)
        return
    path = None
    if log_file and Path(log_file).exists():
        path = Path(log_file)
    else:
        if not log_dir:
            home = Path.home()
            log_dir_path = home / "AppData" / "LocalLow" / "VRChat" / "VRChat"
        else:
            log_dir_path = Path(log_dir)
        if not log_dir_path.is_dir():
            raise SystemExit(f"Log directory not found: {log_dir_path}")
        latest = find_latest_log_in_dir(log_dir_path)
        if not latest:
            raise SystemExit(f"No output_log_*.txt found in: {log_dir_path}")
        path = latest
    print(f"[log] Following: {path}")
    for _, line in tail_follow(path):
        parse_line(line)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--log-dir", type=str, default=None)
    ap.add_argument("--log-file", type=str, default=None)
    ap.add_argument("--port", type=int, default=7957)
    ap.add_argument("--host", type=str, default="127.0.0.1")
    ap.add_argument("--replay", type=str, default=None)
    ap.add_argument("--no-browser", action="store_true", help="Do not auto open browser UI")
    ap.add_argument("--no-tui", action="store_true", help="Do not show console TUI")
    _ = ap.parse_args()
    _server = run_server(_.host, _.port)
    url = f"http://{_.host}:{_.port}/client"
    print(f"[ui] Open: {url}")
    if not _.no_browser:
        threading.Thread(target=_open_browser, args=(url,), daemon=True).start()
    if not _.no_tui:
        threading.Thread(target=run_tui, args=(url,), daemon=True).start()
    with contextlib.suppress(KeyboardInterrupt):
        run_watch(_.log_file, _.log_dir, _.replay)


if __name__ == "__main__":
    main()
