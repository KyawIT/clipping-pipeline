"""Step 1: audio (yt-dlp), Twitch chat (TwitchDownloaderCLI) and stream metadata.

With `download.video: sections` (default) the video itself is NOT downloaded here: only its stream URLs are
recorded in stream.json. Single frames and the chosen clip intervals are fetched from it later (see media.py).
With `download.video: full` the whole video is downloaded to source.mp4 like before.
"""
from __future__ import annotations

import json
import re
import subprocess
import sys
import time
from pathlib import Path

from .common import (CFG, SETUP_HINT, ffprobe, find_tool, jobs_dir, link_or_copy, log, progress, run,
                     which_or_die, write_json)


STREAM_PLATFORMS = {"twitch", "kick"}


def platform_of(info: dict) -> str:
    """twitch / youtube / kick, else the yt-dlp extractor name."""
    extractor = (info.get("extractor_key") or info.get("extractor") or "web").lower()
    for name in ("twitch", "youtube", "kick"):
        if name in extractor:
            return name
    return extractor


def _slug(text: str) -> str:
    return re.sub(r"[^A-Za-z0-9_-]+", "_", text).strip("_")[:60] or "video"


def _is_local_file(source: str) -> bool:
    if re.match(r"^[a-z][a-z0-9+.-]*://", source, re.I):
        return False
    try:  # Windows raises for names with ':' / '?' instead of returning False
        return Path(source).expanduser().exists()
    except OSError:
        return False


def probe_url(url: str, fmt: str | None = None) -> dict:
    """yt-dlp metadata; with `fmt` it also resolves that format selection (requested_formats / url)."""
    ytdlp = which_or_die("yt-dlp")
    cmd = [ytdlp, "-J", "--no-playlist", *(["-f", fmt] if fmt else []), url]
    # stderr is not captured, so yt-dlp's own warnings / errors stay visible
    out = subprocess.run(cmd, check=True, stdout=subprocess.PIPE, text=True, encoding="utf-8", errors="replace").stdout
    return json.loads(out)


def _full_selector(h: int) -> str:
    return f"bv*[height<={h}]+ba/b[height<={h}]/bv*+ba/b"


def _stream_selector(h: int) -> str:
    # ffmpeg can seek in plain http(s) files and HLS playlists, not in fragmented DASH manifests
    ok = "[protocol!*=dash]"
    return f"bv*[height<={h}]{ok}+ba{ok}/b[height<={h}]{ok}/bv*{ok}+ba{ok}/b{ok}/{_full_selector(h)}"


def _has(f: dict, kind: str) -> bool:
    return f.get(kind) not in (None, "none")


def _fmt(f: dict) -> dict:
    return {"url": f["url"], "headers": f.get("http_headers") or {}, "protocol": f.get("protocol"),
            "format_id": f.get("format_id")}


def resolve(url: str) -> tuple[dict, dict]:
    """-> (stream, info). stream = {resolved_at, video: {url, headers}, audio: {...} | None, width, height, fps}."""
    info = probe_url(url, _stream_selector(CFG["download"]["max_height"]))
    req = info.get("requested_formats")
    if req:
        video = next((f for f in req if _has(f, "vcodec")), req[0])
        audio = next((f for f in req if f is not video and _has(f, "acodec")), None)
    else:
        video, audio = info, None  # one format with video + audio (e.g. Twitch HLS)
    stream = {
        "resolved_at": time.time(),
        "video": _fmt(video),
        "audio": _fmt(audio) if audio else None,
        "width": video.get("width"), "height": video.get("height"), "fps": video.get("fps"),
    }
    if any(f["protocol"] and "dash" in f["protocol"] for f in (stream["video"], stream["audio"]) if f):
        log("WARNING: this site only offers DASH streams; fetching clip intervals may fail. "
            "Set download.video: full in config.yaml if it does.")
    return stream, info


def resolve_stream(url: str) -> dict:
    return resolve(url)[0]


