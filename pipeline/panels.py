"""Pre-scaled layout panels: the webcam / gameplay / face crops, cut and scaled by ffmpeg.

Without this, Remotion crops the source in Chrome and upscales it with plain bilinear filtering: a webcam box of
~576x324 px stretched to 1080x730 looks soft and blocky. Here ffmpeg crops each region, cleans compression noise,
upscales with Lanczos and sharpens, at exactly the size the layout shows it. Remotion then draws the panel 1:1.

The geometry mirrors remotion/src/Clip.tsx and CropView.tsx (cover fit); keep them in sync.
"""
from __future__ import annotations

import subprocess
from functools import lru_cache
from pathlib import Path

from . import upscale
from .common import CFG, gpu_lock, log

FULL = {"x": 0.0, "y": 0.0, "w": 1.0, "h": 1.0}
# Real-ESRGAN (when enabled) is for small regions blown up a lot: the webcam box, a fullcam crop. Big crops such as
# a whole podcast frame only grow ~1.8x and would cost minutes per clip (AI time scales with the input pixels),
# so they keep Lanczos + sharpen.
AI_MIN_UPSCALE = 1.4
AI_MAX_INPUT_PX = 400_000


@lru_cache(maxsize=1)
def _filters() -> set[str]:
    out = subprocess.run(["ffmpeg", "-hide_banner", "-filters"], capture_output=True, text=True).stdout
    return {line.split()[1] for line in out.splitlines() if len(line.split()) > 1}


def _cover_rect(box: dict, sw: int, sh: int, ow: int, oh: int) -> tuple[float, float, float, float]:
    """Source rectangle (px) that a cover-fit view of `box` at ow x oh shows (CropView.tsx, fit=cover)."""
    bw, bh = max(1.0, box["w"] * sw), max(1.0, box["h"] * sh)
    s = max(ow / bw, oh / bh)
    vw, vh = ow / s, oh / s
    cx, cy = (box["x"] + box["w"] / 2) * sw, (box["y"] + box["h"] / 2) * sh
    x = min(max(cx - vw / 2, 0.0), sw - vw)
    y = min(max(cy - vh / 2, 0.0), sh - vh)
    return x, y, vw, vh


def _vertical_w(sw: int, sh: int, ow: int, oh: int) -> float:
    return min(1.0, (sh * (ow / oh)) / sw)


