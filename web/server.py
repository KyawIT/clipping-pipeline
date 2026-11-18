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

from .db import DB, UPLOAD_PLATFORMS, norm_video_id
from .runner import Runner

STATIC = Path(__file__).parent / "static"
ACTIVE = ("queued", "running", "review", "queued_render", "rendering")

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
class RunIn(BaseModel):
    url: str
    extra_prompt: str = ""
    desc_link: Optional[str] = None  # None = use the global default, "" = no link
    force: bool = False


def probe(url: str) -> tuple[str, str, dict]:
    """(platform, video_id, info) for a link, via yt-dlp. Raises HTTPException with yt-dlp's reason."""
    if _is_local_file(url):
        p = Path(url).expanduser()
        return "local", p.stem, {"title": p.stem, "uploader": "local"}
    if not url.startswith(("http://", "https://")):
        raise HTTPException(400, "Paste a full link (https://...) or a path to a video file.")
    p = subprocess.run([which_or_die("yt-dlp"), "-J", "--no-playlist", "--skip-download", url],
                       capture_output=True, text=True, encoding="utf-8", errors="replace")
    if p.returncode != 0:
        reason = next((ln for ln in reversed(p.stderr.splitlines()) if "ERROR" in ln), p.stderr.strip()[-300:])
        raise HTTPException(400, f"Couldn't open this link: {reason or 'yt-dlp failed'}")
    info = json.loads(p.stdout)
    if info.get("is_live"):
        raise HTTPException(400, "This stream is still live. Paste the VOD link once the stream has ended.")
    return platform_of(info), str(info.get("id")), info


@app.post("/api/runs")
def create_run(body: RunIn):
    url = body.url.strip()
    platform, vid, info = probe(url)
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


@app.exception_handler(HTTPException)
async def http_error(request: Request, exc: HTTPException):
    return JSONResponse({"error": exc.detail}, status_code=exc.status_code)
