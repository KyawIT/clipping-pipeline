"""SQLite metadata store for the web app. The videos themselves stay on disk in jobs/<job>/out/.

Identity: a video is (platform, video_id) - the YouTube id, the Twitch VOD id (without the leading "v"), the Kick
VOD id - so the same stream can never show up twice. A clip is (video, clip_id). The DB is rebuilt from the job
folders at any time (sync); only upload checkmarks, description links and run history live here alone.
"""
from __future__ import annotations

import json
import sqlite3
import threading
import time
from pathlib import Path
from typing import Any, Optional

from pipeline.common import jobs_dir, parse_ts, read_json

UPLOAD_PLATFORMS = ("tiktok", "reels", "shorts")

SCHEMA = """
CREATE TABLE IF NOT EXISTS videos (
    pk INTEGER PRIMARY KEY,
    platform TEXT NOT NULL,
    video_id TEXT NOT NULL,
    job_id TEXT NOT NULL,
    title TEXT, channel TEXT, url TEXT, duration REAL,
    desc_link TEXT,                       -- NULL = use the global default, '' = no link
    created_at REAL NOT NULL,
    UNIQUE (platform, video_id)
);
CREATE TABLE IF NOT EXISTS clips (
    pk INTEGER PRIMARY KEY,
    video_pk INTEGER NOT NULL REFERENCES videos(pk) ON DELETE CASCADE,
    clip_id TEXT NOT NULL,
    start REAL, "end" REAL,
    title TEXT, description TEXT, hashtags TEXT, hook TEXT,
    score REAL, reason TEXT, layout TEXT,
    mp4_path TEXT, thumb_path TEXT,
    rendered_at REAL,
    UNIQUE (video_pk, clip_id)
);
CREATE TABLE IF NOT EXISTS uploads (
    clip_pk INTEGER NOT NULL REFERENCES clips(pk) ON DELETE CASCADE,
    platform TEXT NOT NULL,
    uploaded_at REAL NOT NULL,
    PRIMARY KEY (clip_pk, platform)
);
CREATE TABLE IF NOT EXISTS runs (
    pk INTEGER PRIMARY KEY,
    url TEXT NOT NULL,
    platform TEXT, video_id TEXT, job_id TEXT,
    status TEXT NOT NULL,
    extra_prompt TEXT,
    desc_link TEXT,                       -- NULL = keep the global default
    selected TEXT,                        -- json list of clip ids chosen in review
    state TEXT,                           -- json snapshot shown by the progress page
    error TEXT,
    created_at REAL NOT NULL, updated_at REAL NOT NULL
);
CREATE TABLE IF NOT EXISTS settings (key TEXT PRIMARY KEY, value TEXT);
"""


def norm_video_id(platform: str, video_id: str) -> str:
    vid = str(video_id)
    if platform == "twitch" and vid[:1] == "v" and vid[1:].isdigit():
        vid = vid[1:]
    return vid


