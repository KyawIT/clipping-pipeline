"""Steps 5-7: validate clips.json, cut segments, build Remotion props, render 9:16 videos."""
from __future__ import annotations

import os
import shutil
import subprocess
import sys
from pathlib import Path

from .common import CFG, ROOT, fmt_ts, log, parse_ts, read_json, run, write_json
from . import vision

LAYOUTS = {"split", "fullcam", "fullgame", "podcast_follow", "podcast_stack", "center"}
REMOTION = ROOT / "remotion"


# ------------------------------------------------------------------ validation

def load_clips(jd: Path) -> list[dict]:
    p = jd / "clips.json"
    if not p.exists():
        sys.exit(f"{p} not found. Run /clip in Claude Code first.")
    data = read_json(p)
    return data["clips"] if isinstance(data, dict) else data


def validate(jd: Path) -> list[str]:
    meta = read_json(jd / "meta.json")
    clips = load_clips(jd)
    c = CFG["clips"]
    errors, spans = [], []
    has_layout = vision.load_layout(meta) is not None
    for i, clip in enumerate(clips):
        cid = clip.get("id") or f"#{i+1}"
        try:
            s, e = parse_ts(clip["start"]), parse_ts(clip["end"])
        except Exception:
            errors.append(f"{cid}: start/end missing or unreadable")
            continue
        d = e - s
        if d < c["min_sec"] or d > c["max_sec"]:
            errors.append(f"{cid}: duration {d:.1f}s outside {c['min_sec']}-{c['max_sec']}s")
        if s < 0 or e > meta["duration"] + 0.5:
            errors.append(f"{cid}: outside video (0 - {meta['duration']:.0f}s)")
        layout = clip.get("layout", "split")
        if layout not in LAYOUTS:
            errors.append(f"{cid}: unknown layout '{layout}' (use one of {sorted(LAYOUTS)})")
        if layout in {"split", "fullcam"} and not has_layout:
            errors.append(f"{cid}: layout '{layout}' needs a webcam preset -> run layout-detect / layout-save")
        if not clip.get("hook"):
            errors.append(f"{cid}: missing hook text")
        tl = clip.get("thumb_layout")
        if tl and (tl not in LAYOUTS or (tl in {"split", "fullcam"} and not has_layout)):
            errors.append(f"{cid}: thumb_layout '{tl}' unknown or needs a webcam preset")
        if "thumb_at" in clip and not s <= parse_ts(clip["thumb_at"]) <= e:
            errors.append(f"{cid}: thumb_at is outside the clip")
        spans.append((s, e, cid))
    spans.sort()
    for (s1, e1, a), (s2, e2, b) in zip(spans, spans[1:]):
        if s2 < e1 - 1:
            errors.append(f"{a} and {b} overlap")
    if errors:
        print("INVALID clips.json:")
        for err in errors:
            print("  -", err)
    else:
        total = sum(e - s for s, e, _ in spans)
        print(f"OK: {len(clips)} clips, {total:.0f}s total")
    return errors


# ------------------------------------------------------------------ captions

def caption_pages(words: list[dict], start: float, end: float, per_page: int) -> list[dict]:
    """Group words into short pages (TikTok style). Times are relative to the clip start."""
    ws = [w for w in words if w["e"] > start and w["s"] < end]
    pages, cur = [], []

    def flush():
        if cur:
            pages.append({"start": cur[0]["s"], "end": cur[-1]["e"], "words": list(cur)})
            cur.clear()

    for w in ws:
        item = {"t": w["w"], "s": round(max(0.0, w["s"] - start), 3), "e": round(min(end, w["e"]) - start, 3)}
        if cur and (len(cur) >= per_page or item["s"] - cur[-1]["e"] > 0.6 or item["e"] - cur[0]["s"] > 1.8):
            flush()
        cur.append(item)
        if item["t"].rstrip().endswith((".", "?", "!")):
            flush()
    flush()
    # keep each page on screen until the next one starts (no flicker in short gaps)
    for a, b in zip(pages, pages[1:]):
        if b["start"] - a["end"] < 0.4:
            a["end"] = b["start"]
    return pages


# ------------------------------------------------------------------ rendering

def cut_segment(src: Path, start: float, end: float, out: Path, fps: int) -> None:
    out.parent.mkdir(parents=True, exist_ok=True)
    run(["ffmpeg", "-y", "-loglevel", "error", "-ss", f"{start:.3f}", "-i", src, "-t", f"{end - start:.3f}",
         "-r", str(fps), "-c:v", "libx264", "-preset", "veryfast", "-crf", "16",
         "-c:a", "aac", "-b:a", "192k", "-movflags", "+faststart", out])


