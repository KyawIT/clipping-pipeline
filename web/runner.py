"""Runs the pipeline for the web app: one run at a time, each stage a subprocess of the normal CLI.

prepare (download audio, transcribe, score)  ->  pick (headless Claude Code, /clip steps 1-6)  ->  review in the
browser  ->  render (clip.py render --only <picked>). Progress comes from `@@{json}` lines (pipeline.common.progress)
plus the tools' own output (yt-dlp %, WhisperX "Progress:", Remotion "Rendered x/y") and Claude's stream-json events.
"""
from __future__ import annotations

import json
import os
import queue
import re
import shutil
import signal
import subprocess
import sys
import threading
import time
import traceback
from pathlib import Path
from typing import Any, Callable, Optional

from pipeline.common import BELOW_NORMAL, CFG, IS_WINDOWS, ROOT, fmt_ts, jobs_dir, parse_ts, read_json, write_json
from pipeline.live import lcfg

CFG_CLIPS = CFG["clips"]


def lcfg_get(key: str, default=None):
    return lcfg().get(key, default)

from .db import DB

STAGES = ["download", "transcribe", "score", "pick", "review", "render"]
CLIP_PY = str(ROOT / "clip.py")

# Tools Claude may use without asking in the headless /clip run (mirrors .claude/settings.json)
CLAUDE_TOOLS = [
    "Read", "Glob", "Grep",
    "Bash(.venv/bin/python clip.py:*)", "Bash(.venv/Scripts/python clip.py:*)",
    "Bash(.venv/Scripts/python.exe clip.py:*)",
    "PowerShell(.venv\\Scripts\\python.exe clip.py:*)", "PowerShell(.venv/Scripts/python.exe clip.py:*)",
    "Bash(sleep:*)", "Bash(tail:*)", "PowerShell(Start-Sleep:*)",
    "Write(jobs/**)", "Edit(jobs/**)",
]

RE_YTDLP = re.compile(r"^\[download\]\s+([\d.]+)%")
RE_WHISPER = re.compile(r"Progress:\s*([\d.]+)%")
RE_RENDERED = re.compile(r"Rendered\s+(\d+)/(\d+)")
RE_ENCODED = re.compile(r"Encoded\s+(\d+)/(\d+)")
RE_READY = re.compile(r"READY\s+job=(\S+)")
# harmless decoder chatter (seeking into a stream mid-GOP): kept in the log file, not in errors shown to the user
NOISE = re.compile(r"^(\[(h264|hevc|aac|mp3|vp9|av1)[^\]]*@ [0-9a-fx]+\]|\s*Last message repeated)")

# how far through one clip each render step is (the bar fills smoothly from step to step)
RENDER_STEPS = {"queued": 0, "fetch": 3, "panels": 12, "ai": 12, "remotion": 38, "thumb": 94, "done": 100}


class Cancelled(Exception):
    pass


class Interrupted(Exception):
    """The web server is shutting down; the run can be resumed after the next start."""


class StageFailed(Exception):
    pass


# Known tool errors -> what the user can do about them
ERROR_HINTS = [
    (re.compile(r"HTTP Error 403|confirm you.re not a bot|Sign in to confirm", re.I),
     "The site refused the download. This is usually temporary: press Resume. If it keeps happening, "
     "install Deno (winget install DenoLand.Deno), which yt-dlp needs for YouTube, and update yt-dlp."),
    (re.compile(r"HTTP Error 429|Too Many Requests", re.I),
     "The site is rate-limiting this connection. Wait a few minutes, then press Resume."),
    (re.compile(r"Private video|members-only|This video is unavailable|Video unavailable", re.I),
     "The video can't be watched without an account (private, members-only or removed)."),
    (re.compile(r"Requested format is not available", re.I),
     "The site didn't offer a usable format this time. Press Resume; if it repeats, update yt-dlp."),
]


def failure_message(tool: str, rc: int, tail: list[str]) -> str:
    """The real reason first: a subprocess (yt-dlp, streamlink...) prints `ERROR: ...` before clip.py dies with a
    traceback that only says "exit status 1". Without such a line, fall back to the end of the output."""
    errors: list[str] = []
    for line in tail:
        if re.match(r"(ERROR|error):\s", line):
            msg = line.split(":", 1)[1].strip()
            if msg not in errors:
                errors.append(msg)
    if not errors:
        return f"{tool} failed (exit {rc}):\n" + "\n".join(tail[-8:])
    text = "\n".join(errors[-3:])
    hint = next((h for pat, h in ERROR_HINTS if pat.search(text)), None)
    return f"{text}\n\n{hint}" if hint else text


def now() -> float:
    return time.time()


def new_state(pk: int, url: str) -> dict[str, Any]:
    return {
        "id": pk, "url": url, "status": "queued", "job_id": None, "video": None,
        "stages": {s: {"state": "pending", "pct": 0, "msg": "", "started": None, "ended": None} for s in STAGES},
        "activity": [], "candidates": [], "heat": False, "clips": [], "error": None, "notes": [],
        "log": [], "updated": now(),
    }


PRIO_LIVE, PRIO_NORMAL = 0, 1  # live pick rounds / renders jump the queue (the stream keeps moving)