class DB:
    def __init__(self, path: Path):
        self.path = path
        self.lock = threading.RLock()
        self.conn = sqlite3.connect(str(path), check_same_thread=False)
        self.conn.row_factory = sqlite3.Row
        with self.lock:
            self.conn.execute("PRAGMA journal_mode=WAL")
            self.conn.execute("PRAGMA foreign_keys=ON")
            self.conn.executescript(SCHEMA)
            self.conn.commit()

    # ------------------------------------------------------------ low level
    def q(self, sql: str, args: tuple = ()) -> list[sqlite3.Row]:
        with self.lock:
            return self.conn.execute(sql, args).fetchall()

    def x(self, sql: str, args: tuple = ()) -> int:
        with self.lock:
            cur = self.conn.execute(sql, args)
            self.conn.commit()
            return cur.lastrowid

    # ------------------------------------------------------------ settings
    def setting(self, key: str, default: str = "") -> str:
        rows = self.q("SELECT value FROM settings WHERE key=?", (key,))
        return rows[0]["value"] if rows else default

    def set_setting(self, key: str, value: str) -> None:
        self.x("INSERT INTO settings(key, value) VALUES(?, ?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",
               (key, value))

    # ------------------------------------------------------------ sync from disk
    def sync_all(self) -> int:
        n = 0
        for jd in sorted(jobs_dir().iterdir()):
            if (jd / "meta.json").exists():
                n += self.sync_job(jd)
        return n

    def sync_job(self, jd: Path) -> int:
        """Upsert one job folder. Returns the number of rendered clips found."""
        meta = read_json(jd / "meta.json")
        platform = meta.get("platform", "local")
        vid = norm_video_id(platform, meta.get("video_id") or jd.name)
        now = time.time()
        with self.lock:
            self.conn.execute(
                """INSERT INTO videos(platform, video_id, job_id, title, channel, url, duration, created_at)
                   VALUES(?,?,?,?,?,?,?,?)
                   ON CONFLICT(platform, video_id) DO UPDATE SET job_id=excluded.job_id, title=excluded.title,
                     channel=excluded.channel, url=excluded.url, duration=excluded.duration""",
                (platform, vid, jd.name, meta.get("title"), meta.get("channel"), meta.get("source"),
                 meta.get("duration"), now))
            vpk = self.conn.execute("SELECT pk FROM videos WHERE platform=? AND video_id=?",
                                    (platform, vid)).fetchone()["pk"]
            clips_file = jd / "clips.json"
            data = read_json(clips_file) if clips_file.exists() else {"clips": []}
            clips = data["clips"] if isinstance(data, dict) else data
            found = 0
            for i, c in enumerate(clips, 1):
                cid = c.get("id") or f"clip_{i:02d}"
                mp4 = jd / "out" / f"{cid}.mp4"
                if not mp4.exists():
                    continue
                found += 1
                thumb = jd / "out" / f"{cid}_thumb.jpg"
                s, e = round(parse_ts(c["start"]), 2), round(parse_ts(c["end"]), 2)
                old = self.conn.execute('SELECT pk, start, "end" FROM clips WHERE video_pk=? AND clip_id=?',
                                        (vpk, cid)).fetchone()
                vals = (s, e, c.get("title"), c.get("description"), json.dumps(c.get("hashtags") or []),
                        c.get("hook"), c.get("score"), c.get("reason"), c.get("layout"), str(mp4),
                        str(thumb) if thumb.exists() else None, mp4.stat().st_mtime)
                if old is None:
                    self.conn.execute(
                        """INSERT INTO clips(start, "end", title, description, hashtags, hook, score, reason, layout,
                             mp4_path, thumb_path, rendered_at, video_pk, clip_id) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                        vals + (vpk, cid))
                else:
                    if abs((old["start"] or 0) - s) > 0.05 or abs((old["end"] or 0) - e) > 0.05:
                        # same id, different moment: it is a new video, so it hasn't been uploaded anywhere
                        self.conn.execute("DELETE FROM uploads WHERE clip_pk=?", (old["pk"],))
                    self.conn.execute(
                        """UPDATE clips SET start=?, "end"=?, title=?, description=?, hashtags=?, hook=?, score=?,
                             reason=?, layout=?, mp4_path=?, thumb_path=?, rendered_at=? WHERE pk=?""",
                        vals + (old["pk"],))
            self.conn.commit()
        return found

    # ------------------------------------------------------------ library
    def video_by_key(self, platform: str, video_id: str) -> Optional[sqlite3.Row]:
        rows = self.q("SELECT * FROM videos WHERE platform=? AND video_id=?",
                      (platform, norm_video_id(platform, video_id)))
        return rows[0] if rows else None

    def rendered_count(self, video_pk: int) -> int:
        return self.q("SELECT COUNT(*) AS n FROM clips WHERE video_pk=?", (video_pk,))[0]["n"]

    def library(self) -> list[dict[str, Any]]:
        """Creators -> videos -> clips, newest first. Only clips whose files still exist."""
        default_link = self.setting("desc_link")
        uploads: dict[int, dict[str, float]] = {}
        for r in self.q("SELECT clip_pk, platform, uploaded_at FROM uploads"):
            uploads.setdefault(r["clip_pk"], {})[r["platform"]] = r["uploaded_at"]
        clips_by_video: dict[int, list[dict]] = {}
        for c in self.q('SELECT * FROM clips ORDER BY clip_id'):
            if not c["mp4_path"] or not Path(c["mp4_path"]).exists():
                continue
            clips_by_video.setdefault(c["video_pk"], []).append({
                "pk": c["pk"], "clip_id": c["clip_id"], "start": c["start"], "end": c["end"],
                "duration": round((c["end"] or 0) - (c["start"] or 0), 1),
                "title": c["title"] or "", "description": c["description"] or "",
                "hashtags": json.loads(c["hashtags"] or "[]"), "hook": c["hook"] or "",
                "score": c["score"], "reason": c["reason"] or "", "layout": c["layout"],
                "has_thumb": bool(c["thumb_path"] and Path(c["thumb_path"]).exists()),
                "rendered_at": c["rendered_at"], "uploads": uploads.get(c["pk"], {}),
            })
        creators: dict[str, dict] = {}
        for v in self.q("SELECT * FROM videos ORDER BY created_at DESC"):
            clips = clips_by_video.get(v["pk"])
            if not clips:
                continue
            link = default_link if v["desc_link"] is None else v["desc_link"]
            key = f"{v['platform']}:{(v['channel'] or 'unknown').lower()}"
            creator = creators.setdefault(key, {"channel": v["channel"] or "unknown", "platform": v["platform"],
                                                "videos": []})
            creator["videos"].append({
                "pk": v["pk"], "platform": v["platform"], "video_id": v["video_id"], "job_id": v["job_id"],
                "title": v["title"] or v["job_id"], "url": v["url"], "duration": v["duration"],
                "desc_link": v["desc_link"], "effective_link": link or "", "created_at": v["created_at"],
                "clips": clips,
            })
        return list(creators.values())

    def clip_paths(self, clip_pk: int) -> Optional[sqlite3.Row]:
        rows = self.q("SELECT mp4_path, thumb_path FROM clips WHERE pk=?", (clip_pk,))
        return rows[0] if rows else None

    def set_upload(self, clip_pk: int, platform: str, on: bool) -> Optional[float]:
        if platform not in UPLOAD_PLATFORMS:
            raise ValueError(platform)
        if on:
            now = time.time()
            self.x("INSERT OR REPLACE INTO uploads(clip_pk, platform, uploaded_at) VALUES(?,?,?)",
                   (clip_pk, platform, now))
            return now
        self.x("DELETE FROM uploads WHERE clip_pk=? AND platform=?", (clip_pk, platform))
        return None

    def set_video_link(self, video_pk: int, link: Optional[str]) -> None:
        self.x("UPDATE videos SET desc_link=? WHERE pk=?", (link, video_pk))

    # ------------------------------------------------------------ runs
    def create_run(self, url: str, extra_prompt: str, desc_link: Optional[str],
                   platform: Optional[str], video_id: Optional[str]) -> int:
        now = time.time()
        return self.x("""INSERT INTO runs(url, platform, video_id, status, extra_prompt, desc_link, created_at, updated_at)
                         VALUES(?,?,?,?,?,?,?,?)""", (url, platform, video_id, "queued", extra_prompt, desc_link, now, now))

    def update_run(self, pk: int, **fields: Any) -> None:
        if not fields:
            return
        fields["updated_at"] = time.time()
        for k in ("state", "selected"):
            if k in fields and not isinstance(fields[k], (str, type(None))):
                fields[k] = json.dumps(fields[k], ensure_ascii=False)
        cols = ", ".join(f"{k}=?" for k in fields)
        self.x(f"UPDATE runs SET {cols} WHERE pk=?", tuple(fields.values()) + (pk,))

    def run(self, pk: int) -> Optional[sqlite3.Row]:
        rows = self.q("SELECT * FROM runs WHERE pk=?", (pk,))
        return rows[0] if rows else None

    def runs(self, limit: int = 30) -> list[sqlite3.Row]:
        return self.q("SELECT * FROM runs ORDER BY pk DESC LIMIT ?", (limit,))

    def mark_interrupted(self) -> None:
        self.x("UPDATE runs SET status='interrupted' WHERE status IN ('queued', 'running', 'queued_render', 'rendering')")
