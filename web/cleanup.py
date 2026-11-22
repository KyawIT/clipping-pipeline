"""Deleting clips and job folders from disk (permanently) and measuring what a job folder holds.

Used by the web app (Library / run page) and by `clip.py delete`. Everything here refuses paths outside jobs/.
"""
from __future__ import annotations

import os
import shutil
import stat
from pathlib import Path
from typing import Any, Optional

from pipeline.common import ROOT, jobs_dir, parse_ts, read_json, write_json

REMOTION_PUBLIC = ROOT / "remotion" / "public" / "jobs"

# heavy files a job only needs for picking / re-rendering, never for the finished clips
WORKING = ["source.mp4", "audio.wav", "vocals.wav", "demucs", "segments", "rec", "frames", "hls_pts.json",
           "chat_live.jsonl", "chat.json", "signals.json"]
WORKING_GLOBS = ["audio_src.*", "*.part", "*.ytdl"]


class Refused(Exception):
    """The request would touch something it must not (outside jobs/, an active run, ...)."""


def safe_job_dir(job_id: str) -> Path:
    if not job_id or job_id in (".", "..") or "/" in job_id or "\\" in job_id or job_id.startswith("_"):
        raise Refused(f"not a job folder: {job_id!r}")
    root = jobs_dir().resolve()
    jd = (root / job_id).resolve()
    if jd.parent != root:
        raise Refused(f"not a job folder: {job_id!r}")
    return jd


def _size(p: Path) -> int:
    try:
        if p.is_symlink():
            return 0
        if p.is_file():
            return p.stat().st_size
        if p.is_dir():
            return sum(_size(c) for c in p.iterdir())
    except OSError:
        pass
    return 0


def _working_paths(jd: Path) -> list[Path]:
    paths = [jd / n for n in WORKING if (jd / n).exists()]
    for g in WORKING_GLOBS:
        paths += [p for p in jd.glob(g) if p not in paths]
    return paths


def usage(jd: Path) -> dict[str, int]:
    """Bytes per group: clips (out/), working (source / audio / cached segments / recording), archive, other."""
    clips = _size(jd / "out")
    working = sum(_size(p) for p in _working_paths(jd))
    archive = _size(jd / "archive")
    total = _size(jd)
    public = _size(REMOTION_PUBLIC / jd.name)
    return {"clips": clips, "working": working + public, "archive": archive,
            "other": max(0, total - clips - working - archive), "total": total + public}


def _short(path) -> str:
    p = Path(path)
    try:
        return str(p.relative_to(jobs_dir()))
    except ValueError:
        return p.name


def _remove(p: Path, failed: list[str]) -> int:
    """Delete a file or folder for good. Returns the bytes freed; locked files go to `failed`."""
    if not p.exists() and not p.is_symlink():
        return 0
    size = _size(p)

    def retry(func, path, _exc):
        try:  # Windows: read-only files can't be deleted until the flag is cleared
            os.chmod(path, stat.S_IWRITE)
            func(path)
        except OSError:
            failed.append(_short(path))

    try:
        if p.is_dir() and not p.is_symlink():
            shutil.rmtree(p, onerror=retry)  # onerror: works on Python 3.9-3.13
        else:
            try:
                p.unlink()
            except PermissionError:
                retry(os.unlink, str(p), None)
    except OSError:
        failed.append(_short(p))
    return size - (_size(p) if p.exists() else 0)


def _clips_list(jd: Path) -> tuple[Any, list[dict]]:
    f = jd / "clips.json"
    if not f.exists():
        return None, []
    data = read_json(f)
    return data, (data["clips"] if isinstance(data, dict) else data)


def clip_files(jd: Path, cid: str, start: Optional[float] = None, end: Optional[float] = None) -> list[Path]:
    """Every file a rendered clip left behind: the mp4, thumbnail, caption text, props and the cached interval."""
    out = jd / "out"
    files = [out / f"{cid}.mp4", out / f"{cid}_thumb.jpg", out / f"{cid}.txt"]
    files += sorted((jd / "props").glob(f"{cid}.json")) + sorted((jd / "props").glob(f"{cid}_*.json"))
    if start is not None and end is not None:
        files += sorted((jd / "segments").glob(f"{start:.2f}_{end:.2f}_*"))
    pub = REMOTION_PUBLIC / jd.name
    files += sorted(pub.glob(f"{cid}.mp4")) + sorted(pub.glob(f"{cid}_*.mp4"))
    return [f for f in files if f.exists()]


def delete_clip(jd: Path, cid: str) -> dict[str, Any]:
    """Remove one clip's files and its entry in clips.json (so `render <job>` won't bring it back)."""
    data, clips = _clips_list(jd)
    entry = next((c for i, c in enumerate(clips, 1) if (c.get("id") or f"clip_{i:02d}") == cid), None)
    s = e = None
    if entry is not None:
        s, e = round(parse_ts(entry["start"]), 2), round(parse_ts(entry["end"]), 2)
    failed: list[str] = []
    freed = sum(_remove(f, failed) for f in clip_files(jd, cid, s, e))
    if entry is not None and not (jd / "out" / f"{cid}.mp4").exists():
        clips.remove(entry)
        write_json(jd / "clips.json", data if isinstance(data, dict) else clips)
    return {"freed": freed, "failed": failed}


def free_space(jd: Path) -> dict[str, Any]:
    """Delete the working files and archive, keep the clips + their metadata."""
    failed: list[str] = []
    freed = sum(_remove(p, failed) for p in _working_paths(jd))
    freed += _remove(jd / "archive", failed)
    freed += _remove(REMOTION_PUBLIC / jd.name, failed)
    return {"freed": freed, "failed": failed}


def delete_job(jd: Path) -> dict[str, Any]:
    failed: list[str] = []
    freed = _remove(REMOTION_PUBLIC / jd.name, failed)
    freed += _remove(jd, failed)
    return {"freed": freed, "failed": failed}


def rerender_after_free(jd: Path) -> bool:
    """Whether clips can still be re-rendered after free_space: stream jobs fetch the intervals again (stream.json
    stays), local files and live recordings can't."""
    return (jd / "stream.json").exists()
