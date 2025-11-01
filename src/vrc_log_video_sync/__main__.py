import argparse
import datetime as dt
import glob
import json
import os
import re
import threading
import time
from dataclasses import dataclass
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
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


STATE = PlayState()
STATE_LOCK = threading.Lock()


def on_attempt(ts: float, url: str) -> None:
    with STATE_LOCK:
        STATE.source = "youtube" if extract_youtube_id(url) else "other"
        STATE.pending_id = extract_youtube_id(url)
        STATE.pending_url = url
        STATE.last_event_epoch = ts
        STATE.last_event_text = "Attempting"
        if STATE.pending_id and STATE.pending_id != STATE.video_id:
            STATE.accepted_zero_start = False
            STATE.duration_sec = None
        STATE.status = "loading"


def on_resolved(ts: float, url: str, resolved: str) -> None:
    with STATE_LOCK:
        pid = extract_youtube_id(url)
        STATE.pending_id = pid or STATE.pending_id
        STATE.pending_url = url  # ensure we remember the original watch URL even if Attempting was missing
        if pid:
            STATE.source = "youtube"  # Unity Video Player path may skip Attempting; set source here
        STATE.resolved_url = resolved
        d = parse_duration_in_line(resolved)
        if d:
            STATE.duration_sec = d
        # Unity Video Player minimal fix: commit video_id at resolve time as well
        if STATE.pending_id and (STATE.video_id != STATE.pending_id):
            STATE.video_id = STATE.pending_id
            STATE.watch_url = ensure_watch_url(STATE.pending_url or STATE.original_url)
        STATE.last_event_epoch = ts
        STATE.last_event_text = "Resolved"
        STATE.status = "loading"


def on_opening(ts: float, url: str, offset: float | None) -> None:
    off = float(offset) if offset is not None else 0.0
    with STATE_LOCK:
        vid_changed = STATE.pending_id and STATE.pending_id != STATE.video_id
        if vid_changed:
            STATE.video_id = STATE.pending_id
            STATE.watch_url = ensure_watch_url(STATE.pending_url or STATE.original_url)
            STATE.accepted_zero_start = False
        if STATE.video_id is None and STATE.pending_id:
            STATE.video_id = STATE.pending_id
            STATE.watch_url = ensure_watch_url(STATE.pending_url or STATE.original_url)
        if off <= 0.05:
            # Accept only the very first zero-start to avoid reset loops.
            if not STATE.accepted_zero_start:
                STATE.started_epoch = ts
                STATE.accepted_zero_start = True
                STATE.status = "playing"
        else:
            STATE.started_epoch = ts - off
            STATE.status = "playing"
        if STATE.duration_sec is None:
            d = parse_duration_in_line(url)
            if d:
                STATE.duration_sec = d
        STATE.last_event_epoch = ts
        STATE.last_event_text = f"Opening offset={int(off)}"


def on_error(ts: float, text: str) -> None:
    with STATE_LOCK:
        # Keep the error status for UI, but the client won't treat it as a hard stop.
        STATE.status = "error"
        STATE.last_event_epoch = ts
        STATE.last_event_text = text


def on_stop(ts: float) -> None:
    with STATE_LOCK:
        STATE.status = "idle"
        STATE.started_epoch = None
        STATE.last_event_epoch = ts
        STATE.last_event_text = "Stop"


def parse_line(line: str) -> None:
    ts = parse_log_ts(line) or time.time()
    m = ATTEMPT.search(line)
    if m:
        on_attempt(ts, m.group("url"))
        return
    m = RESOLVED.search(line)
    if m:
        on_resolved(ts, m.group("url"), m.group("resolved"))
        return
    m = AVPRO_OPEN.search(line)
    if m:
        off = float(m.group("offset")) if m.group("offset") else 0.0
        on_opening(ts, m.group("url"), off)
        return
    # Unity Video Player explicit stop event
    if "Send Event _OnStop" in line:
        on_stop(ts)
        return
    if GENERIC_STOP.search(line):
        on_stop(ts)
        return
    if GENERIC_ERR.search(line):
        on_error(ts, "Video error")
        return
    d = parse_duration_in_line(line)
    if d:
        with STATE_LOCK:
            if STATE.duration_sec is None:
                STATE.duration_sec = d


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
            with STATE_LOCK:
                payload = STATE.to_dict(fudge)
            blob = json.dumps(payload).encode("utf-8")
            self.send_response(HTTPStatus.OK)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Cache-Control", "no-store")
            self.send_header("Content-Length", str(len(blob)))
            self.end_headers()
            self.wfile.write(blob)
            return
        self.send_error(HTTPStatus.NOT_FOUND, "Not Found")

    def log_message(self, fmt, *args):
        return


def find_latest_log_in_dir(log_dir: str) -> str | None:
    candidates = sorted(glob.glob(os.path.join(log_dir, "output_log_*.txt")))
    return candidates[-1] if candidates else None


def tail_follow(path: str):
    dir_name = os.path.dirname(path) or "."
    base_glob = os.path.join(dir_name, "output_log_*.txt")
    file = None
    current_path = None
    pos = 0

    def open_latest():
        nonlocal file, current_path, pos
        candidates = sorted(glob.glob(base_glob))
        if not candidates:
            return False
        latest = candidates[-1]
        if latest != current_path:
            if file:
                file.close()
            current_path = latest
            file = open(current_path, encoding="utf-8", errors="ignore")
            file.seek(0, os.SEEK_END)
            pos = file.tell()
        return True

    while not open_latest():
        time.sleep(0.5)

    while True:
        line = file.readline()
        if not line:
            try:
                if (
                    not os.path.exists(current_path)
                    or os.path.getsize(current_path) < pos
                ):
                    open_latest()
            except Exception:
                open_latest()
            time.sleep(0.2)
            continue
        pos = file.tell()
        yield current_path, line.rstrip("\n")


def run_server(host: str, port: int):
    srv = ThreadingHTTPServer((host, port), Handler)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    return srv


def run_watch(log_file: str | None, log_dir: str | None, replay: str | None):
    if replay:
        with open(replay, encoding="utf-8", errors="ignore") as f:
            for raw in f:
                line = raw.rstrip("\n")
                parse_line(line)
                time.sleep(0.01)
        return
    path = None
    if log_file and os.path.exists(log_file):
        path = log_file
    else:
        if not log_dir:
            home = os.path.expanduser("~")
            log_dir = os.path.join(home, "AppData", "LocalLow", "VRChat", "VRChat")
        if not os.path.isdir(log_dir):
            raise SystemExit(f"Log directory not found: {log_dir}")
        latest = find_latest_log_in_dir(log_dir)
        if not latest:
            raise SystemExit(f"No output_log_*.txt found in: {log_dir}")
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
    _ = ap.parse_args()
    srv = run_server(_.host, _.port)
    print(f"[ui] Open: http://{_.host}:{_.port}/client")
    try:
        run_watch(_.log_file, _.log_dir, _.replay)
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
