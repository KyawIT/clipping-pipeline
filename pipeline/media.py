"""Where the pictures come from.

A job has either a local source.mp4 (local files, `download.video: full`, older jobs) or a stream.json with the
online stream URLs (`download.video: sections`). In the second case only what is needed is fetched over HTTP:
single frames for Claude / layout detection, and the exact clip intervals at render time.
"""
from __future__ import annotations

import re
import subprocess
import time
import urllib.request
from pathlib import Path
from urllib.parse import urljoin

from .common import ffmpeg_headers, fmt_ts, log, read_json, run, write_json

# YouTube stream URLs expire after ~6 h; resolve again well before that (and always after a failed fetch).
STREAM_TTL_SEC = 3600


def local_source(jd: Path) -> Path | None:
    p = jd / "source.mp4"
    return p if p.exists() else None


def has_video(jd: Path) -> bool:
    return local_source(jd) is not None or (jd / "stream.json").exists()


def stream_info(jd: Path, refresh: bool = False) -> dict:
    p = jd / "stream.json"
    if p.exists() and not refresh:
        st = read_json(p)
        if time.time() - st.get("resolved_at", 0) < STREAM_TTL_SEC:
            return st
    from .download import resolve_stream  # lazy: download pulls in yt-dlp helpers
    log("Resolving stream URLs" + (" again" if refresh else ""))
    st = resolve_stream(read_json(jd / "meta.json")["source"])
    write_json(p, st)
    return st


def _is_hls(fmt: dict) -> bool:
    return (fmt.get("protocol") or "").startswith("m3u8") or ".m3u8" in fmt["url"].split("?")[0]


def _remote_input(jd: Path, fmt: dict, start: float, end: float) -> list[str]:
    """ffmpeg input args that start at source time `start` (and cover up to `end`).

    Plain http(s) files: -ss before -i, ffmpeg seeks with range requests (exact).
    HLS (Twitch): ffmpeg's own HLS seek can land a whole segment off, so the covering segments are
    downloaded here and cut locally instead (see _hls_piece)."""
    if _is_hls(fmt):
        piece, piece_start = _hls_piece(jd, fmt, start, end)
        return ["-ss", f"{max(0.0, start - piece_start):.3f}", "-i", str(piece)]
    return ["-ss", f"{start:.3f}", *ffmpeg_headers(fmt.get("headers")), "-i", fmt["url"]]


def _with_stream(jd: Path, fetch):
    """fetch(stream) reads from the stream; on failure resolve fresh URLs once and retry."""
    try:
        return fetch(stream_info(jd))
    except (subprocess.CalledProcessError, OSError) as e:  # OSError covers urllib's URLError / HTTPError
        log(f"Stream fetch failed ({e.__class__.__name__}), the URLs may have expired")
        return fetch(stream_info(jd, refresh=True))


# ------------------------------------------------------------------ HLS (Twitch VODs)

_PLAYLISTS: dict[str, tuple[list[tuple[float, float, str]], str | None]] = {}


def _http_get(url: str, headers: dict | None) -> bytes:
    req = urllib.request.Request(url, headers=headers or {})
    with urllib.request.urlopen(req, timeout=60) as r:
        return r.read()


def _hls_playlist(fmt: dict) -> tuple[list[tuple[float, float, str]], str | None]:
    """-> ([(start, duration, url), ...], init-segment url or None) of a media playlist."""
    if fmt["url"] in _PLAYLISTS:
        return _PLAYLISTS[fmt["url"]]
    text = _http_get(fmt["url"], fmt.get("headers")).decode("utf-8", "replace")
    if "#EXT-X-STREAM-INF" in text:
        raise RuntimeError("HLS master playlist instead of a media playlist; set download.video: full")
    if "#EXT-X-BYTERANGE" in text or re.search(r"#EXT-X-KEY:(?![^\n]*METHOD=NONE)", text):
        raise RuntimeError("encrypted / byte-range HLS is not supported; set download.video: full")
    segs, t, dur, init = [], 0.0, None, None
    for line in (ln.strip() for ln in text.splitlines()):
        if line.startswith("#EXTINF:"):
            dur = float(line[8:].split(",")[0])
        elif line.startswith("#EXT-X-MAP:"):
            m = re.search(r'URI="([^"]+)"', line)
            init = urljoin(fmt["url"], m.group(1)) if m else None
        elif line and not line.startswith("#") and dur is not None:
            segs.append((t, dur, urljoin(fmt["url"], line)))
            t += dur
            dur = None
    if not segs:
        raise RuntimeError("empty HLS playlist")
    _PLAYLISTS[fmt["url"]] = (segs, init)
    return segs, init


def _start_pts(path: Path) -> float | None:
    out = subprocess.run(["ffprobe", "-v", "error", "-show_entries", "format=start_time", "-of", "csv=p=0",
                          str(path)], capture_output=True, text=True).stdout.strip()
    try:
        return float(out)
    except ValueError:
        return None