class Runner:
    def __init__(self, db: DB):
        from .live import LiveManager
        self.db = db
        self.tasks: "queue.PriorityQueue[tuple[int, int, str, int]]" = queue.PriorityQueue()
        self._seq = 0
        self.live = LiveManager(self)
        self.states: dict[int, dict] = {}
        self.subs: dict[int, list[queue.Queue]] = {}
        self.lock = threading.RLock()
        self.proc: Optional[subprocess.Popen] = None
        self.current: Optional[int] = None
        self.cancelled: set[int] = set()
        self.stopping = False
        self._last_emit: dict[int, float] = {}
        self._last_save: dict[int, float] = {}
        self.log_dir = jobs_dir() / "_web_logs"
        self.log_dir.mkdir(parents=True, exist_ok=True)

    # ------------------------------------------------------------ public API
    def start(self) -> None:
        threading.Thread(target=self._worker, name="clip-runner", daemon=True).start()

    def state(self, pk: int) -> Optional[dict]:
        with self.lock:
            if pk in self.states:
                return self.states[pk]
            row = self.db.run(pk)
            if not row:
                return None
            st = json.loads(row["state"]) if row["state"] else new_state(pk, row["url"])
            st["status"] = row["status"]
            st["error"] = row["error"] or st.get("error")
            self.states[pk] = st
            return st

    def put(self, kind: str, pk: int, prio: int = PRIO_NORMAL) -> None:
        self._seq += 1
        self.tasks.put((prio, self._seq, kind, pk))

    def submit(self, pk: int, url: str) -> None:
        with self.lock:
            self.states[pk] = new_state(pk, url)
            if os.environ.get("ANTHROPIC_API_KEY"):
                self.states[pk]["notes"].append(
                    "ANTHROPIC_API_KEY is set: it is ignored for the Claude step, so your Pro plan is used.")
        self._save(pk, force=True)
        self.put("prepare", pk)
        self._emit(pk, force=True)

    # ------------------------------------------------------------ live runs
    def submit_live(self, pk: int, url: str, opts: dict) -> None:
        from .live import new_live_state
        with self.lock:
            st = new_state(pk, url)
            st["live"] = new_live_state(opts)
            st["status"] = "live"
            self.states[pk] = st
        self.db.update_run(pk, status="live")
        self._save(pk, force=True)
        self.live.start(pk)
        self._emit(pk, force=True)

    def is_live(self, pk: int) -> bool:
        st = self.state(pk)
        return bool(st and st.get("live"))

    def queue_live_round(self, pk: int, reason: str) -> None:
        st = self.state(pk)
        st["live"]["round_reason_queued"] = reason
        self._activity(pk, {"hot": "A hot moment just happened: Claude will look at it",
                            "manual": "Pick round requested", "final": "Final pick round over the last stretch"}
                       .get(reason, "Time for a pick round"), "pick")
        self.put("live_pick", pk, PRIO_LIVE)

    def load_live_job(self, pk: int, jd: Path) -> None:
        """After each processed chunk: the growing heat map and candidate markers."""
        st = self.state(pk)
        if (jd / "candidates.json").exists():
            st["candidates"] = [{"id": c["id"], "start": c["start"], "end": c["end"], "peak": c["peak"],
                                 "score": c["score"], "chat": c.get("chat_x_baseline")}
                                for c in read_json(jd / "candidates.json")]
        st["heat"] = (jd / "signals.json").exists()
        st["heat_rev"] = st.get("heat_rev", 0) + 1

    def live_ended(self, pk: int) -> None:
        """The recording is over: one last round over the tail, then the held renders."""
        st = self.state(pk)
        lv = st["live"]
        self._set_status(pk, "running")  # no longer "Live": finishing the last round / renders
        if lv["processed"] - lv["picked_until"] >= 20 and not lv["round_pending"]:
            lv["round_pending"] = True
            self.queue_live_round(pk, "final")
        else:
            self._live_settle(pk)

    def release_held(self, pk: int) -> None:
        """Start the renders that game mode was holding back."""
        st = self.state(pk)
        held = [c["id"] for c in st["clips"] if (c.get("render") or {}).get("step") == "held"]
        if not held:
            return
        for c in st["clips"]:
            if c["id"] in held:
                c["render"] = {"step": "queued", "pct": 0, "msg": "Waiting"}
        st["live"]["render_ids"] = sorted(set(st["live"].get("render_ids", [])) | set(held))
        self.put("render", pk, PRIO_LIVE)
        self._emit(pk, force=True)

    def _live_settle(self, pk: int) -> None:
        """Ended live run with nothing queued: render what is held, then review / done."""
        st = self.state(pk)
        if st["live"].get("round_pending"):
            return  # the final round is still queued; it settles again when it is done
        if any((c.get("render") or {}).get("step") == "held" for c in st["clips"]):
            self.release_held(pk)
            return
        if any((c.get("render") or {}).get("step") in ("queued", "fetch", "panels", "ai", "remotion", "thumb")
               for c in st["clips"]):
            return  # a render task is on its way; it settles again when done
        if any(c.get("new") for c in st["clips"]):
            self._stage(st, "review", 0, "Waiting for you")
            self._set_status(pk, "review")
            self._activity(pk, "The stream is over: review the last picks", "done")
        else:
            n = sum(1 for c in st["clips"] if (c.get("render") or {}).get("step") == "done")
            self._activity(pk, f"Live clipping finished: {n} clip{'' if n == 1 else 's'} in your library", "done")
            self._finish(pk, "done")
        self._emit(pk, force=True)

    def _live_pick(self, pk: int) -> None:
        st = self.state(pk)
        lv = st["live"]
        jd = jobs_dir() / st["job_id"]
        reason = lv.pop("round_reason_queued", "timer")
        start, end = float(lv["picked_until"]), float(lv["processed"])
        n = len(lv["rounds"]) + 1
        try:
            if end - start < (1 if reason == "final" else 30):
                return
            self._stage(st, "pick", 5, f"Round {n}: {fmt_ts(start)} - {fmt_ts(end)}")
            lv.update(picking=True)
            self._emit(pk, force=True)
            self._exec(pk, [sys.executable, CLIP_PY, "candidates", jd.name, "--from", f"{start:.1f}",
                            "--to", f"{end:.1f}"], lambda line: None)
            window = read_json(jd / "candidates_window.json") if (jd / "candidates_window.json").exists() else []
            before = read_json(jd / "clips.json") if (jd / "clips.json").exists() else {"clips": []}
            old = before["clips"] if isinstance(before, dict) else before
            added_ids: list[str] = []
            if window:
                per_round = int(lcfg_get("clips_per_round", 3))
                if os.environ.get("CLIP_FAKE_PICKER") == "1":
                    self._fake_pick(pk, jd, append=True, limit=per_round)
                else:
                    prompt = (
                        f"/clip {jd.name}\n\n"
                        f"This is LIVE pick round {n}, started from the web app while the stream is still running. "
                        f"candidates.md only lists moments from {fmt_ts(start)} to {fmt_ts(end)}. Do steps 1-6 only "
                        "and do NOT render. Keep every clip that is already in clips.json exactly as it is (same ids, "
                        f"same fields) and APPEND at most {per_round} new clips with ids continuing the numbering "
                        f"(clip_{len(old) + 1:02d}, ...). If nothing in the window is worth a clip, append none and "
                        "say so. Don't ask questions, decide. Every new clip needs a `title`, a `description` and "
                        "3-6 relevant `hashtags`."
                    )
                    row = self.db.run(pk)
                    if row["extra_prompt"]:
                        prompt += f"\n\nExtra instructions from the user: {row['extra_prompt']}"
                    self._claude_pick(pk, jd, prompt=prompt)
                added_ids = self._merge_round(pk, jd, old, n)
            lv["rounds"].append({"n": n, "from": start, "to": end, "at": now(), "reason": reason,
                                 "added": added_ids, "candidates": len(window)})
            lv["picked_until"] = end
            self._stage(st, "pick", 100, f"Round {n}: {len(added_ids)} new clip{'' if len(added_ids) == 1 else 's'}",
                        state="done")
            self._activity(pk, f"Round {n} ({fmt_ts(start)} - {fmt_ts(end)}): "
                           + (f"{len(added_ids)} new clip{'' if len(added_ids) == 1 else 's'} to review"
                              if added_ids else "nothing worth clipping"), "done" if added_ids else "info")
        finally:
            lv.update(round_pending=False, picking=False)
            self._save(pk, force=True)
            self._emit(pk, force=True)
            if lv.get("ended"):
                self._live_settle(pk)

    def _merge_round(self, pk: int, jd: Path, old: list[dict], n: int) -> list[str]:
        """Validate the round, put back any earlier clip Claude touched, tag the new ones with their round."""
        ok, report = self._validate(pk, jd)
        data = read_json(jd / "clips.json")
        clips = data["clips"] if isinstance(data, dict) else data
        old_ids = {c.get("id") for c in old}
        new = [c for c in clips if c.get("id") not in old_ids]
        if not ok:  # drop only the new clips that fail; never lose the earlier ones
            bad = {line.split(":")[0].strip() for line in report.splitlines() if ":" in line}
            new = [c for c in new if c.get("id") not in bad]
            self._activity(pk, f"Dropped {len(bad)} new pick(s) that didn't validate", "info")
        for c in new:
            c["round"] = n
        write_json(jd / "clips.json", {"clips": old + new})
        self._load_clips(pk, jd)
        ids = [c["id"] for c in new]
        st = self.state(pk)
        for c in st["clips"]:
            if c["id"] in ids:
                c.update(new=True, selected=True, round=n, render=None)
        return ids

    def live_render_now(self, pk: int) -> None:
        self.state(pk)["live"]["render_now"] = True
        self.release_held(pk)

    def _approve_live(self, pk: int, clip_ids: list[str]) -> None:
        """Review of one or more live rounds: only the clips still marked new are decided here."""
        st = self.state(pk)
        lv = st["live"]
        new_ids = [c["id"] for c in st["clips"] if c.get("new")]
        if not new_ids:
            raise ValueError("no new clips to review")
        chosen = [i for i in clip_ids if i in new_ids]
        hold = lv["game_mode"] and not lv.get("ended") and not lv.get("render_now")
        with self.lock:
            for c in st["clips"]:
                if c["id"] not in new_ids:
                    continue
                c["new"] = False
                c["selected"] = c["id"] in chosen
                if c["selected"]:
                    c["render"] = ({"step": "held", "pct": 0, "msg": "Renders when the stream ends (game mode)"}
                                   if hold else {"step": "queued", "pct": 0, "msg": "Waiting"})
        if chosen and not hold:
            lv["render_ids"] = sorted(set(lv.get("render_ids", [])) | set(chosen))
            if lv.get("ended"):
                self._set_status(pk, "queued_render")
            self.put("render", pk, PRIO_LIVE)
        n = len(chosen)
        self._activity(pk, f"{n} clip{'' if n == 1 else 's'} approved"
                       + (": rendering after the stream (game mode)" if hold and n else ""), "done")
        self._save(pk, force=True)
        self._emit(pk, force=True)
        if lv.get("ended") and not (chosen and not hold):
            self._live_settle(pk)

    def approve(self, pk: int, clip_ids: list[str]) -> None:
        st = self.state(pk)
        if st and st.get("live"):
            return self._approve_live(pk, clip_ids)
        if not st or not clip_ids:
            raise ValueError("nothing selected")
        with self.lock:
            for c in st["clips"]:
                c["selected"] = c["id"] in clip_ids
                c["render"] = {"step": "queued", "pct": 0, "msg": "Waiting"} if c["selected"] else None
            st["status"] = "queued_render"
            self._stage(st, "review", 100, f"{len(clip_ids)} clips approved", state="done")
        self.db.update_run(pk, status="queued_render", selected=clip_ids)
        self._save(pk, force=True)
        self.put("render", pk)
        self._emit(pk, force=True)

    def resume(self, pk: int) -> None:
        st = self.state(pk)
        if not st:
            raise ValueError("unknown run")
        row = self.db.run(pk)
        self.cancelled.discard(pk)
        if st.get("live"):
            st["error"] = None
            self.db.update_run(pk, error=None)
            lv = st["live"]
            lv["round_pending"] = False
            if not lv.get("ended"):
                self._activity(pk, "Resuming the live recording", "info")
                self.live.start(pk)  # the recorder continues the same job as a new part
            else:
                if lv["processed"] - lv["picked_until"] >= 20:
                    lv["round_pending"] = True
                    self.queue_live_round(pk, "final")
                else:
                    for c in st["clips"]:
                        if (c.get("render") or {}).get("step") not in (None, "done", "error"):
                            c["render"] = {"step": "held", "pct": 0, "msg": "Waiting"}
                    self._live_settle(pk)
            self._save(pk, force=True)
            self._emit(pk, force=True)
            return
        with self.lock:
            st["error"] = None
            if st["stages"]["render"]["state"] in ("active", "error") or row["status"] == "queued_render":
                st["status"] = "queued_render"
                task = "render"
            elif st["stages"]["review"]["state"] == "active":
                st["status"] = "review"
                task = None
            else:
                st["status"] = "queued"
                task = "prepare"
        self.db.update_run(pk, status=st["status"], error=None)
        self._save(pk, force=True)
        if task:
            self.put(task, pk)
        self._emit(pk, force=True)

    def cancel(self, pk: int) -> None:
        self.cancelled.add(pk)
        if self.live.running(pk):
            self.live.kill(pk)  # the supervisor marks the run cancelled when the process is gone
        if self.current == pk and self.proc and self.proc.poll() is None:
            self._kill(self.proc)
        st = self.state(pk)
        if st and self.current != pk:
            self._finish(pk, "cancelled")

    def subscribe(self, pk: int) -> queue.Queue:
        q: queue.Queue = queue.Queue(maxsize=200)
        with self.lock:
            self.subs.setdefault(pk, []).append(q)
        return q

    def unsubscribe(self, pk: int, q: queue.Queue) -> None:
        with self.lock:
            if q in self.subs.get(pk, []):
                self.subs[pk].remove(q)

    def shutdown(self) -> None:
        self.stopping = True
        live_runs = [pk for pk in list(self.states) if self.live.running(pk)]
        self.live.shutdown()
        for pk in live_runs:
            self._finish(pk, "interrupted")
        if self.proc and self.proc.poll() is None:
            self._kill(self.proc)
        if self.current is not None:  # don't wait for the worker thread: record the state now
            self._finish(self.current, "interrupted")

    def forget(self, pk: int) -> None:
        """Drop a deleted run from memory (its DB row is already gone)."""
        with self.lock:
            self.states.pop(pk, None)
            self.subs.pop(pk, None)
        self.cancelled.add(pk)  # a stale queued task for it is skipped (run ids are never reused, see DB.create_run)

    def queue_position(self, pk: int) -> int:
        items = sorted(list(self.tasks.queue))
        ids = [p for *_, p in items]
        return ids.index(pk) + 1 if pk in ids else 0

    # public names for the live manager
    def emit(self, pk: int, force: bool = False) -> None:
        self._emit(pk, force)

    def set_status(self, pk: int, status: str) -> None:
        self._set_status(pk, status)

    def finish(self, pk: int, status: str) -> None:
        self._finish(pk, status)

    def fail(self, pk: int, message: str) -> None:
        self._fail(pk, message)

    def activity(self, pk: int, text: str, kind: str = "info") -> None:
        self._activity(pk, text, kind)

    # ------------------------------------------------------------ worker
    def _worker(self) -> None:
        while True:
            _prio, _seq, kind, pk = self.tasks.get()
            if pk in self.cancelled:
                continue
            self.current = pk
            try:
                if kind == "prepare":
                    self._do_prepare(pk)
                elif kind == "render":
                    self._do_render(pk)
                elif kind == "live_pick":
                    self._live_pick(pk)
            except Cancelled:
                self._finish(pk, "cancelled")
            except Interrupted:
                self._finish(pk, "interrupted")
            except StageFailed as e:
                self._task_failed(pk, kind, str(e))
            except Exception as e:  # keep the worker alive whatever happens
                self._log(pk, traceback.format_exc())
                self._task_failed(pk, kind, f"{e.__class__.__name__}: {e}")
            finally:
                self.current = None
                self.proc = None

    def _task_failed(self, pk: int, kind: str, message: str) -> None:
        if self.stopping:
            self._finish(pk, "interrupted")
            return
        st = self.state(pk)
        if st and st.get("live") and (self.live.running(pk) or st["live"].get("ended")):
            # a failed round / render doesn't end a live run: report it, let the next one (or a retry) go again
            for c in st["clips"]:
                r = c.get("render") or {}
                if r.get("step") not in (None, "done", "held"):
                    c["render"] = None
                    c["new"] = True  # back into review, so approving it again retries the render
            first = message.splitlines()[0][:300] if message else "unknown error"
            st["notes"] = (st.get("notes") or [])[-3:] + [
                f"{'Pick round' if kind == 'live_pick' else 'Render'} failed: {first}"]
            self._activity(pk, ("The pick round" if kind == "live_pick" else "A render") + " failed"
                           + ("; the recording continues" if self.live.running(pk) else ""), "info")
            self._save(pk, force=True)
            self._emit(pk, force=True)
            if not self.live.running(pk) and st["live"].get("ended") and kind != "live_pick":
                self._live_settle(pk)
            return
        self._fail(pk, message)

    # ------------------------------------------------------------ prepare + pick
    def _do_prepare(self, pk: int) -> None:
        st = self.state(pk)
        url = self.db.run(pk)["url"]
        self._set_status(pk, "running")
        self._stage(st, "download", 0, "Starting")
        self._emit(pk, force=True)

        def on_line(line: str) -> None:
            if line.startswith("@@"):
                self._on_progress(pk, json.loads(line[2:]))
                return
            m = RE_YTDLP.match(line)
            if m and st["stages"]["download"]["state"] == "active":
                self._stage(st, "download", float(m.group(1)) * 0.97, "Downloading the audio track")
                return
            m = RE_WHISPER.search(line)
            if m and st["stages"]["transcribe"]["state"] == "active":
                self._stage(st, "transcribe", 1 + 0.84 * float(m.group(1)))
                return
            m = RE_READY.search(line)
            if m:
                st["job_id"] = m.group(1)

        self._exec(pk, [sys.executable, CLIP_PY, "prepare", url], on_line)
        if not st["job_id"]:
            raise StageFailed("prepare finished without a job")
        jd = jobs_dir() / st["job_id"]
        self._load_job(pk, jd)
        self._stage(st, "score", 100, st["stages"]["score"]["msg"] or "Scored", state="done")
        self.db.update_run(pk, job_id=st["job_id"])
        self._pick(pk, jd)

    def _load_job(self, pk: int, jd: Path) -> None:
        st = self.state(pk)
        meta = read_json(jd / "meta.json")
        st["video"] = {"title": meta.get("title"), "channel": meta.get("channel"), "platform": meta.get("platform"),
                       "duration": meta.get("duration"), "mode": meta.get("mode"), "video_id": meta.get("video_id")}
        if (jd / "candidates.json").exists():
            st["candidates"] = [{"id": c["id"], "start": c["start"], "end": c["end"], "peak": c["peak"],
                                 "score": c["score"], "chat": c.get("chat_x_baseline")}
                                for c in read_json(jd / "candidates.json")]
        st["heat"] = (jd / "signals.json").exists()
        self.db.update_run(pk, platform=meta.get("platform"), video_id=str(meta.get("video_id")))

    def _validate(self, pk: int, jd: Path) -> tuple[bool, str]:
        p = subprocess.run([sys.executable, CLIP_PY, "validate", jd.name], cwd=ROOT, env=self._env(),
                           capture_output=True, text=True, encoding="utf-8", errors="replace")
        return p.returncode == 0, (p.stdout + p.stderr).strip()

    def _pick(self, pk: int, jd: Path) -> None:
        st = self.state(pk)
        self._stage(st, "pick", 2, "Handing the best moments to Claude")
        self._emit(pk, force=True)
        if (jd / "clips.json").exists() and self._validate(pk, jd)[0] and st["stages"]["pick"].get("resumable"):
            self._activity(pk, "Using the clips Claude already picked", "info")
        else:
            self._archive_previous(pk, jd)
            if os.environ.get("CLIP_FAKE_PICKER") == "1":
                self._fake_pick(pk, jd)
            else:
                self._claude_pick(pk, jd)
        ok, report = self._validate(pk, jd)
        if not ok:
            raise StageFailed(f"Claude's clips.json did not validate:\n{report}")
        st["stages"]["pick"]["resumable"] = True
        self._load_clips(pk, jd)
        self._stage(st, "pick", 100, f"{len(st['clips'])} clips picked", state="done")
        self._stage(st, "review", 0, "Waiting for you")
        self._set_status(pk, "review")
        self._activity(pk, f"{len(st['clips'])} clips ready for your review", "done")
        self._emit(pk, force=True)

    def _archive_previous(self, pk: int, jd: Path) -> None:
        """A new pick for a video that already has clips: move the old picks and renders aside, so new clip ids
        never get matched with old files (they stay on disk in jobs/<job>/archive/<time>/)."""
        out = jd / "out"
        old_clips = jd / "clips.json"
        if not old_clips.exists() and not (out.exists() and any(out.iterdir())):
            return
        dest = jd / "archive" / time.strftime("%Y%m%d-%H%M%S")
        dest.mkdir(parents=True, exist_ok=True)
        if old_clips.exists():
            old_clips.replace(dest / "clips.json")
        if out.exists():
            out.replace(dest / "out")
        self.db.sync_job(jd)  # old rows now point at missing files and drop out of the library view
        self._activity(pk, f"Earlier clips of this video moved to archive/{dest.name}", "info")

    def _load_clips(self, pk: int, jd: Path) -> None:
        st = self.state(pk)
        data = read_json(jd / "clips.json")
        clips = data["clips"] if isinstance(data, dict) else data
        old = {c["id"]: c for c in st.get("clips", [])}
        st["clips"] = []
        for i, c in enumerate(clips, 1):
            cid = c.get("id") or f"clip_{i:02d}"
            s, e = parse_ts(c["start"]), parse_ts(c["end"])
            prev = old.get(cid, {})
            st["clips"].append({
                "id": cid, "start": s, "end": e, "duration": round(e - s, 1), "hook": c.get("hook", ""),
                "title": c.get("title", ""), "score": c.get("score"), "reason": c.get("reason", ""),
                "layout": c.get("layout", ""), "hashtags": c.get("hashtags", []),
                "selected": prev.get("selected", True), "render": prev.get("render"), "pk": prev.get("pk"),
                "new": prev.get("new", False), "round": c.get("round", prev.get("round")),
            })

    def _claude_pick(self, pk: int, jd: Path, prompt: Optional[str] = None) -> None:
        st = self.state(pk)
        claude = shutil.which("claude")
        if not claude:
            raise StageFailed("Claude Code is not installed or not on PATH. Install it "
                              "(npm install -g @anthropic-ai/claude-code), run `claude` once and type /login.")
        row = self.db.run(pk)
        if prompt is None:
            prompt = (
                f"/clip {jd.name}\n\n"
                "This run was started from the web app. Do steps 1-6 only: stop as soon as `validate` passes and do "
                "NOT render (the web app shows the picks to the user and renders them). Don't ask questions, decide. "
                "Every clip needs a `title`, a `description` and 3-6 relevant `hashtags`."
            )
            if row["extra_prompt"]:
                prompt += f"\n\nExtra instructions from the user: {row['extra_prompt']}"
        env = self._env()
        env.pop("ANTHROPIC_API_KEY", None)  # use the Claude Code login (Pro plan), never API billing
        clips_file = jd / "clips.json"
        seen = {"mtime": clips_file.stat().st_mtime if clips_file.exists() else 0.0, "result": None}

        def on_line(line: str) -> None:
            try:
                ev = json.loads(line)
            except ValueError:
                return
            t = ev.get("type")
            if t == "assistant":
                for block in ev.get("message", {}).get("content", []):
                    if block.get("type") == "tool_use":
                        text, kind = describe_tool(block.get("name", ""), block.get("input") or {})
                        if text:
                            self._activity(pk, text, kind)
                    elif block.get("type") == "text" and block.get("text", "").strip():
                        self._activity(pk, block["text"].strip()[:220], "claude")
            elif t == "user":  # tool results: pick up clips.json as soon as Claude writes it
                if clips_file.exists() and clips_file.stat().st_mtime != seen["mtime"]:
                    seen["mtime"] = clips_file.stat().st_mtime
                    try:
                        self._load_clips(pk, jd)
                        self._stage(st, "pick", 80, f"{len(st['clips'])} clips drafted")
                    except Exception:
                        pass
                else:
                    p = st["stages"]["pick"]["pct"]
                    self._stage(st, "pick", min(75, p + 3))
            elif t == "result":
                seen["result"] = ev
            self._emit(pk)

        self._activity(pk, "Claude is reading the job", "info")
        self._exec(pk, [claude, "-p", "--output-format", "stream-json", "--verbose", "--allowedTools", *CLAUDE_TOOLS],
                   on_line, stdin_text=prompt, env=env)
        res = seen["result"]
        if res and (res.get("is_error") or res.get("subtype") not in (None, "success")):
            raise StageFailed(f"Claude stopped: {res.get('result') or res.get('subtype')}")

    def _fake_pick(self, pk: int, jd: Path, append: bool = False, limit: int = 3) -> None:
        """Test stand-in for Claude (CLIP_FAKE_PICKER=1): picks the top candidates with canned text.
        append=True (live rounds): adds to clips.json from candidates_window.json instead of replacing it."""
        st = self.state(pk)
        meta = read_json(jd / "meta.json")
        src = "candidates_window.json" if append else "candidates.json"
        cands = sorted(read_json(jd / src), key=lambda c: -c["score"])[:limit]
        old = []
        if append and (jd / "clips.json").exists():
            data = read_json(jd / "clips.json")
            old = data["clips"] if isinstance(data, dict) else data
        for text, kind in (("Reading the hottest moments", "read"), ("Fine-tuning cut points", "cut"),
                           ("Writing hooks, titles and hashtags", "write")):
            self._activity(pk, f"[fake picker] {text}", kind)
            self._stage(st, "pick", st["stages"]["pick"]["pct"] + 25)
            self._emit(pk, force=True)
            time.sleep(1.0)
        clips = []
        for k, c in enumerate(cands, len(old) + 1):
            s = max(0.0, c["peak"] - 10.0)
            e = min(float(meta["duration"]), s + 18.0 + 2 * (k % 4))
            if e - s < float(CFG_CLIPS["min_sec"]):
                s = max(0.0, e - float(CFG_CLIPS["min_sec"]) - 1)
            if any(s < parse_ts(o["end"]) and e > parse_ts(o["start"]) for o in old + clips):
                continue
            clips.append({"id": f"clip_{k:02d}", "start": round(s, 1), "end": round(e, 1), "layout": "center",
                          "hook": f"Test hook number *{k}*", "emphasis": [],
                          "thumb_text": f"TEST *CLIP* {k}", "thumb_at": round(s + 6, 1),
                          "title": f"Test clip {k}: the moment at {fmt_ts(c['peak'])}",
                          "description": "A test description written by the fake picker.",
                          "hashtags": ["#test", "#clips", "#fyp"], "score": 92 - 9 * k,
                          "reason": f"candidate {c['id']} (score {c['score']})"})
        write_json(jd / "clips.json", {"clips": old + clips})

    # ------------------------------------------------------------ render
    def _do_render(self, pk: int) -> None:
        st = self.state(pk)
        row = self.db.run(pk)
        live = st.get("live")
        env = None
        if live:
            ids = live.pop("render_ids", [])
            if not ids:
                return
            if live.get("game_mode") and not live.get("ended"):
                env = self._env()
                env["CLIP_AI_UPSCALE"] = "off"  # the GPU belongs to the game while the stream runs
        else:
            ids = json.loads(row["selected"] or "[]")
        jd = jobs_dir() / st["job_id"]
        if not live or live.get("ended"):
            self._set_status(pk, "rendering")
        self._stage(st, "render", 0, f"Rendering {len(ids)} clips")
        for c in st["clips"]:
            if c["id"] in ids:
                c["render"] = {"step": "queued", "pct": 0, "msg": "Waiting"}
        self._emit(pk, force=True)
        cur = {"clip": None}

        def clip(cid: Optional[str]) -> Optional[dict]:
            return next((c for c in st["clips"] if c["id"] == cid), None)

        def set_pct(cid: Optional[str], pct: float, msg: Optional[str] = None, step: Optional[str] = None) -> None:
            c = clip(cid)
            if not c or c.get("render") is None:
                return
            c["render"]["pct"] = max(c["render"]["pct"], round(pct, 1))
            if msg:
                c["render"]["msg"] = msg
            if step:
                c["render"]["step"] = step
            sel = [x for x in st["clips"] if x.get("render") is not None]
            total = sum(x["render"]["pct"] for x in sel) / max(1, len(sel))
            done = sum(1 for x in sel if x["render"]["step"] == "done")
            self._stage(st, "render", total, f"{done}/{len(sel)} clips done")

        def on_line(line: str) -> None:
            if line.startswith("@@"):
                ev = json.loads(line[2:])
                if ev.get("stage") != "render":
                    return
                if ev.get("clip"):
                    cur["clip"] = ev["clip"]
                stepname = ev.get("step", "")
                if stepname == "ai":
                    set_pct(cur["clip"], RENDER_STEPS["panels"] + 0.26 * ev.get("pct", 0), ev.get("msg"), "ai")
                else:
                    set_pct(cur["clip"], RENDER_STEPS.get(stepname, 0), ev.get("msg"), stepname)
                self._emit(pk)
                return
            m = RE_RENDERED.search(line)
            if m and cur["clip"]:
                a, b = int(m.group(1)), max(1, int(m.group(2)))
                set_pct(cur["clip"], RENDER_STEPS["remotion"] + 42 * a / b, f"Rendering frames {a}/{b}")
                self._emit(pk)
                return
            m = RE_ENCODED.search(line)
            if m and cur["clip"]:
                a, b = int(m.group(1)), max(1, int(m.group(2)))
                set_pct(cur["clip"], 80 + 14 * a / b, f"Encoding {a}/{b}")
                self._emit(pk)

        self._exec(pk, [sys.executable, CLIP_PY, "render", st["job_id"], "--only", ",".join(ids)], on_line, env=env,
                   low_priority=bool(live and live.get("game_mode") and not live.get("ended")))
        # into the library
        self.db.sync_job(jd)
        meta = read_json(jd / "meta.json")
        video = self.db.video_by_key(meta.get("platform", "local"), str(meta.get("video_id")))
        if video is not None:
            if row["desc_link"] is not None:
                self.db.set_video_link(video["pk"], row["desc_link"])
            pks = {r["clip_id"]: r["pk"] for r in self.db.q("SELECT pk, clip_id FROM clips WHERE video_pk=?",
                                                              (video["pk"],))}
            for c in st["clips"]:
                c["pk"] = pks.get(c["id"])
        n = f"{len(ids)} clip{'' if len(ids) == 1 else 's'}"
        self._stage(st, "render", 100, f"{n} ready", state="done")
        self._activity(pk, f"Done: {n} {'is' if len(ids) == 1 else 'are'} in your library", "done")
        if live:
            for c in st["clips"]:
                if c["id"] in ids and c.get("render"):
                    c["render"].update(step="done", pct=100, msg="Ready")
            self._save(pk, force=True)
            self._emit(pk, force=True)
            if live.get("ended"):
                self._live_settle(pk)
            return
        self._finish(pk, "done")

    # ------------------------------------------------------------ progress plumbing
    def _on_progress(self, pk: int, ev: dict) -> None:
        st = self.state(pk)
        stage = ev.get("stage")
        if stage == "job":
            st["job_id"] = ev.get("job_id")
            st["video"] = {k: ev.get(k) for k in ("title", "channel", "platform", "duration", "video_id")}
            self._activity(pk, f"Found “{ev.get('title')}” by {ev.get('channel')}", "info")
        elif stage in STAGES:
            self._stage(st, stage, ev.get("pct"), ev.get("msg"))
            if ev.get("msg"):
                self._activity(pk, ev["msg"], stage)
        self._emit(pk)

    def _stage(self, st: dict, name: str, pct: Optional[float] = None, msg: Optional[str] = None,
               state: str = "active") -> None:
        idx = STAGES.index(name)
        for s in STAGES[:idx]:  # moving forward closes the earlier stages
            sd = st["stages"][s]
            if sd["state"] in ("pending", "active"):
                sd.update(state="done", pct=100, ended=sd["ended"] or now())
                sd["started"] = sd["started"] or now()
        sd = st["stages"][name]
        if sd["state"] != state:
            if state == "active" and not sd["started"]:
                sd["started"] = now()
            if state in ("done", "error"):
                sd["ended"] = now()
            sd["state"] = state
        if pct is not None:
            sd["pct"] = round(max(0.0, min(100.0, float(pct))), 1)
        if msg:
            sd["msg"] = msg

    def _activity(self, pk: int, text: str, kind: str = "info") -> None:
        st = self.state(pk)
        st["activity"].append({"t": now(), "text": text, "kind": kind})
        del st["activity"][:-60]

    def _set_status(self, pk: int, status: str) -> None:
        st = self.state(pk)
        st["status"] = status
        self.db.update_run(pk, status=status)
        self._save(pk, force=True)

    def _finish(self, pk: int, status: str) -> None:
        st = self.state(pk)
        st["status"] = status
        if status in ("cancelled", "interrupted"):
            for sd in st["stages"].values():
                if sd["state"] == "active" and not (status == "interrupted" and sd is st["stages"]["review"]):
                    sd["state"] = "error"
                    sd["msg"] = "Cancelled" if status == "cancelled" else "Stopped when the app closed: press Resume"
        self.db.update_run(pk, status=status)
        self._save(pk, force=True)
        self._emit(pk, force=True)

    def _fail(self, pk: int, message: str) -> None:
        st = self.state(pk)
        st["error"] = message
        for sd in st["stages"].values():
            if sd["state"] == "active":
                sd["state"] = "error"
        st["status"] = "error"
        self.db.update_run(pk, status="error", error=message)
        self._save(pk, force=True)
        self._emit(pk, force=True)

    def _save(self, pk: int, force: bool = False) -> None:
        t = now()
        if not force and t - self._last_save.get(pk, 0) < 2.0:
            return
        self._last_save[pk] = t
        st = self.state(pk)
        if st is not None:
            self.db.update_run(pk, state=st)

    def _emit(self, pk: int, force: bool = False) -> None:
        t = now()
        if not force and t - self._last_emit.get(pk, 0) < 0.25:
            return
        self._last_emit[pk] = t
        st = self.state(pk)
        if st is None:
            return
        st["updated"] = t
        payload = json.dumps(st, ensure_ascii=False)
        with self.lock:
            for q in list(self.subs.get(pk, [])):
                try:
                    q.put_nowait(payload)
                except queue.Full:
                    pass
        self._save(pk)

    def _log(self, pk: int, text: str) -> None:
        with open(self.log_dir / f"run_{pk}.log", "a", encoding="utf-8") as f:
            f.write(text.rstrip("\n") + "\n")

    # ------------------------------------------------------------ subprocesses
    def _env(self) -> dict[str, str]:
        env = dict(os.environ)
        env.update(CLIP_PROGRESS="1", PYTHONUNBUFFERED="1", PYTHONIOENCODING="utf-8")
        return env

    def _exec(self, pk: int, cmd: list[str], on_line: Callable[[str], None], stdin_text: Optional[str] = None,
              env: Optional[dict] = None, low_priority: bool = False) -> None:
        if pk in self.cancelled:
            raise Cancelled()
        if self.stopping:
            raise Interrupted()
        self._log(pk, f"$ {' '.join(cmd)}")
        kw: dict[str, Any] = {}
        if IS_WINDOWS:
            kw["creationflags"] = subprocess.CREATE_NEW_PROCESS_GROUP | (BELOW_NORMAL if low_priority else 0)
        else:
            kw["start_new_session"] = True
            if low_priority:
                kw["preexec_fn"] = lambda: os.nice(10)
        self.proc = subprocess.Popen(cmd, cwd=ROOT, env=env or self._env(), stdout=subprocess.PIPE,
                                     stderr=subprocess.STDOUT,
                                     stdin=subprocess.PIPE if stdin_text is not None else subprocess.DEVNULL, **kw)
        if stdin_text is not None:
            self.proc.stdin.write(stdin_text.encode("utf-8"))
            self.proc.stdin.close()
        st = self.state(pk)
        buf = b""
        tail: list[str] = []
        while True:
            chunk = self.proc.stdout.read1(65536) if hasattr(self.proc.stdout, "read1") else self.proc.stdout.read(4096)
            if not chunk:
                break
            buf += chunk
            # progress bars redraw with \r; treat it as a line break too
            parts = re.split(rb"\r\n|\r|\n", buf)
            buf = parts.pop()
            for raw in parts:
                line = raw.decode("utf-8", "replace").strip()
                if not line:
                    continue
                self._log(pk, line)
                if not line.startswith(("@@", "{")) and not NOISE.match(line):
                    tail.append(line)
                    del tail[:-40]
                    st["log"] = tail[-12:]
                try:
                    on_line(line)
                except Exception:
                    self._log(pk, "progress parse error: " + traceback.format_exc())
        rc = self.proc.wait()
        if pk in self.cancelled:
            raise Cancelled()
        if self.stopping:
            raise Interrupted()
        if rc != 0:
            raise StageFailed(failure_message(Path(cmd[1]).name if len(cmd) > 1 else cmd[0], rc, tail))

    @staticmethod
    def _kill(proc: subprocess.Popen) -> None:
        try:
            if IS_WINDOWS:
                subprocess.run(["taskkill", "/PID", str(proc.pid), "/T", "/F"], capture_output=True)
            else:
                os.killpg(proc.pid, signal.SIGTERM)
        except Exception:
            proc.kill()


