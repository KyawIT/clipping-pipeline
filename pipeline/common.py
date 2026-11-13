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


def load_config() -> dict[str, Any]:
    with open(ROOT / "config.yaml", "r", encoding="utf-8") as f:
        return yaml.safe_load(f)


CFG = load_config()


def jobs_dir() -> Path:
    p = ROOT / CFG["jobs_dir"]
    p.mkdir(parents=True, exist_ok=True)
    return p


def layouts_dir() -> Path:
    p = ROOT / CFG["layouts_dir"]
    p.mkdir(parents=True, exist_ok=True)
    return p


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


def which_or_die(name: str, hint: str = "") -> str:
    """Find a tool in the venv (next to the running python), ./bin, or PATH."""
    for cand in (Path(sys.executable).parent / name, ROOT / ".venv" / "bin" / name, ROOT / "bin" / name):
        if cand.exists() and os.access(cand, os.X_OK):
            return str(cand)
    path = shutil.which(name)
    if not path:
        sys.exit(f"Missing dependency: {name}. {hint}".strip())
    return path


def run(cmd: list[str], **kw) -> subprocess.CompletedProcess:
    log("$ " + " ".join(str(c) for c in cmd))
    return subprocess.run([str(c) for c in cmd], check=True, **kw)


def ffprobe(path: Path) -> dict[str, Any]:
    out = subprocess.run(
        ["ffprobe", "-v", "error", "-print_format", "json", "-show_format", "-show_streams", str(path)],
        check=True, capture_output=True, text=True,
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
