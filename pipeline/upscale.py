"""AI upscaling (Real-ESRGAN) for regions that get blown up a lot, in practice the webcam.

Uses Real-ESRGAN's `realesr-general-x4v3`: a small network made for real-world video (compression blocks, noise,
soft webcams), run with the PyTorch that WhisperX already installed. No extra dependencies.
Frames stream ffmpeg (crop, decode) -> model (x4) -> ffmpeg (Lanczos to the panel size, encode), no temp images.

Model and weights: Real-ESRGAN by Xintao Wang et al., BSD-3-Clause, https://github.com/xinntao/Real-ESRGAN
"""
from __future__ import annotations

import hashlib
import subprocess
import urllib.request
from functools import lru_cache
from pathlib import Path

import numpy as np

from .common import CFG, ROOT, ffprobe, log, progress

MODELS_DIR = ROOT / "models"
_RELEASE = "https://github.com/xinntao/Real-ESRGAN/releases/download/v0.2.5.0/"
WEIGHTS = {  # file -> sha256 of the official v0.2.5.0 release asset (checked after download)
    "realesr-general-x4v3.pth": "8dc7edb9ac80ccdc30c3a5dca6616509367f05fbc184ad95b731f05bece96292",
    # "weak denoise" twin; blended with the above by ai_denoise
    "realesr-general-wdn-x4v3.pth": "1641f8c4464b9f097c9fdda5589273713f67cf59f3d909e0bd688f0cee269dca",
}
SCALE = 4


def _torch():
    import torch
    return torch


def device() -> str | None:
    """'cuda' / 'mps' / 'cpu' when AI upscaling should run, None when it is off (config: render.ai_upscale)."""
    mode = str(CFG["render"].get("ai_upscale", "auto")).lower()
    if mode in ("off", "false", "0", "no"):
        return None
    try:
        torch = _torch()
    except ImportError:
        return None
    if torch.cuda.is_available():
        return "cuda"
    if getattr(torch.backends, "mps", None) and torch.backends.mps.is_available():
        return "mps"
    return "cpu" if mode in ("on", "true", "1", "yes") else None  # CPU works but is slow: only when forced


def _weights(name: str) -> Path:
    path = MODELS_DIR / name
    if not path.exists():
        MODELS_DIR.mkdir(parents=True, exist_ok=True)
        log(f"Downloading AI upscaling weights {name} (one time, ~5 MB)")
        tmp = path.with_suffix(".part")
        urllib.request.urlretrieve(_RELEASE + name, tmp)
        digest = hashlib.sha256(tmp.read_bytes()).hexdigest()
        if digest != WEIGHTS[name]:
            tmp.unlink()
            raise RuntimeError(f"{name}: checksum mismatch ({digest}), refusing to use it")
        tmp.replace(path)
    return path


def _build_net():
    torch = _torch()
    nn, F = torch.nn, torch.nn.functional

    class SRVGGNetCompact(nn.Module):
        """Real-ESRGAN's compact VGG-style SR network (num_feat=64, num_conv=32, PReLU, pixel shuffle x4)."""

        def __init__(self, num_feat: int = 64, num_conv: int = 32, upscale: int = SCALE):
            super().__init__()
            self.upscale = upscale
            body = [nn.Conv2d(3, num_feat, 3, 1, 1), nn.PReLU(num_parameters=num_feat)]
            for _ in range(num_conv):
                body += [nn.Conv2d(num_feat, num_feat, 3, 1, 1), nn.PReLU(num_parameters=num_feat)]
            body.append(nn.Conv2d(num_feat, 3 * upscale * upscale, 3, 1, 1))
            self.body = nn.ModuleList(body)
            self.upsampler = nn.PixelShuffle(upscale)

        def forward(self, x):
            out = x
            for layer in self.body:
                out = layer(out)
            return self.upsampler(out) + F.interpolate(x, scale_factor=self.upscale, mode="nearest")

    return SRVGGNetCompact()


@lru_cache(maxsize=2)
def _model(dev: str, denoise: float):
    torch = _torch()

    def load(name: str) -> dict:
        sd = torch.load(_weights(name), map_location="cpu", weights_only=True)  # tensors only, no pickled code
        return sd.get("params_ema") or sd.get("params") or sd

    sharp = load("realesr-general-x4v3.pth")
    if denoise < 1.0:  # denoise 1 = the general model, 0 = the weak-denoise model (keeps more texture / grain)
        weak = load("realesr-general-wdn-x4v3.pth")
        sharp = {k: denoise * v + (1 - denoise) * weak[k] for k, v in sharp.items()}
    net = _build_net()
    net.load_state_dict(sharp, strict=True)
    net.eval().to(dev)
    if dev == "cuda":
        net.half()
    return net


def render_panel(seg_file: Path, crop: tuple[int, int, int, int], out_w: int, out_h: int, out: Path,
                 fps: int, until: float | None = None) -> None:
    """crop = (x, y, w, h) in source px. Writes `out`: that region, AI-upscaled x4, then resized to out_w x out_h."""
    torch = _torch()
    dev = device() or "cpu"
    net = _model(dev, float(CFG["render"].get("ai_denoise", 0.5)))
    x, y, w, h = crop
    W4, H4 = w * SCALE, h * SCALE
    limit = ["-t", f"{until:.3f}"] if until else []
    dec = subprocess.Popen(["ffmpeg", "-v", "error", "-i", str(seg_file), *limit,
                            "-vf", f"crop={w}:{h}:{x}:{y}:exact=1", "-f", "rawvideo", "-pix_fmt", "rgb24", "-"],
                           stdout=subprocess.PIPE)
    enc = subprocess.Popen(["ffmpeg", "-y", "-v", "error", "-f", "rawvideo", "-pix_fmt", "rgb24",
                            "-s", f"{W4}x{H4}", "-r", str(fps), "-i", "-",
                            "-vf", f"scale={out_w}:{out_h}:flags=lanczos+accurate_rnd+full_chroma_int,format=yuv420p",
                            "-c:v", "libx264", "-preset", "medium", "-crf", "8", "-movflags", "+faststart", str(out)],
                           stdin=subprocess.PIPE)
    frame_bytes, batch = w * h * 3, (4 if dev == "cuda" else 1)
    dtype = torch.float16 if dev == "cuda" else torch.float32
    n, total = 0, max(1, round((until or ffprobe(seg_file)["duration"]) * fps))
    last_report = 0
    try:
        with torch.inference_mode():
            while True:
                frames = []
                for _ in range(batch):
                    buf = dec.stdout.read(frame_bytes)
                    if len(buf) < frame_bytes:
                        break
                    frames.append(np.frombuffer(buf, np.uint8).reshape(h, w, 3))
                if not frames:
                    break
                t = torch.from_numpy(np.stack(frames)).to(dev).permute(0, 3, 1, 2).to(dtype).div_(255)
                up = net(t).clamp_(0, 1).mul_(255).round_().to(torch.uint8).permute(0, 2, 3, 1).cpu().numpy()
                enc.stdin.write(np.ascontiguousarray(up).tobytes())
                n += len(frames)
                if n - last_report >= 30:
                    last_report = n
                    progress("render", min(99.0, 100 * n / total), f"AI upscaling the webcam ({n}/{total})", step="ai")
    finally:
        enc.stdin.close()
        dec.stdout.close()
        rc_dec, rc_enc = dec.wait(), enc.wait()
        if dev == "cuda":
            torch.cuda.empty_cache()
    if rc_dec or rc_enc or n == 0:
        raise subprocess.CalledProcessError(rc_dec or rc_enc or 1, "ffmpeg (AI upscale pipe)")
