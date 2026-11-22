"""Local web app: paste a link -> live progress -> review -> render, plus the clip library.

Start with `python clip.py web` (or web.cmd / web.sh). Listens on 127.0.0.1 only.
"""
from __future__ import annotations

import asyncio
import json
import os
import queue
import shutil
import subprocess
import sys
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Optional
from urllib.parse import urlparse

import numpy as np
from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, JSONResponse, PlainTextResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from pipeline.common import IS_WINDOWS, jobs_dir, read_json, which_or_die
from pipeline.download import _is_local_file, platform_of
from pipeline.live import EST_MBPS, lcfg

from . import cleanup
from .db import DB, UPLOAD_PLATFORMS, norm_video_id
from .live import live_defaults
from .runner import Runner

STATIC = Path(__file__).parent / "static"
ACTIVE = ("queued", "running", "review", "queued_render", "rendering", "live")

db: DB
runner: Runner


@asynccontextmanager
async def lifespan(app: FastAPI):
    global db, runner
    db = DB(jobs_dir() / "library.db")
    db.mark_interrupted()
    await asyncio.to_thread(db.sync_all)
    runner = Runner(db)
    runner.start()
    yield
    runner.shutdown()


app = FastAPI(title="Clipping pipeline", lifespan=lifespan, docs_url=None, redoc_url=None)


@app.middleware("http")
async def local_only(request: Request, call_next):
    """Only this machine's browser: blocks DNS-rebinding hosts and other websites posting to localhost."""
    host = (request.headers.get("host") or "").rsplit(":", 1)[0].strip("[]")
    if host not in ("127.0.0.1", "localhost", "::1"):
        return PlainTextResponse("forbidden host", status_code=403)
    if request.method not in ("GET", "HEAD", "OPTIONS"):
        origin = request.headers.get("origin")
        if origin and urlparse(origin).hostname not in ("127.0.0.1", "localhost", "::1"):
            return PlainTextResponse("forbidden origin", status_code=403)
    return await call_next(request)


# ------------------------------------------------------------------ pages
@app.get("/")
def index():
    return FileResponse(STATIC / "index.html", headers={"Cache-Control": "no-cache"})


class NoCacheStatic(StaticFiles):
    """Revalidate on every load (cheap ETag check) so an updated app.js / style.css is never stale."""

    async def get_response(self, path, scope):
        resp = await super().get_response(path, scope)
        resp.headers["Cache-Control"] = "no-cache"
        return resp


app.mount("/static", NoCacheStatic(directory=STATIC), name="static")


# ------------------------------------------------------------------ overview + settings
def run_summary(row) -> dict:
    st = json.loads(row["state"]) if row["state"] else {}
    video = st.get("video") or {}
    return {"id": row["pk"], "url": row["url"], "status": row["status"], "created_at": row["created_at"],
            "title": video.get("title"), "channel": video.get("channel"),
            "platform": video.get("platform") or row["platform"], "clips": len(st.get("clips") or []),
            "queue": runner.queue_position(row["pk"])}


@app.get("/api/overview")
def overview():
    return {
        "runs": [run_summary(r) for r in db.runs(40)],
        "settings": {"desc_link": db.setting("desc_link")},
        "claude_found": bool(shutil.which("claude")),
        "fake_picker": os.environ.get("CLIP_FAKE_PICKER") == "1",
        "api_key_set": bool(os.environ.get("ANTHROPIC_API_KEY")),
    }


class SettingsIn(BaseModel):
    desc_link: str = ""


@app.put("/api/settings")
def put_settings(body: SettingsIn):
    db.set_setting("desc_link", body.desc_link.strip())
    return {"ok": True}


# ------------------------------------------------------------------ runs
class LiveIn(BaseModel):
    audio: bool = False          # False = record the video locally, True = audio only (picture from the VOD)
    quality: str = "720p"        # 1080p | 720p | 480p
    game_mode: bool = True       # low priority, CPU transcription, renders wait for the stream to end


class RunIn(BaseModel):
    url: str
    extra_prompt: str = ""
    desc_link: Optional[str] = None  # None = use the global default, "" = no link
    force: bool = False
    live: Optional[LiveIn] = None    # set once the user chose the live options