def build_props(jd: Path, job_id: str, clip: dict, meta: dict, words: list[dict], layout_preset: dict | None,
                public_rel: str) -> dict:
    r = CFG["render"]
    s, e = parse_ts(clip["start"]), parse_ts(clip["end"])
    layout = clip.get("layout") or ("split" if meta.get("mode") == "stream" else "podcast_follow")
    props = {
        "src": public_rel,
        "durationSec": round(e - s, 3),
        "fps": r["fps"],
        "width": r["width"],
        "height": r["height"],
        "sourceWidth": meta["width"],
        "sourceHeight": meta["height"],
        "layout": layout,
        "hook": clip.get("hook", ""),
        "hookSeconds": r["hook_seconds"],
        "splitCamRatio": r["split_cam_ratio"],
        "captions": caption_pages(words, s, e, int(r["caption_words_per_page"])),
        "cam": (layout_preset or {}).get("cam"),
        "game": clip.get("game_box") or (layout_preset or {}).get("game"),
        "follow": None,
        "stack": None,
        "emphasis": clip.get("emphasis", []),
    }
    if layout in {"podcast_follow", "podcast_stack"}:
        log(f"  tracking faces for {clip.get('id')}")
        track = vision.face_track(jd / "source.mp4", s, e)
        props["follow"] = vision.follow_keyframes(track)
        if layout == "podcast_stack":
            props["stack"] = vision.stack_boxes(track)
            if not props["stack"]:
                log("  not enough 2-face frames -> falling back to podcast_follow")
                props["layout"] = "podcast_follow"
    return props


def remotion_cmd(npx: str, *args: str) -> list[str]:
    cmd = [npx, "remotion", *args]
    if os.environ.get("REMOTION_BROWSER"):  # optional: use an existing Chrome/headless-shell
        cmd.append(f"--browser-executable={os.environ['REMOTION_BROWSER']}")
    return cmd


def render_thumb(jd: Path, npx: str, cid: str, clip: dict, props: dict) -> None:
    """Thumbnail = the clip composition at `thumb_at` with the thumbnail overlay instead of hook + captions."""
    s, e = parse_ts(clip["start"]), parse_ts(clip["end"])
    at = parse_ts(clip.get("thumb_at", s + 0.35 * (e - s)))
    last = max(0, int((e - s) * props["fps"]) - 1)
    frame = min(last, max(0, round((at - s) * props["fps"])))
    # faces sell thumbnails: a split clip gets the full webcam unless thumb_layout says otherwise
    layout = clip.get("thumb_layout") or ("fullcam" if props["layout"] == "split" and props.get("cam") else props["layout"])
    tp = {**props, "thumbText": clip["thumb_text"], "captions": [], "layout": layout}
    tp_path = jd / "props" / f"{cid}_thumb.json"
    write_json(tp_path, tp)
    out = jd / "out" / f"{cid}_thumb.jpg"
    subprocess.run(remotion_cmd(npx, "still", "src/index.ts", "Clip", str(out), f"--props={tp_path}",
                                f"--frame={frame}", "--image-format=jpeg", "--jpeg-quality=92"),
                   cwd=REMOTION, check=True)
    log(f"  -> {out}")


def render(jd: Path, job_id: str, only: list[str] | None = None, thumbs_only: bool = False) -> None:
    if validate(jd):
        sys.exit(1)
    if not (REMOTION / "node_modules").exists():
        sys.exit("Remotion not installed. Run ./setup.sh")
    meta = read_json(jd / "meta.json")
    words = read_json(jd / "transcript.json")["words"]
    preset = vision.load_layout(meta)
    clips = load_clips(jd)
    r = CFG["render"]
    out_dir = jd / "out"
    out_dir.mkdir(exist_ok=True)
    npx = shutil.which("npx") or sys.exit("npx not found (install Node.js 18+)")

    for i, clip in enumerate(clips, 1):
        cid = clip.get("id") or f"clip_{i:02d}"
        if only and cid not in only:
            continue
        s, e = parse_ts(clip["start"]), parse_ts(clip["end"])
        log(f"Rendering {cid}: {fmt_ts(s)} - {fmt_ts(e)} [{clip.get('layout')}]")
        public_rel = f"jobs/{job_id}/{cid}.mp4"
        seg = REMOTION / "public" / public_rel
        cut_segment(jd / "source.mp4", s, e, seg, r["fps"])
        props_path = jd / "props" / f"{cid}.json"
        if thumbs_only and props_path.exists():
            props = read_json(props_path)  # skips face tracking again
        else:
            props = build_props(jd, job_id, clip, meta, words, preset, public_rel)
            write_json(props_path, props)
        if not thumbs_only:
            out = out_dir / f"{cid}.mp4"
            subprocess.run(remotion_cmd(npx, "render", "src/index.ts", "Clip", str(out),
                                        f"--props={props_path}", "--crf", str(r["crf"])), cwd=REMOTION, check=True)
            (out_dir / f"{cid}.txt").write_text(
                f"{clip.get('title', '')}\n\n{clip.get('description', '')}\n\n"
                f"{' '.join(clip.get('hashtags', []))}\n\nsource: {meta.get('source')} @ {fmt_ts(s)}\n",
                encoding="utf-8")
            log(f"  -> {out}")
        if clip.get("thumb_text"):
            render_thumb(jd, npx, cid, clip, props)
        seg.unlink(missing_ok=True)
    log(f"Done. Clips in {out_dir}")
