"""Step 1: download video (yt-dlp), Twitch chat (TwitchDownloaderCLI), extract audio."""
from __future__ import annotations

import json
import re
import shutil
import subprocess
from pathlib import Path

from .common import CFG, ROOT, ffprobe, jobs_dir, log, run, which_or_die, write_json


def _slug(text: str) -> str:
    return re.sub(r"[^A-Za-z0-9_-]+", "_", text).strip("_")[:60] or "video"


def probe_url(url: str) -> dict:
    ytdlp = which_or_die("yt-dlp", "Run ./setup.sh")
    out = subprocess.run([ytdlp, "-J", "--no-playlist", url], check=True, capture_output=True, text=True).stdout
    return json.loads(out)


def prepare_job(source: str, mode: str | None) -> tuple[str, Path]:
    """Create the job folder and fill it with source.mp4, audio.wav, chat.json (Twitch), meta.json."""
    src_path = Path(source).expanduser()
    if src_path.exists():
        job_id = "local_" + _slug(src_path.stem)
        jd = jobs_dir() / job_id
        jd.mkdir(parents=True, exist_ok=True)
        meta = {"source": str(src_path.resolve()), "platform": "local", "title": src_path.stem,
                "channel": "local", "video_id": src_path.stem}
        target = jd / "source.mp4"
        if not target.exists():
            if src_path.suffix.lower() == ".mp4":
                try:
                    target.symlink_to(src_path.resolve())
                except OSError:
                    shutil.copy2(src_path, target)
            else:
                run(["ffmpeg", "-y", "-i", src_path, "-c:v", "libx264", "-preset", "veryfast",
                     "-crf", "18", "-c:a", "aac", target])
    else:
        info = probe_url(source)
        extractor = (info.get("extractor_key") or info.get("extractor") or "web").lower()
        platform = "twitch" if "twitch" in extractor else ("youtube" if "youtube" in extractor else extractor)
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
        target = jd / "source.mp4"
        if not target.exists():
            h = CFG["download"]["max_height"]
            run([which_or_die("yt-dlp"), "--no-playlist",
                 "-f", f"bv*[height<={h}]+ba/b[height<={h}]/bv*+ba/b",
                 "--merge-output-format", "mp4", "-o", str(target), source])
        else:
            log("source.mp4 already downloaded, skipping")

        if platform == "twitch" and CFG["download"].get("twitch_chat", True):
            download_twitch_chat(vid, jd / "chat.json")

    if mode is None:
        mode = "stream" if meta["platform"] == "twitch" else "podcast"
    meta["mode"] = mode
    meta.update(ffprobe(jd / "source.mp4"))
    write_json(jd / "meta.json", meta)

    audio = jd / "audio.wav"
    if not audio.exists():
        run(["ffmpeg", "-y", "-loglevel", "error", "-i", jd / "source.mp4",
             "-vn", "-ac", "1", "-ar", "16000", "-c:a", "pcm_s16le", audio])
    return job_id, jd


def download_twitch_chat(video_id: str, out: Path) -> None:
    if out.exists():
        log("chat.json already downloaded, skipping")
        return
    vod_id = video_id.lstrip("v")
    cli = shutil.which("TwitchDownloaderCLI") or str(ROOT / "bin" / "TwitchDownloaderCLI")
    if not Path(cli).exists() and not shutil.which("TwitchDownloaderCLI"):
        log("TwitchDownloaderCLI not found (run ./setup.sh) -> continuing without chat signal")
        return
    try:
        run([cli, "chatdownload", "--id", vod_id, "-o", str(out)])
    except subprocess.CalledProcessError as e:
        log(f"Chat download failed ({e}); continuing without chat signal")