def probe(url: str) -> tuple[str, str, dict]:
    """(platform, video_id, info) for a link, via yt-dlp. Raises HTTPException with yt-dlp's reason."""
    if _is_local_file(url):
        p = Path(url).expanduser()
        return "local", p.stem, {"title": p.stem, "uploader": "local"}
    if os.environ.get("CLIP_FAKE_LIVE") and url == "fake-live":  # test mode: a local file played as a live stream
        return "fake", "live-fake", {"is_live": True, "id": "fake", "title": "Fake live stream", "uploader": "fake"}
    if not url.startswith(("http://", "https://")):
        raise HTTPException(400, "Paste a full link (https://...) or a path to a video file.")
    p = subprocess.run([which_or_die("yt-dlp"), "-J", "--no-playlist", "--skip-download", url],
                       capture_output=True, text=True, encoding="utf-8", errors="replace")
    if p.returncode != 0:
        reason = next((ln for ln in reversed(p.stderr.splitlines()) if "ERROR" in ln), p.stderr.strip()[-300:])
        raise HTTPException(400, f"Couldn't open this link: {reason or 'yt-dlp failed'}")
    info = json.loads(p.stdout)
    if info.get("is_live"):
        return platform_of(info), f"live-{info.get('id')}", info
    return platform_of(info), str(info.get("id")), info


def _create_live(body: RunIn, url: str, platform: str, vid: str, info: dict) -> dict:
    for r in db.runs(200):
        st = runner.state(r["pk"]) or {}
        if st.get("live") and r["platform"] == platform and r["video_id"] == vid \
                and (st.get("status") in ("live", "review", "queued_render", "rendering") or runner.live.running(r["pk"])):
            return {"attach": r["pk"]}
    if body.live is None:  # first step: tell the page it's live so it can show the live options
        d = live_defaults()
        return {"live_offer": {"title": info.get("title"), "channel": info.get("uploader") or info.get("channel"),
                               "platform": platform, "defaults": d, "est": EST_MBPS,
                               "pick_every_min": lcfg()["pick_every_min"]}}
    opts = body.live.model_dump() if hasattr(body.live, "model_dump") else body.live.dict()
    if opts["quality"] not in ("1080p", "720p", "480p"):
        raise HTTPException(400, "quality must be 1080p, 720p or 480p")
    if not shutil.which("streamlink") and not (Path(sys.executable).parent / ("streamlink.exe" if IS_WINDOWS else "streamlink")).exists():
        raise HTTPException(400, "streamlink is missing: run setup again (or pip install streamlink in the venv).")
    pk = db.create_run(url, body.extra_prompt.strip(), body.desc_link, platform, vid)
    runner.submit_live(pk, url, opts)
    st = runner.state(pk)
    st["video"] = {"title": info.get("title"), "channel": info.get("uploader") or info.get("channel"),
                   "platform": platform, "duration": 0, "video_id": vid}
    return {"run": pk}


@app.post("/api/runs")
def create_run(body: RunIn):
    url = body.url.strip()
    platform, vid, info = probe(url)
    if info.get("is_live"):
        return _create_live(body, url, platform, vid, info)
    nvid = norm_video_id(platform, vid)
    for r in db.runs(200):
        if r["status"] in ACTIVE and r["platform"] == platform and norm_video_id(platform, r["video_id"] or "") == nvid:
            return {"attach": r["pk"]}
    video = db.video_by_key(platform, vid)
    if video is not None and not body.force:
        n = db.rendered_count(video["pk"])
        if n:
            return {"duplicate": {"video_pk": video["pk"], "title": video["title"], "channel": video["channel"],
                                  "clips": n}}
    pk = db.create_run(url, body.extra_prompt.strip(), body.desc_link, platform, nvid)
    runner.submit(pk, url)
    st = runner.state(pk)
    st["video"] = {"title": info.get("title"), "channel": info.get("uploader") or info.get("channel"),
                   "platform": platform, "duration": info.get("duration"), "video_id": vid}
    return {"run": pk}


@app.get("/api/runs/{pk}")
def get_run(pk: int):
    st = runner.state(pk)
    if not st:
        raise HTTPException(404, "unknown run")
    return st


@app.get("/api/runs/{pk}/events")
async def run_events(pk: int, request: Request):
    if not runner.state(pk):
        raise HTTPException(404, "unknown run")
    q = runner.subscribe(pk)

    async def gen():
        try:
            yield f"data: {json.dumps(runner.state(pk), ensure_ascii=False)}\n\n"
            while not await request.is_disconnected():
                try:
                    payload = await asyncio.to_thread(q.get, True, 10)
                except queue.Empty:
                    yield ": ping\n\n"
                    continue
                yield f"data: {payload}\n\n"
        finally:
            runner.unsubscribe(pk, q)

    return StreamingResponse(gen(), media_type="text/event-stream",
                             headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})


