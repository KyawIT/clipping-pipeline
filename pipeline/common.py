"""Shared helpers: config, paths, subprocess, ffprobe, time formatting."""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Any

import yaml

ROOT = Path(__file__).resolve().parent.parent
IS_WINDOWS = os.name == "nt"
# venv layout differs: .venv/bin/python on Linux/macOS, .venv\Scripts\python.exe on Windows
VENV_BIN = ROOT / ".venv" / ("Scripts" if IS_WINDOWS else "bin")
SETUP_HINT = "Run setup.cmd" if IS_WINDOWS else "Run ./setup.sh"


def load_config() -> dict[str, Any]:
    with open(ROOT / "config.yaml", "r", encoding="utf-8") as f:
        return yaml.safe_load(f)


CFG = load_config()


def jobs_dir() -> Path:
    # CLIP_JOBS_DIR / CLIP_LAYOUTS_DIR override config.yaml (handy for a separate test library)
    p = ROOT / os.environ.get("CLIP_JOBS_DIR", CFG["jobs_dir"])
    p.mkdir(parents=True, exist_ok=True)
    return p


def layouts_dir() -> Path:
    p = ROOT / os.environ.get("CLIP_LAYOUTS_DIR", CFG["layouts_dir"])
    p.mkdir(parents=True, exist_ok=True)
    return p


def progress(stage: str, pct: float | None = None, msg: str | None = None, **data: Any) -> None:
    """Machine-readable progress for the web app: one `@@{json}` line on stdout when CLIP_PROGRESS=1.
    Silent in normal CLI use."""
    if os.environ.get("CLIP_PROGRESS") != "1":
        return
    ev: dict[str, Any] = {"stage": stage, **data}
    if pct is not None:
        ev["pct"] = round(float(pct), 1)
    if msg:
        ev["msg"] = msg
    print("@@" + json.dumps(ev, ensure_ascii=False), flush=True)


def job_dir(job_id: str) -> Path:
    p = jobs_dir() / job_id
    if not p.exists():
        sys.exit(f"Job not found: {p}")
    return p


def read_json(path: Path) -> Any:
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


def write_json(path: Path, data: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
    tmp.replace(path)


def log(msg: str) -> None:
    print(f"[clip] {msg}", flush=True)


def find_tool(name: str) -> str | None:
    """Find a tool in the venv (next to the running python), ./bin, or PATH.

    shutil.which adds .exe/.cmd (PATHEXT) on Windows and checks the executable bit elsewhere.
    """
    for d in (Path(sys.executable).parent, VENV_BIN, ROOT / "bin"):
        if d.is_dir():
            path = shutil.which(name, path=str(d))
            if path:
                return path
    return shutil.which(name)


def which_or_die(name: str, hint: str = "") -> str:
    path = find_tool(name)
    if not path:
        sys.exit(f"Missing dependency: {name}. {hint or SETUP_HINT}".strip())
    return path


def run(cmd: list[str], **kw) -> subprocess.CompletedProcess:
    log("$ " + " ".join(str(c) for c in cmd))
    return subprocess.run([str(c) for c in cmd], check=True, **kw)


def ffmpeg_headers(headers: dict[str, str] | None) -> list[str]:
    """HTTP headers (from yt-dlp) as ffmpeg/ffprobe input options."""
    return ["-headers", "".join(f"{k}: {v}\r\n" for k, v in headers.items())] if headers else []


def link_or_copy(src: Path, target: Path, symlink: bool = True) -> None:
    """Symlink (needs admin / developer mode on Windows), else hardlink (same drive), else copy."""
    if symlink:
        try:
            target.symlink_to(src)
            return
        except OSError:
            pass
    try:
        os.link(src, target)
        return
    except OSError:
        pass
    log(f"Copying {src.name} -> {target.parent}")
    shutil.copy2(src, target)


def ffprobe(path: Path | str, headers: dict[str, str] | None = None) -> dict[str, Any]:
    """Duration, size and fps of a local file or a stream URL."""
    out = subprocess.run(
        ["ffprobe", "-v", "error", "-print_format", "json", "-show_format", "-show_streams",
         *ffmpeg_headers(headers), str(path)],
        check=True, capture_output=True, text=True, encoding="utf-8", errors="replace",
    ).stdout
    data = json.loads(out)
    v = next((s for s in data["streams"] if s.get("codec_type") == "video"), None)
    info: dict[str, Any] = {"duration": float(data["format"].get("duration", 0.0))}
    if v:
        info["width"] = int(v["width"])
        info["height"] = int(v["height"])
        num, den = (v.get("avg_frame_rate") or "30/1").split("/")
        info["fps"] = float(num) / float(den) if float(den) else 30.0
    return info


def fmt_ts(sec: float) -> str:
    sec = max(0, int(round(sec)))
    h, rem = divmod(sec, 3600)
    m, s = divmod(rem, 60)
    return f"{h}:{m:02d}:{s:02d}" if h else f"{m}:{s:02d}"


def parse_ts(value: str | float | int) -> float:
    """Accept seconds (123.4) or timestamps (1:02:03.5 / 2:03)."""
    if isinstance(value, (int, float)):
        return float(value)
    parts = str(value).strip().split(":")
    total = 0.0
    for p in parts:
        total = total * 60 + float(p)
    return total


def env_flag(name: str) -> bool:
    return os.environ.get(name, "").lower() in {"1", "true", "yes"}
