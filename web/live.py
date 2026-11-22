"""Live runs in the web app: supervises one `clip.py live` process per live run and schedules Claude pick rounds.

The recorder runs outside the runner's serial worker (it lives for hours); pick rounds and renders go through the
runner queue with priority, so a VOD that is being prepared never delays the live stream for long.

Rounds: every `live.pick_every_min`, ~3 min after a hot moment (at least `live.min_pick_gap_min` apart), on
"Pick now", and once more after the stream ends. Each round only looks at the stretch since the previous one.
"""
from __future__ import annotations

import json
import re
import subprocess
import sys
import threading
import time
import traceback
from pathlib import Path
from typing import Any, Optional

from pipeline.common import BELOW_NORMAL, IS_WINDOWS, ROOT, jobs_dir, write_json
from pipeline.live import EST_MBPS, lcfg

CLIP_PY = str(ROOT / "clip.py")
RE_LIVE_JOB = re.compile(r"LIVE\s+job=(\S+)")
RE_ENDED = re.compile(r"LIVE_ENDED\s+job=(\S+)")
MIN_ROUND_SEC = 90  # don't start a round over less new material than this


def live_defaults() -> dict[str, Any]:
    c = lcfg()
    return {"audio": c["video"] == "audio", "quality": c["quality"], "game_mode": bool(c["game_mode"])}


def new_live_state(opts: dict) -> dict[str, Any]:
    c = lcfg()
    return {
        "audio": bool(opts.get("audio")), "quality": opts.get("quality") or c["quality"],
        "game_mode": bool(opts.get("game_mode", True)),
        "recording": False, "recorded": 0.0, "processed": 0.0, "disk": 0, "kbps": 0, "chat": 0, "chat_per_min": 0,
        "step": "starting", "msg": "Connecting to the stream", "started_at": time.time(), "ended": False,
        "rounds": [], "picked_until": 0.0, "next_round_at": time.time() + 60 * float(c["pick_every_min"]),
        "round_reason": "timer", "round_pending": False, "render_now": False, "hot": [],
        "est": EST_MBPS, "pick_every_min": c["pick_every_min"], "offline": False,
    }