@app.get("/api/runs/{pk}/heat")
def run_heat(pk: int):
    """The stream's moment score over time, squeezed to <= 600 bars (max per bar) and scaled 0-1."""
    st = runner.state(pk)
    if not st or not st.get("job_id"):
        raise HTTPException(404, "no job yet")
    path = jobs_dir() / st["job_id"] / "signals.json"
    if not path.exists():
        raise HTTPException(404, "not scored yet")
    v = np.asarray(read_json(path)["combined"], dtype=float)
    n = min(600, len(v))
    bars = [float(chunk.max()) for chunk in np.array_split(v, n)] if n else []
    arr = np.asarray(bars)
    lo, hi = (np.percentile(arr, 5), np.percentile(arr, 99.5)) if len(arr) else (0, 1)
    scaled = np.clip((arr - lo) / max(1e-6, hi - lo), 0, 1) if len(arr) else arr
    return {"duration": len(v), "bars": [round(float(x), 3) for x in scaled]}


class ApproveIn(BaseModel):
    clip_ids: list[str]


@app.post("/api/runs/{pk}/render")
def approve(pk: int, body: ApproveIn):
    try:
        runner.approve(pk, body.clip_ids)
    except ValueError as e:
        raise HTTPException(400, str(e))
    return {"ok": True}


@app.post("/api/runs/{pk}/cancel")
def cancel(pk: int):
    runner.cancel(pk)
    return {"ok": True}


class LiveSettingsIn(BaseModel):
    audio: Optional[bool] = None
    quality: Optional[str] = None
    game_mode: Optional[bool] = None


def _live_run(pk: int) -> dict:
    st = runner.state(pk)
    if not st or not st.get("live"):
        raise HTTPException(404, "not a live run")
    return st


@app.post("/api/runs/{pk}/live")
def live_settings(pk: int, body: LiveSettingsIn):
    st = _live_run(pk)
    if st["live"].get("ended"):
        raise HTTPException(400, "The stream has already ended.")
    changes = body.model_dump() if hasattr(body, "model_dump") else body.dict()
    return {"live": runner.live.set_settings(pk, changes)}


@app.post("/api/runs/{pk}/pick-now")
def live_pick_now(pk: int):
    st = _live_run(pk)
    if not runner.live.running(pk) and not st["live"].get("ended"):
        raise HTTPException(400, "The recorder isn't running. Press Resume first.")
    runner.live.request_round(pk, "manual")
    return {"ok": True}


@app.post("/api/runs/{pk}/render-now")
def live_render_now(pk: int):
    _live_run(pk)
    runner.live_render_now(pk)
    return {"ok": True}


@app.post("/api/runs/{pk}/stop")
def live_stop(pk: int):
    _live_run(pk)
    if not runner.live.running(pk):
        raise HTTPException(400, "The recorder isn't running.")
    runner.live.stop(pk)
    return {"ok": True}


@app.post("/api/runs/{pk}/resume")
def resume(pk: int):
    try:
        runner.resume(pk)
    except ValueError as e:
        raise HTTPException(400, str(e))
    return {"ok": True}


# ------------------------------------------------------------------ library
@app.get("/api/library")
def library():
    return {"creators": db.library(), "default_link": db.setting("desc_link")}


@app.post("/api/rescan")
def rescan():
    return {"clips": db.sync_all()}


class UploadIn(BaseModel):
    on: bool


@app.post("/api/clips/{pk}/uploads/{platform}")
def set_upload(pk: int, platform: str, body: UploadIn):
    if platform not in UPLOAD_PLATFORMS:
        raise HTTPException(400, "unknown platform")
    if not db.clip_paths(pk):
        raise HTTPException(404, "unknown clip")
    return {"uploaded_at": db.set_upload(pk, platform, body.on)}


class VideoIn(BaseModel):
    desc_link: Optional[str] = None  # None = back to the global default


@app.put("/api/videos/{pk}")
def put_video(pk: int, body: VideoIn):
    db.set_video_link(pk, None if body.desc_link is None else body.desc_link.strip())
    return {"ok": True}


def _clip_file(pk: int, kind: str) -> Path:
    row = db.clip_paths(pk)
    path = row and (row["mp4_path"] if kind == "video" else row["thumb_path"])
    if not path:
        raise HTTPException(404, "no such file")
    p = Path(path).resolve()
    if jobs_dir().resolve() not in p.parents or not p.exists():
        raise HTTPException(404, "no such file")
    return p


@app.get("/media/clips/{pk}/{kind}")
def media(pk: int, kind: str):
    if kind not in ("video", "thumb"):
        raise HTTPException(404)
    return FileResponse(_clip_file(pk, kind))


@app.post("/api/clips/{pk}/reveal")
def reveal(pk: int):
    """Open the file manager with the clip's mp4 selected."""
    p = _clip_file(pk, "video")
    if IS_WINDOWS:
        subprocess.Popen(f'explorer /select,"{p}"')  # string form: explorer needs the quotes after the comma
    elif sys.platform == "darwin":
        subprocess.Popen(["open", "-R", str(p)])
    else:
        subprocess.Popen(["xdg-open", str(p.parent)])
    return {"ok": True}