def specs(props: dict) -> dict[str, dict]:
    """The panels a layout shows: role -> {box | follow, w, h}. Mirrors the switch in Clip.tsx."""
    W, H = props["width"], props["height"]
    sw, sh = props["sourceWidth"], props["sourceHeight"]
    cam, game = props.get("cam") or FULL, props.get("game") or FULL
    layout = props["layout"]
    if layout == "split":
        cam_h = round(H * props["splitCamRatio"])
        return {"cam": {"box": cam, "w": W, "h": cam_h}, "game": {"box": game, "w": W, "h": H - cam_h}}
    if layout == "fullcam":
        return {"cam": {"box": cam, "w": W, "h": H}}
    if layout == "fullgame":
        game_h = round(W * (game["h"] * sh) / (game["w"] * sw))
        out = {"game": {"box": game, "w": W, "h": game_h}}
        if props.get("cam"):
            bubble = round(W * 0.42)
            out["cam"] = {"box": cam, "w": bubble, "h": bubble}
        return out
    if layout == "podcast_stack" and props.get("stack"):
        a, b = props["stack"]
        return {"a": {"box": a, "w": W, "h": H // 2}, "b": {"box": b, "w": W, "h": H // 2}}
    w = _vertical_w(sw, sh, W, H)
    if layout == "podcast_follow" and props.get("follow") and len(props["follow"]) > 1:
        return {"main": {"follow": props["follow"], "boxw": w, "w": W, "h": H}}
    cx = (props.get("follow") or [{"cx": 0.5}])[0]["cx"] if layout == "podcast_follow" else 0.5
    return {"main": {"box": {"x": min(1 - w, max(0.0, cx - w / 2)), "y": 0.0, "w": w, "h": 1.0}, "w": W, "h": H}}


def _clean_and_sharpen(upscale: float) -> tuple[list[str], list[str]]:
    """Filters before / after the Lanczos scale. Only upscaled panels get them (that is where blocks show)."""
    if upscale < 1.1:
        return [], []
    q = CFG["render"].get("panel_sharpen", 0.5)
    f = _filters()
    pre = ["hqdn3d=1.5:1.5:3:3"] if "hqdn3d" in f else []  # smooth compression noise so it isn't sharpened
    amount = min(1.0, q * min(2.0, upscale) / 1.5)
    if "cas" in f:  # contrast-adaptive sharpening: crisp edges without halos
        post = [f"cas=strength={amount:.2f}"]
    elif "unsharp" in f:
        post = [f"unsharp=5:5:{amount:.2f}:5:5:0"]
    else:  # minimal ffmpeg builds: Lanczos alone
        post = []
    return pre, post


def _follow_expr(keys: list[dict], boxw: float, sw: int, scale: float) -> str:
    """ffmpeg expression for the crop x (in scaled px) that interpolates the face-follow keyframes over t."""
    pts = [(k["t"], min(1 - boxw, max(0.0, k["cx"] - boxw / 2)) * sw * scale) for k in keys]
    slim = [pts[0]] + [p for i, p in enumerate(pts[1:-1], 1)
                       if not (abs(p[1] - pts[i - 1][1]) < 0.5 and abs(p[1] - pts[i + 1][1]) < 0.5)] + [pts[-1]]
    expr = f"{slim[-1][1]:.1f}"
    for (t0, x0), (t1, x1) in reversed(list(zip(slim, slim[1:]))):
        seg = f"{x0:.1f}" if abs(x1 - x0) < 0.5 else f"{x0:.1f}+({x1 - x0:.1f})*(t-{t0:.3f})/{max(1e-3, t1 - t0):.3f}"
        expr = f"if(lt(t,{t1:.3f}),{seg},{expr})"
    return f"if(lt(t,{slim[0][0]:.3f}),{slim[0][1]:.1f},{expr})"


def _filter_chain(spec: dict, sw: int, sh: int) -> str:
    W, H = spec["w"], spec["h"]
    scale_flags = "lanczos+accurate_rnd+full_chroma_int"
    if "follow" in spec:  # moving crop: scale the whole frame to output height first, then crop -> smooth pans
        boxw = spec["boxw"]
        s = H / sh
        pre, post = _clean_and_sharpen(s)
        sx = max(W, round(sw * s / 2) * 2)
        chain = pre + [f"scale={sx}:{H}:flags={scale_flags}",
                       f"crop={W}:{H}:'{_follow_expr(spec['follow'], boxw, sw, s)}':0"] + post
    else:
        x, y, w, h = _cover_rect(spec["box"], sw, sh, W, H)
        pre, post = _clean_and_sharpen(W / w)
        chain = [f"crop={w:.0f}:{h:.0f}:{x:.0f}:{y:.0f}:exact=1"] + pre + \
                [f"scale={W}:{H}:flags={scale_flags}"] + post
    return ",".join(chain + ["format=yuv420p"])


def build(seg_file: Path, props: dict, out_dir: Path, prefix: str, public_root: Path,
          until: float | None = None) -> dict[str, dict]:
    """Render every panel of props' layout from the clip segment. -> props["panels"] ({role: {src, w, h}})."""
    sw, sh = props["sourceWidth"], props["sourceHeight"]
    ai = upscale.device()
    panels, notes = {}, []
    for role, spec in specs(props).items():
        out = out_dir / f"{prefix}_{role}.mp4"
        out.parent.mkdir(parents=True, exist_ok=True)
        crop = None if "follow" in spec else _cover_rect(spec["box"], sw, sh, spec["w"], spec["h"])
        if ai and crop and spec["w"] / crop[2] >= AI_MIN_UPSCALE and crop[2] * crop[3] <= AI_MAX_INPUT_PX:
            x, y, w, h = (int(round(v)) for v in crop)
            with gpu_lock(ai == "cuda"):  # never at the same time as a WhisperX run (live transcription)
                upscale.render_panel(seg_file, (x, y, w, h), spec["w"], spec["h"], out, props["fps"], until)
            notes.append(f"{role} {spec['w']}x{spec['h']} (AI x{spec['w'] / w:.1f})")
        else:
            subprocess.run(["ffmpeg", "-y", "-loglevel", "error", "-i", str(seg_file),
                            *(["-t", f"{until:.3f}"] if until else []),
                            "-vf", _filter_chain(spec, sw, sh), "-an",
                            "-c:v", "libx264", "-preset", "medium", "-crf", "8", "-movflags", "+faststart", str(out)],
                           check=True)
            notes.append(f"{role} {spec['w']}x{spec['h']}")
        panels[role] = {"src": out.relative_to(public_root).as_posix(), "w": spec["w"], "h": spec["h"]}
    log("  panels: " + ", ".join(notes))
    return panels