def prepare_job(source: str, mode: str | None) -> tuple[str, Path]:
    """Create the job folder and fill it with audio.wav, chat.json (Twitch), meta.json and either
    stream.json (sections mode) or source.mp4 (local files / full mode)."""
    src_path = Path(source).expanduser()
    stream = None
    if _is_local_file(source):
        job_id = "local_" + _slug(src_path.stem)
        jd = jobs_dir() / job_id
        jd.mkdir(parents=True, exist_ok=True)
        meta = {"source": str(src_path.resolve()), "platform": "local", "title": src_path.stem,
                "channel": "local", "video_id": src_path.stem}
        progress("job", job_id=job_id, platform="local", video_id=src_path.stem, title=src_path.stem,
                 channel="local", duration=None)
        progress("download", 50, "Using the local file")
        target = jd / "source.mp4"
        if not target.exists():
            if src_path.suffix.lower() == ".mp4":
                link_or_copy(src_path.resolve(), target)
            else:
                run(["ffmpeg", "-y", "-i", src_path, "-c:v", "libx264", "-preset", "veryfast",
                     "-crf", "18", "-c:a", "aac", target])
    else:
        progress("download", 0, "Looking up the video")
        stream, info = resolve(source)
        platform = platform_of(info)
        vid = str(info.get("id"))
        job_id = f"{platform}_{_slug(vid)}"
        jd = jobs_dir() / job_id
        jd.mkdir(parents=True, exist_ok=True)
        meta = {
            "source": source, "platform": platform, "video_id": vid,
            "title": info.get("title"), "channel": info.get("uploader") or info.get("channel") or "unknown",
            "channel_id": (info.get("uploader_id") or info.get("channel_id") or "unknown"),
            "duration": info.get("duration"),
        }
        progress("job", job_id=job_id, platform=platform, video_id=vid, title=meta["title"],
                 channel=meta["channel"], duration=meta["duration"])
        target = jd / "source.mp4"
        if target.exists():
            log("source.mp4 already downloaded, using it")
        elif CFG["download"].get("video", "sections") == "full":
            run([which_or_die("yt-dlp"), "--no-playlist", "-f", _full_selector(CFG["download"]["max_height"]),
                 "--merge-output-format", "mp4", "-o", str(target), source])
        else:
            write_json(jd / "stream.json", stream)
            log("Video stays online: only the audio is downloaded now, clips are fetched when rendering")
            download_audio(source, jd, stream)

        if platform == "twitch" and CFG["download"].get("twitch_chat", True):
            progress("download", 100, "Downloading the Twitch chat replay")
            download_twitch_chat(vid, jd / "chat.json")

    if mode is None:  # streams have a webcam overlay; everything else is treated like a podcast / talking head
        mode = "stream" if meta["platform"] in STREAM_PLATFORMS else "podcast"
    meta["mode"] = mode

    if (jd / "source.mp4").exists():
        meta.update(ffprobe(jd / "source.mp4"))
        audio = jd / "audio.wav"
        if not audio.exists():
            run(["ffmpeg", "-y", "-loglevel", "error", "-i", jd / "source.mp4",
                 "-vn", "-ac", "1", "-ar", "16000", "-c:a", "pcm_s16le", audio])
    else:
        meta.update({k: stream[k] for k in ("width", "height", "fps") if stream.get(k)})
        if not (meta.get("width") and meta.get("height") and meta.get("fps")):
            v = stream["video"]
            probed = ffprobe(v["url"], v["headers"])
            meta.update({k: probed[k] for k in ("width", "height", "fps") if k in probed and not meta.get(k)})
        if not meta.get("duration"):
            meta["duration"] = ffprobe(jd / "audio.wav")["duration"]
        meta["duration"] = float(meta["duration"])
    write_json(jd / "meta.json", meta)
    return job_id, jd


def download_audio(source: str, jd: Path, stream: dict) -> None:
    """Audio-only download (a few % of the video size), converted to 16 kHz mono WAV for WhisperX."""
    wav = jd / "audio.wav"
    if wav.exists():
        log("audio.wav already downloaded, skipping")
        return

    def finished() -> list[Path]:
        return [p for p in jd.glob("audio_src.*") if not p.name.endswith((".part", ".ytdl"))]

    if not finished():  # yt-dlp resumes its own .part file after an interruption
        progress("download", 0, "Downloading the audio track")
        # --newline: one progress line per update, which the web app turns into a progress bar
        run([which_or_die("yt-dlp"), "--no-playlist", "--newline", "-f", "ba/worst",
             "-o", str(jd / "audio_src.%(ext)s"), source])
    progress("download", 100, "Converting the audio")
    got = finished()
    if not got:
        sys.exit("Audio download failed")
    src = got[0]
    # Sites without an audio-only stream hand over the whole video. If it is the full-quality one, keep it
    # as source.mp4 instead of fetching the clips again later (prepare then extracts audio.wav from it).
    probed = ffprobe(src)
    if src.suffix.lower() == ".mp4" and probed.get("height") and probed["height"] >= (stream.get("height") or 10**9):
        log("No audio-only stream on this site, so the whole video was downloaded anyway: keeping it as source.mp4")
        src.replace(jd / "source.mp4")
        return
    run(["ffmpeg", "-y", "-loglevel", "error", "-i", src,
         "-vn", "-ac", "1", "-ar", "16000", "-c:a", "pcm_s16le", wav])
    src.unlink()


def download_twitch_chat(video_id: str, out: Path) -> None:
    if out.exists():
        log("chat.json already downloaded, skipping")
        return
    vod_id = video_id.lstrip("v")
    cli = find_tool("TwitchDownloaderCLI")
    if not cli:
        log(f"TwitchDownloaderCLI not found ({SETUP_HINT}) -> continuing without chat signal")
        return
    try:
        run([cli, "chatdownload", "--id", vod_id, "-o", str(out)])
    except subprocess.CalledProcessError as e:
        log(f"Chat download failed ({e}); continuing without chat signal")
