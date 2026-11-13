"""Frames for Claude to look at, webcam-layout detection (streams) and face tracking (podcasts)."""
from __future__ import annotations

import subprocess
from pathlib import Path

import numpy as np

from .common import fmt_ts, layouts_dir, log, read_json, write_json

_CASCADE = None


def _cv2():
    import cv2
    return cv2


def _detector():
    global _CASCADE
    if _CASCADE is None:
        cv2 = _cv2()
        _CASCADE = cv2.CascadeClassifier(cv2.data.haarcascades + "haarcascade_frontalface_default.xml")
    return _CASCADE


def grab_frame(src: Path, t: float, width: int | None = None) -> np.ndarray:
    """Decode a single frame at time t (seconds) as BGR ndarray."""
    vf = ["-vf", f"scale={width}:-2"] if width else []
    proc = subprocess.run(
        ["ffmpeg", "-v", "error", "-ss", f"{t:.3f}", "-i", str(src), "-frames:v", "1", *vf,
         "-f", "image2pipe", "-vcodec", "png", "-"],
        check=True, capture_output=True,
    )
    cv2 = _cv2()
    img = cv2.imdecode(np.frombuffer(proc.stdout, np.uint8), cv2.IMREAD_COLOR)
    if img is None:
        raise RuntimeError(f"could not decode frame at {t}s")
    return img


