#!/usr/bin/env python3
"""Clipping pipeline CLI.

  python clip.py prepare <url-or-file> [--mode stream|podcast]   download + transcribe + score (no Claude usage)
  python clip.py status  <job>                                    what is done for a job
  python clip.py words   <job> --from 1:02:10 --to 1:02:40        word-level timestamps for exact cuts
  python clip.py frames  <job> --at 1:02:15,1:02:30 [--grid]      small JPGs for Claude to look at
  python clip.py layout-detect <job>                              propose the webcam box (streams)
  python clip.py layout-save   <job> --cam x,y,w,h [--game x,y,w,h]  save the streamer's layout preset
  python clip.py validate <job>                                   check clips.json
  python clip.py render   <job> [--only clip_01,clip_03] [--thumbs]  render the 9:16 clips + thumbnails
"""
from __future__ import annotations

import argparse
import sys

from pipeline.common import fmt_ts, job_dir, jobs_dir, log, parse_ts, read_json


def _floats(text: str) -> list[float]:
    vals = [float(v) for v in text.split(",")]
    if len(vals) != 4 or not all(0 <= v <= 1 for v in vals):
        raise argparse.ArgumentTypeError("expected x,y,w,h as fractions 0-1, e.g. 0.70,0.02,0.28,0.30")
    return vals


def cmd_prepare(a):
    from pipeline.download import prepare_job
    from pipeline.transcribe import transcribe
    from pipeline.signals import build_candidates
    from pipeline.vision import load_layout

    job_id, jd = prepare_job(a.source, a.mode)
    log(f"Job: {job_id}")
    transcribe(jd)
    build_candidates(jd)
    meta = read_json(jd / "meta.json")
    print()
    print(f"READY  job={job_id}  mode={meta['mode']}  duration={fmt_ts(meta['duration'])}")
    if meta["mode"] == "stream" and not load_layout(meta):
        print("No webcam layout preset for this channel yet: /clip will detect it on the first run.")
    print(f"Next: open Claude Code in this folder and run:  /clip {job_id}")


def cmd_status(a):
    from pipeline.vision import layout_path
    jd = job_dir(a.job)
    meta = read_json(jd / "meta.json")
    print(f"job: {a.job}  title: {meta.get('title')}  mode: {meta.get('mode')}  duration: {fmt_ts(meta['duration'])}")
    for name in ["source.mp4", "audio.wav", "chat.json", "transcript.json", "candidates.md", "clips.json"]:
        print(f"  [{'x' if (jd / name).exists() else ' '}] {name}")
    lp = layout_path(meta)
    print(f"  [{'x' if lp.exists() else ' '}] layout preset {lp.name}")
    outs = sorted((jd / "out").glob("*.mp4")) if (jd / "out").exists() else []
    print(f"  rendered clips: {len(outs)}")


def cmd_words(a):
    jd = job_dir(a.job)
    words = read_json(jd / "transcript.json")["words"]
    s, e = parse_ts(a.from_), parse_ts(a.to)
    line, line_start = [], None
    for w in words:
        if w["e"] < s or w["s"] > e:
            continue
        if line_start is None:
            line_start = w["s"]
        line.append(f"{w['w']}@{w['s']:.1f}")
        if len(line) >= 12 or w["w"].endswith((".", "?", "!")):
            print(f"[{fmt_ts(line_start)}] " + " ".join(line))
            line, line_start = [], None
    if line:
        print(f"[{fmt_ts(line_start)}] " + " ".join(line))


def cmd_frames(a):
    from pipeline.vision import extract_frames
    times = [parse_ts(t) for t in a.at.split(",")]
    extract_frames(job_dir(a.job), times, grid=a.grid, width=a.width)


def cmd_layout_detect(a):
    from pipeline.vision import detect_layout
    detect_layout(job_dir(a.job))


def cmd_layout_save(a):
    from pipeline.vision import save_layout
    save_layout(job_dir(a.job), a.cam, a.game)


def cmd_validate(a):
    from pipeline.render import validate
    sys.exit(1 if validate(job_dir(a.job)) else 0)


def cmd_render(a):
    from pipeline.render import render
    only = a.only.split(",") if a.only else None
    render(job_dir(a.job), a.job, only, thumbs_only=a.thumbs)


def cmd_list(_a):
    for p in sorted(jobs_dir().iterdir()):
        if (p / "meta.json").exists():
            m = read_json(p / "meta.json")
            print(f"{p.name:40s} {m.get('mode', ''):8s} {fmt_ts(m.get('duration', 0)):>8s}  {m.get('title', '')}")


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)

    p = sub.add_parser("prepare"); p.add_argument("source"); p.add_argument("--mode", choices=["stream", "podcast"])
    p.set_defaults(fn=cmd_prepare)
    p = sub.add_parser("status"); p.add_argument("job"); p.set_defaults(fn=cmd_status)
    p = sub.add_parser("list"); p.set_defaults(fn=cmd_list)
    p = sub.add_parser("words"); p.add_argument("job"); p.add_argument("--from", dest="from_", required=True)
    p.add_argument("--to", required=True); p.set_defaults(fn=cmd_words)
    p = sub.add_parser("frames"); p.add_argument("job"); p.add_argument("--at", required=True)
    p.add_argument("--grid", action="store_true"); p.add_argument("--width", type=int, default=640)
    p.set_defaults(fn=cmd_frames)
    p = sub.add_parser("layout-detect"); p.add_argument("job"); p.set_defaults(fn=cmd_layout_detect)
    p = sub.add_parser("layout-save"); p.add_argument("job"); p.add_argument("--cam", type=_floats)
    p.add_argument("--game", type=_floats); p.set_defaults(fn=cmd_layout_save)
    p = sub.add_parser("validate"); p.add_argument("job"); p.set_defaults(fn=cmd_validate)
    p = sub.add_parser("render"); p.add_argument("job"); p.add_argument("--only")
    p.add_argument("--thumbs", action="store_true", help="only (re)render thumbnails"); p.set_defaults(fn=cmd_render)

    a = ap.parse_args()
    a.fn(a)


if __name__ == "__main__":
    main()