def _download_segments(fmt: dict, init: str | None, urls: list[str], out: Path) -> None:
    with open(out, "wb") as f:
        for u in ([init] if init else []) + urls:
            f.write(_http_get(u, fmt.get("headers")))


def _first_pts(jd: Path, fmt: dict, segs: list, init: str | None) -> float | None:
    """PTS at the very start of the stream (= source time 0 of audio.wav / the transcript). Cached per job."""
    cache_path = jd / "hls_pts.json"
    cache = read_json(cache_path) if cache_path.exists() else {}
    key = segs[0][2].split("?")[0]
    if key not in cache:
        tmp = jd / "segments" / ("first" + (".mp4" if init else ".ts"))
        tmp.parent.mkdir(parents=True, exist_ok=True)
        _download_segments(fmt, init, [segs[0][2]], tmp)
        cache[key] = _start_pts(tmp)
        tmp.unlink(missing_ok=True)
        write_json(cache_path, cache)
    return cache[key]


def _hls_piece(jd: Path, fmt: dict, start: float, end: float) -> tuple[Path, float]:
    """Download only the HLS segments covering [start - 1, end + 1] into one local file.
    -> (file, source time at the file's start). The time comes from the stream's own timestamps (PTS),
    with the playlist's segment durations as a fallback."""
    segs, init = _hls_playlist(fmt)
    lo, hi = start - 1.0, end + 1.0
    pick = [i for i, (t, d, _) in enumerate(segs) if t + d > lo and t < hi] or [len(segs) - 1]
    piece = jd / "segments" / f"hls_{fmt.get('format_id') or 'v'}_{start:.2f}_{end:.2f}{'.mp4' if init else '.ts'}"
    piece.parent.mkdir(parents=True, exist_ok=True)
    _download_segments(fmt, init, [segs[i][2] for i in pick], piece)
    estimate = segs[pick[0]][0]
    first, here = _first_pts(jd, fmt, segs, init), _start_pts(piece)
    piece_start = estimate
    if first is not None and here is not None and abs((here - first) - estimate) < 3.0:
        piece_start = here - first
    return piece, piece_start


# ------------------------------------------------------------------ frames

def _png(inputs: list[str], width: int | None) -> bytes:
    vf = ["-vf", f"scale={width}:-2"] if width else []
    return subprocess.run(["ffmpeg", "-v", "error", *inputs, "-frames:v", "1", *vf,
                           "-f", "image2pipe", "-vcodec", "png", "-"], check=True, capture_output=True).stdout


def frame_png(src: Path, t: float, width: int | None = None) -> bytes:
    """One frame at t seconds as PNG bytes. `src` is a job folder (source.mp4 or the stream) or a video file."""
    if not src.is_dir():
        return _png(["-ss", f"{t:.3f}", "-i", str(src)], width)
    local = local_source(src)
    if local:
        return _png(["-ss", f"{t:.3f}", "-i", str(local)], width)
    try:
        return _with_stream(src, lambda st: _png(_remote_input(src, st["video"], t, t + 0.5), width))
    finally:
        _clean_pieces(src)


def _clean_pieces(jd: Path) -> None:
    for p in (jd / "segments").glob("hls_*"):
        p.unlink(missing_ok=True)


# ------------------------------------------------------------------ clip intervals

def segment(jd: Path, start: float, end: float, fps: int) -> Path:
    """The interval [start, end] as a local mp4, re-encoded at `fps` and frame-accurate (input seek + transcode).
    Cached in jobs/<job>/segments/, so re-rendering a clip with a new hook or thumbnail fetches nothing."""
    out = jd / "segments" / f"{start:.2f}_{end:.2f}_{fps}_hq.mp4"
    if out.exists():
        return out
    out.parent.mkdir(parents=True, exist_ok=True)
    tmp = out.with_name(out.stem + ".part.mp4")
    # an intermediate: encode near-losslessly so the panels / final render start from the source's own detail
    encode = ["-t", f"{end - start:.3f}", "-r", str(fps), "-c:v", "libx264", "-preset", "medium", "-crf", "10",
              "-c:a", "aac", "-b:a", "256k", "-movflags", "+faststart", str(tmp)]

    local = local_source(jd)
    if local:
        run(["ffmpeg", "-y", "-loglevel", "error", "-ss", f"{start:.3f}", "-i", local, *encode])
    else:
        def fetch(st: dict) -> None:
            video, audio = st["video"], st.get("audio")
            log(f"  fetching {fmt_ts(start)} - {fmt_ts(end)} ({end - start:.1f}s) from the stream")
            inputs = _remote_input(jd, video, start, end) + (_remote_input(jd, audio, start, end) if audio else [])
            maps = ["-map", "0:v:0", "-map", "1:a:0" if audio else "0:a:0?"]
            # not via run(): the signed URLs would flood the log
            subprocess.run(["ffmpeg", "-y", "-loglevel", "error", *inputs, *maps, *encode], check=True)
        try:
            _with_stream(jd, fetch)
        finally:
            _clean_pieces(jd)
    tmp.replace(out)
    return out