def detect_faces(img: np.ndarray) -> list[tuple[float, float, float, float]]:
    """Return faces as normalized (x, y, w, h)."""
    cv2 = _cv2()
    h, w = img.shape[:2]
    scale = 960 / w if w > 960 else 1.0
    small = cv2.resize(img, (int(w * scale), int(h * scale))) if scale != 1.0 else img
    gray = cv2.equalizeHist(cv2.cvtColor(small, cv2.COLOR_BGR2GRAY))
    sh, sw = gray.shape[:2]
    faces = _detector().detectMultiScale(gray, scaleFactor=1.1, minNeighbors=6,
                                         minSize=(max(24, sw // 40), max(24, sw // 40)))
    return [(x / sw, y / sh, fw / sw, fh / sh) for (x, y, fw, fh) in faces]


def draw_grid(img: np.ndarray, boxes: dict[str, dict] | None = None) -> np.ndarray:
    """Overlay a labeled 10% grid (and optional named boxes) so Claude can read coordinates."""
    cv2 = _cv2()
    out = img.copy()
    h, w = out.shape[:2]
    for i in range(1, 10):
        x, y = int(w * i / 10), int(h * i / 10)
        cv2.line(out, (x, 0), (x, h), (255, 255, 0), 1)
        cv2.line(out, (0, y), (w, y), (255, 255, 0), 1)
        cv2.putText(out, f"{i*10}", (x + 2, 14), cv2.FONT_HERSHEY_SIMPLEX, 0.4, (255, 255, 0), 1)
        cv2.putText(out, f"{i*10}", (2, y - 3), cv2.FONT_HERSHEY_SIMPLEX, 0.4, (255, 255, 0), 1)
    colors = [(0, 0, 255), (0, 255, 0), (255, 0, 255)]
    for k, (name, b) in enumerate((boxes or {}).items()):
        p1 = (int(b["x"] * w), int(b["y"] * h))
        p2 = (int((b["x"] + b["w"]) * w), int((b["y"] + b["h"]) * h))
        cv2.rectangle(out, p1, p2, colors[k % 3], 2)
        cv2.putText(out, name, (p1[0] + 4, p1[1] + 18), cv2.FONT_HERSHEY_SIMPLEX, 0.6, colors[k % 3], 2)
    return out


def extract_frames(jd: Path, times: list[float], grid: bool = False, width: int = 640) -> list[Path]:
    cv2 = _cv2()
    out_dir = jd / "frames"
    out_dir.mkdir(exist_ok=True)
    paths = []
    for t in times:
        img = grab_frame(jd / "source.mp4", t, width)
        if grid:
            img = draw_grid(img)
        p = out_dir / f"f_{int(t*10):07d}{'_grid' if grid else ''}.jpg"
        cv2.imwrite(str(p), img, [cv2.IMWRITE_JPEG_QUALITY, 80])
        paths.append(p)
    for p, t in zip(paths, times):
        print(f"{fmt_ts(t)}  {p}")
    return paths


# ------------------------------------------------------------------ stream webcam layout

def layout_key(meta: dict) -> str:
    return f"{meta.get('platform', 'local')}_{meta.get('channel', 'unknown')}".replace("/", "_").replace(" ", "_")


def layout_path(meta: dict) -> Path:
    return layouts_dir() / f"{layout_key(meta)}.json"


def detect_layout(jd: Path) -> dict:
    """Guess the webcam box from faces found across ~16 frames. Claude verifies the drawn result."""
    cv2 = _cv2()
    meta = read_json(jd / "meta.json")
    dur = float(meta["duration"])
    times = list(np.linspace(dur * 0.05, dur * 0.95, 16))
    found = []
    for t in times:
        try:
            faces = detect_faces(grab_frame(jd / "source.mp4", t, 960))
        except Exception:
            continue
        if faces:
            found.append(max(faces, key=lambda f: f[2] * f[3]))
    proposal: dict = {"game": {"x": 0.0, "y": 0.0, "w": 1.0, "h": 1.0}}
    if found:
        arr = np.array(found)
        # most frequent location: keep faces whose center is close to the median center
        centers = arr[:, :2] + arr[:, 2:] / 2
        med = np.median(centers, axis=0)
        keep = arr[np.linalg.norm(centers - med, axis=1) < 0.08]
        if len(keep) == 0:
            keep = arr
        fx, fy, fw, fh = np.median(keep, axis=0)
        cx, cy = fx + fw / 2, fy + fh / 2
        cw, ch = min(fw * 3.2, 0.5), min(fh * 3.0, 0.6)  # typical cam overlay around a face
        x = float(np.clip(cx - cw / 2, 0, 1 - cw))
        y = float(np.clip(cy - ch / 2, 0, 1 - ch))
        proposal["cam"] = {"x": round(x, 3), "y": round(y, 3), "w": round(float(cw), 3), "h": round(float(ch), 3)}
        proposal["faces_found"] = len(found)
    else:
        proposal["cam"] = None
        proposal["faces_found"] = 0
    write_json(jd / "layout_proposal.json", proposal)

    out_dir = jd / "frames"
    out_dir.mkdir(exist_ok=True)
    boxes = {"cam": proposal["cam"]} if proposal["cam"] else {}
    for i, t in enumerate([times[3], times[8], times[13]]):
        img = draw_grid(grab_frame(jd / "source.mp4", t, 960), boxes)
        p = out_dir / f"layout_{i}.jpg"
        cv2.imwrite(str(p), img, [cv2.IMWRITE_JPEG_QUALITY, 80])
        print(p)
    print("proposal:", proposal)
    print("saved preset would be:", layout_path(meta))
    return proposal


def save_layout(jd: Path, cam: list[float] | None, game: list[float] | None) -> Path:
    meta = read_json(jd / "meta.json")
    to_box = lambda v: {"x": v[0], "y": v[1], "w": v[2], "h": v[3]}
    data = {"cam": to_box(cam) if cam else None,
            "game": to_box(game) if game else {"x": 0.0, "y": 0.0, "w": 1.0, "h": 1.0},
            "source_size": [meta.get("width"), meta.get("height")]}
    p = layout_path(meta)
    write_json(p, data)
    # draw for a final visual check
    cv2 = _cv2()
    dur = float(meta["duration"])
    img = draw_grid(grab_frame(jd / "source.mp4", dur * 0.4, 960),
                    {k: v for k, v in {"cam": data["cam"], "game": data["game"]}.items() if v})
    check = jd / "frames" / "layout_saved.jpg"
    check.parent.mkdir(exist_ok=True)
    cv2.imwrite(str(check), img)
    log(f"Saved layout preset {p} (check image: {check})")
    return p


def load_layout(meta: dict) -> dict | None:
    p = layout_path(meta)
    return read_json(p) if p.exists() else None


# ------------------------------------------------------------------ podcast face tracking

def face_track(src: Path, start: float, end: float, step: float = 0.5) -> list[dict]:
    """Sample faces in [start, end]; returns [{t (relative), faces: [(x,y,w,h), ...]}]."""
    track = []
    t = start
    while t < end:
        try:
            faces = detect_faces(grab_frame(src, t, 960))
        except Exception:
            faces = []
        faces = sorted(faces, key=lambda f: f[2] * f[3], reverse=True)[:3]
        track.append({"t": round(t - start, 2), "faces": faces})
        t += step
    return track


def follow_keyframes(track: list[dict], deadzone: float = 0.06, alpha: float = 0.35) -> list[dict]:
    """Smoothed horizontal crop center that follows the biggest face (with a dead zone)."""
    keys, cur = [], None
    for s in track:
        if s["faces"]:
            x, y, w, h = s["faces"][0]
            target = x + w / 2
        else:
            target = cur if cur is not None else 0.5
        if cur is None:
            cur = target
        elif abs(target - cur) > deadzone:
            cur = cur + alpha * (target - cur)
        keys.append({"t": s["t"], "cx": round(float(cur), 4)})
    return keys or [{"t": 0, "cx": 0.5}]


def stack_boxes(track: list[dict]) -> list[dict] | None:
    """For a 2-person shot: median boxes of the left and right person (top/bottom in the output)."""
    lefts, rights = [], []
    for s in track:
        if len(s["faces"]) >= 2:
            a, b = sorted(s["faces"][:2], key=lambda f: f[0])
            lefts.append(a)
            rights.append(b)
    if len(lefts) < max(3, len(track) // 4):
        return None
    out = []
    for arr in (np.array(lefts), np.array(rights)):
        x, y, w, h = np.median(arr, axis=0)
        cx, cy = x + w / 2, y + h / 2
        bw, bh = min(w * 3.0, 0.5), min(h * 2.6, 1.0)
        out.append({"x": round(float(np.clip(cx - bw / 2, 0, 1 - bw)), 4),
                    "y": round(float(np.clip(cy - bh / 2, 0, 1 - bh)), 4),
                    "w": round(float(bw), 4), "h": round(float(bh), 4)})
    return out