def describe_tool(name: str, inp: dict) -> tuple[Optional[str], str]:
    """Turn one of Claude's tool calls into a friendly activity line."""
    if name in ("Bash", "PowerShell"):
        cmd = inp.get("command", "")
        m = re.search(r"clip\.py\s+(\S+)(.*)", cmd)
        if not m:
            return ("Waiting a moment" if "sleep" in cmd.lower() else None), "info"
        sub, rest = m.group(1), m.group(2)
        if sub == "words":
            a = re.search(r"--from\s+(\S+)", rest)
            b = re.search(r"--to\s+(\S+)", rest)
            span = f" {a.group(1)}–{b.group(1)}" if a and b else ""
            return f"Fine-tuning the cut{span}", "cut"
        if sub == "frames":
            at = re.search(r"--at\s+(\S+)", rest)
            return f"Looking at the picture{' at ' + at.group(1) if at else ''}", "look"
        if sub == "layout-detect":
            return "Finding the webcam in the stream", "look"
        if sub == "layout-save":
            return "Saving the webcam layout for this channel", "look"
        if sub == "validate":
            return "Checking the picks", "check"
        if sub == "status":
            return "Checking what's ready", "info"
        if sub == "prepare":
            return "Preparing the video", "info"
        return None, "info"
    path = str(inp.get("file_path", "")).replace("\\", "/")
    if name == "Read":
        if path.endswith("candidates.md"):
            return "Reading the hottest moments of the stream", "read"
        if path.lower().endswith((".jpg", ".png")):
            return "Looking at a frame", "look"
        if path.endswith("CLAUDE.md") or path.endswith("config.yaml"):
            return "Reading the clipping rules", "read"
        return None, "read"
    if name in ("Write", "Edit") and path.endswith("clips.json"):
        return "Writing hooks, titles and hashtags", "write"
    return None, "info"