# ------------------------------------------------------------------ deleting (permanent)
BUSY = ("queued", "running", "queued_render", "rendering", "live")


def _job_busy(job_id: Optional[str], platform: Optional[str] = None, video_id: Optional[str] = None,
              statuses: tuple = BUSY) -> None:
    for r in db.runs_for(job_id, platform, video_id):
        st = runner.state(r["pk"]) or {}
        if (st.get("status") or r["status"]) in statuses:
            raise HTTPException(409, "A run for this video is still working. Cancel it or let it finish first.")


def _job_dir_or_404(job_id: str) -> Path:
    try:
        return cleanup.safe_job_dir(job_id)
    except cleanup.Refused as e:
        raise HTTPException(400, str(e))


@app.delete("/api/clips/{pk}")
def delete_clip(pk: int):
    row = db.clip(pk)
    if not row:
        raise HTTPException(404, "unknown clip")
    _job_busy(row["job_id"], row["platform"], row["vid"], statuses=("queued_render", "rendering"))
    jd = _job_dir_or_404(row["job_id"])
    res = cleanup.delete_clip(jd, row["clip_id"])
    if not res["failed"]:
        db.delete_clip_row(pk)
    return res


@app.get("/api/videos/{pk}/usage")
def video_usage(pk: int):
    v = db.video(pk)
    if not v:
        raise HTTPException(404, "unknown video")
    jd = _job_dir_or_404(v["job_id"])
    return {**cleanup.usage(jd), "rerender_after_free": cleanup.rerender_after_free(jd)}


@app.post("/api/videos/{pk}/free-space")
def video_free_space(pk: int):
    v = db.video(pk)
    if not v:
        raise HTTPException(404, "unknown video")
    _job_busy(v["job_id"], v["platform"], v["video_id"])
    return cleanup.free_space(_job_dir_or_404(v["job_id"]))


def _delete_job_everywhere(job_id: Optional[str], platform: Optional[str], video_id: Optional[str]) -> dict:
    res = {"freed": 0, "failed": []}
    if job_id:
        res = cleanup.delete_job(_job_dir_or_404(job_id))
    if res["failed"]:
        return res  # keep the library rows while files are still there
    if platform and video_id:
        v = db.video_by_key(platform, video_id)
        if v is not None:
            db.delete_video(v["pk"])
    for r in db.runs_for(job_id, platform, video_id):
        db.delete_run(r["pk"])
        runner.forget(r["pk"])
    return res


@app.delete("/api/videos/{pk}")
def delete_video(pk: int):
    v = db.video(pk)
    if not v:
        raise HTTPException(404, "unknown video")
    _job_busy(v["job_id"], v["platform"], v["video_id"])
    return _delete_job_everywhere(v["job_id"], v["platform"], v["video_id"])


@app.get("/api/runs/{pk}/usage")
def run_usage(pk: int):
    st = runner.state(pk)
    if not st:
        raise HTTPException(404, "unknown run")
    if not st.get("job_id"):
        return {"total": 0, "clips": 0, "working": 0, "archive": 0, "other": 0}
    jd = _job_dir_or_404(st["job_id"])
    return cleanup.usage(jd) if jd.exists() else {"total": 0, "clips": 0, "working": 0, "archive": 0, "other": 0}


@app.post("/api/runs/{pk}/free-space")
def run_free_space(pk: int):
    """After a live run: delete the recording and other working files, keep the rendered clips."""
    st = runner.state(pk)
    if not st or not st.get("job_id"):
        raise HTTPException(404, "unknown run")
    if st["status"] in BUSY or runner.live.running(pk):
        raise HTTPException(409, "This run is still working.")
    return cleanup.free_space(_job_dir_or_404(st["job_id"]))


@app.delete("/api/runs/{pk}")
def delete_run(pk: int):
    """A finished / failed / cancelled run, its job folder and (if it got that far) its clips in the library."""
    st = runner.state(pk)
    row = db.run(pk)
    if not st or not row:
        raise HTTPException(404, "unknown run")
    if st["status"] in BUSY:
        raise HTTPException(409, "This run is still working. Cancel it first.")
    job_id = st.get("job_id") or row["job_id"]
    video = st.get("video") or {}
    platform, vid = row["platform"] or video.get("platform"), row["video_id"] or video.get("video_id")
    _job_busy(job_id, platform, vid)
    if not job_id or not (jobs_dir() / job_id).exists():
        db.delete_run(pk)
        runner.forget(pk)
        return {"freed": 0, "failed": []}
    return _delete_job_everywhere(job_id, platform, vid)


@app.exception_handler(HTTPException)
async def http_error(request: Request, exc: HTTPException):
    return JSONResponse({"error": exc.detail}, status_code=exc.status_code)