class LiveManager:
    def __init__(self, runner):
        self.runner = runner
        self.procs: dict[int, subprocess.Popen] = {}
        self.stopping = False
        self.lock = threading.RLock()

    # ------------------------------------------------------------ public
    def start(self, pk: int) -> None:
        with self.lock:
            if pk in self.procs and self.procs[pk].poll() is None:
                return
        threading.Thread(target=self._supervise, args=(pk,), name=f"live-{pk}", daemon=True).start()

    def running(self, pk: int) -> bool:
        p = self.procs.get(pk)
        return bool(p and p.poll() is None)

    def job_dir(self, pk: int) -> Optional[Path]:
        st = self.runner.state(pk)
        return jobs_dir() / st["job_id"] if st and st.get("job_id") else None

    def set_settings(self, pk: int, changes: dict) -> dict:
        st = self.runner.state(pk)
        lv = st["live"]
        for k in ("audio", "game_mode"):
            if k in changes and changes[k] is not None:
                lv[k] = bool(changes[k])
        if changes.get("quality") in ("1080p", "720p", "480p"):
            lv["quality"] = changes["quality"]
        jd = self.job_dir(pk)
        if jd is not None:
            write_json(jd / "rec" / "control.json", {k: lv[k] for k in ("audio", "quality", "game_mode")})
        if not lv["game_mode"]:
            self.runner.release_held(pk)
        self.runner.emit(pk)
        return lv

    def request_round(self, pk: int, reason: str = "manual") -> None:
        st = self.runner.state(pk)
        lv = st["live"]
        if lv["round_pending"]:
            return
        lv["next_round_at"] = time.time()
        lv["round_reason"] = reason
        self._maybe_round(pk, force=True)

    def stop(self, pk: int) -> None:
        jd = self.job_dir(pk)
        st = self.runner.state(pk)
        if jd is not None:
            (jd / "rec").mkdir(parents=True, exist_ok=True)
            (jd / "rec" / "stop").write_text("stop", encoding="utf-8")
        st["live"].update(step="finishing", msg="Stopping: processing the last minutes")
        self.runner.emit(pk)

    def kill(self, pk: int) -> None:
        p = self.procs.get(pk)
        if p and p.poll() is None:
            self.runner._kill(p)

    def shutdown(self) -> None:
        self.stopping = True
        for pk, p in list(self.procs.items()):
            if p.poll() is None:
                self.runner._kill(p)

    # ------------------------------------------------------------ supervisor
    def _cmd(self, pk: int) -> list[str]:
        st = self.runner.state(pk)
        lv = st["live"]
        src = st.get("job_id") if st.get("job_id") and (jobs_dir() / st["job_id"] / "meta.json").exists() \
            else self.runner.db.run(pk)["url"]
        cmd = [sys.executable, CLIP_PY, "live", src, "--quality", lv["quality"]]
        if lv["audio"]:
            cmd.append("--audio")
        if not lv["game_mode"]:
            cmd.append("--no-game-mode")
        return cmd

    def _supervise(self, pk: int) -> None:
        r = self.runner
        st = r.state(pk)
        lv = st["live"]
        cmd = self._cmd(pk)
        r._log(pk, "$ " + " ".join(cmd))
        kw: dict[str, Any] = {}
        if IS_WINDOWS:
            kw["creationflags"] = subprocess.CREATE_NEW_PROCESS_GROUP | (BELOW_NORMAL if lv["game_mode"] else 0)
        else:
            kw["start_new_session"] = True
        try:
            proc = subprocess.Popen(cmd, cwd=ROOT, env=r._env(), stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                                    stdin=subprocess.DEVNULL, **kw)
        except OSError as e:
            r.fail(pk, f"Couldn't start the live recorder: {e}")
            return
        with self.lock:
            self.procs[pk] = proc
        r.set_status(pk, "live")
        lv.update(recording=False, step="starting", msg="Connecting to the stream", ended=False)
        r.emit(pk, force=True)

        ticker = threading.Thread(target=self._tick_loop, args=(pk, proc), daemon=True, name=f"live-tick-{pk}")
        ticker.start()
        tail: list[str] = []
        ended = False
        for raw in iter(proc.stdout.readline, b""):
            line = raw.decode("utf-8", "replace").strip()
            if not line:
                continue
            r._log(pk, line)
            try:
                if line.startswith("@@"):
                    self._on_event(pk, json.loads(line[2:]))
                elif RE_LIVE_JOB.search(line):
                    st["job_id"] = RE_LIVE_JOB.search(line).group(1)
                    r.db.update_run(pk, job_id=st["job_id"])
                elif RE_ENDED.search(line):
                    ended = True
                else:
                    if not line.startswith("{"):
                        tail.append(line)
                        del tail[:-40]
                        st["log"] = tail[-12:]
            except Exception:
                r._log(pk, "live parse error: " + traceback.format_exc())
            r.emit(pk)
        rc = proc.wait()
        with self.lock:
            self.procs.pop(pk, None)
        lv["recording"] = False
        if self.stopping:
            return
        if pk in r.cancelled:
            r.finish(pk, "cancelled")
            return
        if ended:
            lv.update(ended=True, step="ended", msg="The stream has ended")
            r.activity(pk, "Recording finished", "done")
            r.emit(pk, force=True)
            r.live_ended(pk)
        else:
            msg = f"The live recorder stopped (exit {rc})" + (":\n" + "\n".join(tail[-6:]) if tail else "")
            r.fail(pk, msg)

    def _on_event(self, pk: int, ev: dict) -> None:
        r = self.runner
        st = r.state(pk)
        lv = st["live"]
        stage = ev.get("stage")
        if stage == "job":
            st["job_id"] = ev.get("job_id")
            st["video"] = {k: ev.get(k) for k in ("title", "channel", "platform", "duration", "video_id")}
            r.db.update_run(pk, job_id=st["job_id"], platform=ev.get("platform"), video_id=str(ev.get("video_id")))
            r.activity(pk, f"Live: “{ev.get('title')}” by {ev.get('channel')}", "info")
            return
        if stage != "live":
            return
        step = ev.get("step")
        for k in ("recorded", "processed", "disk", "kbps", "chat", "chat_per_min", "recording", "part"):
            if k in ev:
                lv[k] = ev[k]
        for k in ("audio", "quality", "game_mode"):
            if k in ev and not lv.get("_switching"):
                lv[k] = ev[k]
        if step == "tick":
            lv["offline"] = False
            if lv["step"] in ("starting", "offline") and ev.get("recording"):
                lv.update(step="recording", msg="Recording")
        elif step == "offline":
            lv.update(offline=True, step="offline", msg=ev.get("msg") or "Stream offline")
        elif step == "transcribe":
            if lv["step"] != "finishing":
                lv.update(step="transcribing", msg=ev.get("msg") or "Transcribing")
        elif step == "processed":
            lv.update(step="recording", msg="Recording", processed=ev.get("processed", lv["processed"]))
            jd = self.job_dir(pk)
            if jd is not None:
                r.load_live_job(pk, jd)
            if st.get("video"):
                st["video"]["duration"] = lv["processed"]
            if ev.get("hot") is not None:
                lv["hot"].append(ev["hot"])
                r.activity(pk, f"Hot moment at {_ts(ev['hot'])}", "score")
                self._hot(pk)
        elif step == "finishing":
            lv.update(step="finishing", msg=ev.get("msg") or "Processing the last minutes")
        elif step == "error":
            r.activity(pk, ev.get("msg") or "Processing failed, retrying", "info")
        elif step == "ended":
            lv.update(step="ended", recording=False)

    def _hot(self, pk: int) -> None:
        lv = self.runner.state(pk)["live"]
        gap = 60 * float(lcfg()["min_pick_gap_min"])
        last = lv["rounds"][-1]["at"] if lv["rounds"] else lv["started_at"]
        soon = max(time.time() + 180, last + gap)
        if soon < lv["next_round_at"]:
            lv["next_round_at"] = soon
            lv["round_reason"] = "hot"

    def _tick_loop(self, pk: int, proc: subprocess.Popen) -> None:
        while proc.poll() is None and not self.stopping:
            try:
                self._maybe_round(pk)
            except Exception:
                self.runner._log(pk, "round scheduling error: " + traceback.format_exc())
            time.sleep(5)

    def _maybe_round(self, pk: int, force: bool = False) -> None:
        r = self.runner
        st = r.state(pk)
        if not st or not st.get("job_id"):
            return
        lv = st["live"]
        if lv["round_pending"] or time.time() < lv["next_round_at"]:
            return
        if lv["processed"] - lv["picked_until"] < (1 if force and lv.get("ended") else MIN_ROUND_SEC):
            if force:
                r.activity(pk, "Not enough new material for a pick round yet", "info")
            lv["next_round_at"] = time.time() + 120
            return
        lv["round_pending"] = True
        lv["next_round_at"] = time.time() + 60 * float(lcfg()["pick_every_min"])
        r.queue_live_round(pk, lv.get("round_reason") or "timer")
        lv["round_reason"] = "timer"
        r.emit(pk, force=True)


def _ts(sec: float) -> str:
    sec = int(sec)
    h, m, s = sec // 3600, sec % 3600 // 60, sec % 60
    return f"{h}:{m:02d}:{s:02d}" if h else f"{m}:{s:02d}"
