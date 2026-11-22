"""Live streams: record while the stream runs, transcribe as it goes, keep the moment scores up to date.

    clip.py live <url>          start (or resume, given an existing live job id)

How it works (one long-running process per live job, started by the web app or by hand):
  * Recorder: streamlink (filters Twitch ads, follows the live playlist) piped into ffmpeg's segment muxer, which
    writes 5-minute .ts files to jobs/<job>/rec/. "audio" mode keeps only the sound (Twitch audio_only stream, or the
    lowest quality with the picture dropped) for ~0.16 Mbps instead of ~3 Mbps; the picture then comes from the VOD.
    Every restart (reconnect, quality switch, resume) is a new "part"; rec/segments.json maps every file to
    recording time and wall-clock time.
  * Chat: Twitch IRC (anonymous, read-only) or YouTube live chat via yt-dlp -> chat_live.jsonl (wall clock),
    converted to the normal chat.json (content_offset_seconds) for the scorer.
  * Processor: every `live.chunk_min` minutes of new recording, appends the audio to audio.wav, transcribes only the
    new part (with a 20 s overlap so no word is cut at the seam), rescores the whole timeline and reports a "hot"
    moment when the newest stretch has one of the strongest peaks of the stream.
  * Control: rec/control.json (written by the web app) switches audio/quality/game mode without stopping;
    rec/stop ends the recording, processes the tail and exits. Ctrl+C does the same.

Everything downstream (candidates, clips.json, render) is the normal pipeline: media.py cuts clips straight from
the recorded segments, or from the VOD for stretches recorded as audio only.
"""
from __future__ import annotations

import json
import os
import random
import re
import signal
import socket
import subprocess
import sys
import threading
import time
import wave
from pathlib import Path
from urllib.parse import urlparse

import numpy as np

from .common import (CFG, IS_WINDOWS, ffprobe, fmt_ts, jobs_dir, log, lower_own_priority, progress, read_json,
                     which_or_die, write_json)
from .download import STREAM_PLATFORMS, _slug, platform_of

SR = 16000
OVERLAP_SEC = 20.0
DEFAULTS = {
    "video": "record", "quality": "720p", "segment_sec": 300, "chunk_min": 10, "pick_every_min": 45,
    "hot_trigger": True, "hot_z": 3.0, "min_pick_gap_min": 15, "clips_per_round": 3, "round_candidates": 10,
    "game_mode": True, "transcribe_device": "cpu", "cpu_threads": 4, "reconnect_min": 10,
}
QUALITY = {"1080p": "1080p60,1080p,best", "720p": "720p60,720p,best", "480p": "480p,360p,worst"}
# rough bitrates for the estimate shown in the web app (Mbps)
EST_MBPS = {"1080p": 6.0, "720p": 3.0, "480p": 1.4, "audio": 0.16}


def lcfg() -> dict:
    c = {**DEFAULTS, **(CFG.get("live") or {})}
    for key in ("segment_sec", "chunk_min"):  # env overrides, handy for quick tests with short files
        env = os.environ.get(f"CLIP_LIVE_{key.upper()}")
        if env:
            c[key] = float(env)
    return c


_print_lock = threading.Lock()


def say(stage: str, pct=None, msg=None, **data) -> None:
    with _print_lock:
        progress(stage, pct, msg, **data)


# ------------------------------------------------------------------ probing

def probe(url: str) -> dict | None:
    """yt-dlp metadata of a live URL, or None when it isn't live (offline channel / ended stream)."""
    p = subprocess.run([which_or_die("yt-dlp"), "-J", "--no-playlist", "--skip-download", url],
                       capture_output=True, text=True, encoding="utf-8", errors="replace")
    if p.returncode != 0:
        return None
    try:
        info = json.loads(p.stdout)
    except ValueError:
        return None
    return info if info.get("is_live") else None


def channel_login(url: str, info: dict | None = None) -> str:
    """Twitch / Kick channel name from the URL (twitch.tv/<login>), else yt-dlp's uploader id."""
    path = [p for p in urlparse(url).path.split("/") if p]
    if path and path[0] not in ("videos", "watch", "live"):
        return path[0].lower()
    return str((info or {}).get("uploader_id") or (info or {}).get("channel_id") or "").lower()


def is_live_job(jd: Path) -> bool:
    return (jd / "meta.json").exists() and bool(read_json(jd / "meta.json").get("live"))


def create_job(url: str, info: dict) -> tuple[str, Path]:
    platform = platform_of(info)
    sid = str(info.get("id") or int(time.time()))
    job_id = f"{platform}_live_{_slug(sid)}"
    jd = jobs_dir() / job_id
    jd.mkdir(parents=True, exist_ok=True)
    if not (jd / "meta.json").exists():
        meta = {
            # "live-" keeps a YouTube live job apart from a later VOD job of the same video id in the library
            "source": url, "platform": platform, "video_id": f"live-{sid}", "stream_id": sid, "live": True,
            "title": info.get("title") or info.get("description") or "Live stream",
            "channel": info.get("uploader") or info.get("channel") or channel_login(url, info) or "unknown",
            "channel_id": info.get("uploader_id") or info.get("channel_id") or channel_login(url, info) or "unknown",
            "login": channel_login(url, info),
            "stream_started_at": info.get("timestamp") or info.get("release_timestamp"),
            "mode": "stream" if platform in STREAM_PLATFORMS else "podcast",
            "duration": 0.0,
        }
        if info.get("width") and info.get("height"):
            meta.update(width=info["width"], height=info["height"], fps=info.get("fps") or 30)
        write_json(jd / "meta.json", meta)
    return job_id, jd


# ------------------------------------------------------------------ recording

class Recording:
    """rec/segments.json bookkeeping: closed segment files with recording time + wall clock."""

    def __init__(self, jd: Path):
        self.jd = jd
        self.dir = jd / "rec"
        self.dir.mkdir(parents=True, exist_ok=True)
        self.path = self.dir / "segments.json"
        self.data = read_json(self.path) if self.path.exists() else {"parts": [], "segments": []}
        self.seen: dict[str, float] = {}  # file -> wall clock when it first appeared (its content start)
        self.lock = threading.RLock()

    @property
    def end(self) -> float:
        segs = self.data["segments"]
        return segs[-1]["end"] if segs else 0.0

    def new_part(self, audio: bool, quality: str) -> int:
        with self.lock:
            n = len(self.data["parts"])
            self.data["parts"].append({"n": n, "start": self.end, "wall": time.time(), "audio": audio,
                                       "quality": quality})
            self.save()
            return n

    def save(self) -> None:
        write_json(self.path, self.data)

    def scan(self, part: int) -> int:
        """Pick up newly closed segments of `part` (ffmpeg lists a file in the csv once it is closed)."""
        with self.lock:
            return self._scan(part)

    def _scan(self, part: int) -> int:
        for f in self.dir.glob(f"p{part:02d}_*.ts"):
            self.seen.setdefault(f.name, time.time())
        csv = self.dir / f"p{part:02d}.csv"
        if not csv.exists():
            return 0
        known = {s["file"] for s in self.data["segments"]}
        added = 0
        for line in csv.read_text(encoding="utf-8", errors="replace").splitlines():
            name = line.split(",")[0].strip()
            if not name or name in known:
                continue
            f = self.dir / name
            try:
                info = ffprobe(f)
            except (subprocess.CalledProcessError, ValueError, KeyError):
                info = {"duration": 0.0}
            dur = float(info.get("duration") or 0.0)
            if dur < 0.5:  # an empty tail file after a disconnect
                f.unlink(missing_ok=True)
                known.add(name)
                continue
            with self.lock:
                start = self.end
                self.data["segments"].append({
                    "file": name, "part": part, "start": round(start, 3), "end": round(start + dur, 3),
                    "kind": "av" if info.get("width") else "audio",
                    "wall": round(self.seen.get(name, time.time() - dur), 3),
                })
                if info.get("width"):
                    self.data["segments"][-1].update(width=info["width"], height=info["height"])
                self.save()
            known.add(name)
            added += 1
        return added

    def rec_time(self, wall: float) -> float | None:
        """Wall clock -> recording time, through the segment anchors. None outside the recording."""
        segs = self.data["segments"]
        best = None
        for s in segs:
            if s["wall"] <= wall:
                best = s
            else:
                break
        if best is None:
            return None
        t = best["start"] + (wall - best["wall"])
        return t if t <= best["end"] + 2.0 else None

    def edge(self) -> float:
        """Recording time right now: closed segments plus how long the open one has been written."""
        with self.lock:
            closed = {s["file"] for s in self.data["segments"]}
            open_walls = [w for f, w in self.seen.items() if f not in closed and (self.dir / f).exists()]
        return self.end + (max(0.0, time.time() - max(open_walls)) if open_walls else 0.0)

    def disk_bytes(self) -> int:
        return sum(f.stat().st_size for f in self.dir.glob("*.ts") if f.exists())


class Recorder:
    """streamlink -> ffmpeg segment muxer for one part."""

    def __init__(self, jd: Path, rec: Recording, url: str, platform: str, audio: bool, quality: str):
        self.jd, self.rec, self.url, self.platform = jd, rec, url, platform
        self.audio, self.quality = audio, quality
        self.part = -1
        self.src: subprocess.Popen | None = None
        self.mux: subprocess.Popen | None = None

    def _source_cmd(self) -> list[str]:
        fake = os.environ.get("CLIP_FAKE_LIVE")
        if fake:  # offline test: play a local file in real time, continuing where the last part stopped
            return ["ffmpeg", "-hide_banner", "-loglevel", "error", "-re", "-ss", f"{self.rec.end:.3f}",
                    "-i", fake, "-c", "copy", "-f", "mpegts", "pipe:1"]
        if self.audio:
            q = "audio_only,audio,worst" if self.platform == "twitch" else "worst"
        else:
            q = QUALITY.get(self.quality, QUALITY["720p"])
        # Streamlink's Twitch plugin filters ad segments by itself (the old --twitch-disable-ads is gone)
        cmd = [which_or_die("streamlink", "pip install streamlink (or run setup again)"), "--stdout",
               "--retry-streams", "10", "--retry-max", "3", "--retry-open", "3", "--stream-segment-threads", "1",
               "--loglevel", "warning"]
        return cmd + [self.url, q]

    def start(self) -> None:
        self.part = self.rec.new_part(self.audio, self.quality)
        seg = lcfg()["segment_sec"]
        maps = ["-map", "0:a:0"] if self.audio else ["-map", "0:v:0?", "-map", "0:a:0?"]
        mux_cmd = ["ffmpeg", "-hide_banner", "-loglevel", "error", "-fflags", "+genpts", "-i", "pipe:0", *maps,
                   "-c", "copy", "-f", "segment", "-segment_time", str(seg), "-reset_timestamps", "1",
                   "-segment_list", str(self.rec.dir / f"p{self.part:02d}.csv"), "-segment_list_type", "csv",
                   str(self.rec.dir / f"p{self.part:02d}_%05d.ts")]
        log(f"Recording part {self.part} ({'audio only' if self.audio else self.quality})")
        self.src = subprocess.Popen(self._source_cmd(), stdout=subprocess.PIPE, stdin=subprocess.DEVNULL)
        self.mux = subprocess.Popen(mux_cmd, stdin=self.src.stdout, stdout=subprocess.DEVNULL)
        self.src.stdout.close()  # ffmpeg owns the pipe now: it gets EOF when streamlink exits

    def alive(self) -> bool:
        return bool(self.mux and self.mux.poll() is None)

    def stop(self) -> None:
        """End this part cleanly: stop the source, let ffmpeg close (and list) the last segment."""
        if self.src and self.src.poll() is None:
            self.src.terminate()
        if self.mux:
            try:
                self.mux.wait(timeout=20)
            except subprocess.TimeoutExpired:
                self.mux.kill()
        if self.src:
            try:
                self.src.wait(timeout=10)
            except subprocess.TimeoutExpired:
                self.src.kill()


# ------------------------------------------------------------------ chat

class Chat:
    """Logs live chat to chat_live.jsonl ({"t": wall clock, "body": text}) in a background thread."""

    def __init__(self, jd: Path, platform: str, url: str, login: str, video_id: str, stop: threading.Event):
        self.jd, self.platform, self.url, self.login, self.vid, self.stop = jd, platform, url, login, video_id, stop
        self.out = jd / "chat_live.jsonl"
        self.count = 0
        self.recent: list[float] = []
        self.proc: subprocess.Popen | None = None

    def start(self) -> None:
        if os.environ.get("CLIP_FAKE_LIVE"):
            return
        if self.platform == "twitch" and self.login:
            threading.Thread(target=self._twitch, daemon=True, name="chat").start()
        elif self.platform == "youtube":
            threading.Thread(target=self._youtube, daemon=True, name="chat").start()
        else:
            log("No live chat reader for this site: moments are scored from the voice only")

    def _write(self, t: float, body: str) -> None:
        with open(self.out, "a", encoding="utf-8") as f:
            f.write(json.dumps({"t": round(t, 3), "body": body}, ensure_ascii=False) + "\n")
        self.count += 1
        self.recent.append(t)
        cutoff = time.time() - 60
        while self.recent and self.recent[0] < cutoff:
            self.recent.pop(0)

    def _twitch(self) -> None:
        """Anonymous read-only IRC (nick justinfanNNNN needs no login)."""
        while not self.stop.is_set():
            try:
                s = socket.create_connection(("irc.chat.twitch.tv", 6667), timeout=30)
                s.sendall(f"NICK justinfan{random.randint(10000, 99999)}\r\nJOIN #{self.login}\r\n".encode())
                buf = b""
                while not self.stop.is_set():
                    try:
                        data = s.recv(65536)
                    except socket.timeout:
                        s.sendall(b"PING :tmi.twitch.tv\r\n")
                        continue
                    if not data:
                        break
                    buf += data
                    *lines, buf = buf.split(b"\r\n")
                    for raw in lines:
                        line = raw.decode("utf-8", "replace")
                        if line.startswith("PING"):
                            s.sendall(("PONG" + line[4:] + "\r\n").encode())
                        elif " PRIVMSG #" in line:
                            body = line.split(" PRIVMSG #", 1)[1].split(" :", 1)
                            if len(body) == 2:
                                self._write(time.time(), body[1])
                s.close()
            except OSError as e:
                log(f"Twitch chat disconnected ({e.__class__.__name__}), reconnecting")
            self.stop.wait(5)

    def _youtube(self) -> None:
        """yt-dlp writes the live chat as one JSON action per line while the stream runs."""
        base = self.jd / "ytchat"
        target = Path(str(base) + ".live_chat.json")
        self.proc = subprocess.Popen([which_or_die("yt-dlp"), "--skip-download", "--write-subs", "--sub-langs",
                                      "live_chat", "--no-playlist", "-o", str(base) + ".%(ext)s", self.url],
                                     stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        pos = 0
        while not self.stop.is_set():
            self.stop.wait(5)
            for p in (target, Path(str(target) + ".part")):
                if not p.exists():
                    continue
                with open(p, "r", encoding="utf-8", errors="replace") as f:
                    f.seek(pos)
                    for line in f:
                        if not line.endswith("\n"):
                            break
                        pos += len(line.encode("utf-8"))
                        t, body = _yt_chat_item(line)
                        if body:
                            self._write(t or time.time(), body)
                break
        if self.proc and self.proc.poll() is None:
            self.proc.terminate()


def _yt_chat_item(line: str) -> tuple[float | None, str]:
    try:
        ev = json.loads(line)
    except ValueError:
        return None, ""
    found: dict = {}

    def walk(o):
        if isinstance(o, dict):
            if "liveChatTextMessageRenderer" in o:
                found["r"] = o["liveChatTextMessageRenderer"]
                return
            for v in o.values():
                walk(v)
        elif isinstance(o, list):
            for v in o:
                walk(v)

    walk(ev)
    r = found.get("r")
    if not r:
        return None, ""
    runs = (r.get("message") or {}).get("runs") or []
    body = "".join(x.get("text") or (x.get("emoji") or {}).get("shortcuts", [""])[0] for x in runs)
    ts = r.get("timestampUsec")
    return (int(ts) / 1e6 if ts else None), body.strip()


def write_chat_json(jd: Path, rec: Recording, until: float) -> int:
    """chat_live.jsonl (wall clock) -> chat.json in the TwitchDownloader shape the scorer reads."""
    src = jd / "chat_live.jsonl"
    if not src.exists():
        return 0
    comments = []
    with open(src, "r", encoding="utf-8", errors="replace") as f:
        for line in f:
            try:
                m = json.loads(line)
            except ValueError:
                continue
            t = rec.rec_time(float(m["t"]))
            if t is not None and 0 <= t <= until:
                comments.append({"content_offset_seconds": round(t, 2), "message": {"body": m["body"]}})
    write_json(jd / "chat.json", {"comments": comments})
    return len(comments)


# ------------------------------------------------------------------ audio

def _seg_pcm(path: Path, dur: float) -> bytes:
    """16 kHz mono s16le of one recorded segment, padded / trimmed to exactly its listed duration."""
    p = subprocess.run(["ffmpeg", "-v", "error", "-i", str(path), "-vn", "-ac", "1", "-ar", str(SR),
                        "-f", "s16le", "-"], capture_output=True)
    pcm = p.stdout if p.returncode == 0 else b""
    n = int(round(dur * SR)) * 2
    return pcm[:n] + b"\0" * max(0, n - len(pcm))


def _write_wav(path: Path, pcm: bytes) -> None:
    with wave.open(str(path), "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(SR)
        w.writeframes(pcm)


def append_wav(path: Path, pcm: bytes) -> None:
    """Append raw 16 kHz mono PCM to a WAV file in place (patches the RIFF / data sizes)."""
    if not path.exists():
        _write_wav(path, pcm)
        return
    with open(path, "r+b") as f:
        f.seek(0, 2)
        f.write(pcm)
        size = f.tell()
        f.seek(4)
        f.write((size - 8).to_bytes(4, "little"))
        f.seek(40)  # standard 44-byte header written by the wave module
        f.write((size - 44).to_bytes(4, "little"))


def wav_seconds(path: Path) -> float:
    if not path.exists():
        return 0.0
    with wave.open(str(path), "rb") as w:
        return w.getnframes() / w.getframerate()


# ------------------------------------------------------------------ processing

class Processor:
    def __init__(self, jd: Path, rec: Recording, settings: dict):
        self.jd, self.rec, self.settings = jd, rec, settings
        self.state_path = jd / "live_state.json"
        self.state = read_json(self.state_path) if self.state_path.exists() else {"hot": []}
        # audio.wav is the source of truth for how far processing got: it is appended last, after the transcript
        # (re-transcribing a range after a crash replaces those words instead of duplicating them)
        self.state["processed"] = wav_seconds(jd / "audio.wav")

    @property
    def processed(self) -> float:
        return self.state["processed"]

    def save(self) -> None:
        write_json(self.state_path, self.state)

    def due(self, final: bool = False) -> bool:
        backlog = self.rec.end - self.processed
        return backlog >= (1.0 if final else 60.0 * float(lcfg()["chunk_min"]))

    def update(self, final: bool = False) -> None:
        from .signals import build_candidates
        from .transcribe import transcribe_chunk

        segs = self.rec.data["segments"]
        a0, end = self.processed, self.rec.end
        if end - a0 < 1.0:
            return
        a = max(0.0, a0 - OVERLAP_SEC)
        pick = [s for s in segs if s["end"] > a and s["start"] < end]
        pcm = b"".join(_seg_pcm(self.rec.dir / s["file"], s["end"] - s["start"]) for s in pick)
        base = pick[0]["start"]
        lo = int(round((a - base) * SR)) * 2
        mid = int(round((a0 - base) * SR)) * 2
        hi = int(round((end - base) * SR)) * 2
        chunk = self.jd / "live_chunk.wav"
        _write_wav(chunk, pcm[lo:hi])

        log(f"Transcribing {fmt_ts(a0)} - {fmt_ts(end)}")
        say("live", msg=f"Transcribing {fmt_ts(a0)} - {fmt_ts(end)}", step="transcribe")
        game = self.settings.get("game_mode", True)
        device = lcfg()["transcribe_device"] if game else None
        threads = int(lcfg()["cpu_threads"]) if game else None
        added = transcribe_chunk(self.jd, chunk, offset=a, keep_from=a0, device=device, threads=threads)
        chunk.unlink(missing_ok=True)

        # audio.wav last: it marks the chunk as done
        want = int(round((end - a0) * SR)) * 2
        piece = pcm[mid:hi]
        append_wav(self.jd / "audio.wav", piece[:want] + b"\0" * max(0, want - len(piece)))
        meta = read_json(self.jd / "meta.json")
        meta["duration"] = round(end, 3)
        av = next((s for s in reversed(segs) if s.get("width")), None)
        if av and not meta.get("width"):
            meta.update(width=av["width"], height=av["height"])
        if not meta.get("fps"):
            meta["fps"] = 30
        write_json(self.jd / "meta.json", meta)
        chat_n = write_chat_json(self.jd, self.rec, end)
        build_candidates(self.jd, write_md=False)
        self.state["processed"] = end
        hot = self._hot(a0, end, final)
        self.save()
        say("live", step="processed", processed=end, words=added, chat=chat_n, hot=hot)

    def _hot(self, a0: float, end: float, final: bool) -> float | None:
        """A strong new peak whose payoff is already recorded -> its time (the web app starts a pick round)."""
        if not lcfg()["hot_trigger"] or not (self.jd / "signals.json").exists():
            return None
        v = np.asarray(read_json(self.jd / "signals.json")["combined"], dtype=float)
        lo, hi = int(a0), int(end if final else end - 120)
        if hi - lo < 10:
            return None
        peak = lo + int(np.argmax(v[lo:hi]))
        score = float(v[peak])
        cands = read_json(self.jd / "candidates.json") if (self.jd / "candidates.json").exists() else []
        top = sorted((c["score"] for c in cands), reverse=True)[:3]
        if score < float(lcfg()["hot_z"]) or (len(top) >= 3 and score < top[-1]):
            return None
        if any(abs(peak - h) < 120 for h in self.state.get("hot", [])):
            return None
        self.state.setdefault("hot", []).append(peak)
        log(f"Hot moment at {fmt_ts(peak)} (score {score:.1f})")
        return float(peak)


# ------------------------------------------------------------------ VOD (audio-only stretches)

def find_vod(meta: dict) -> str | None:
    """The VOD of this live stream, if the platform has one yet."""
    platform, login = meta.get("platform"), meta.get("login")
    ytdlp = which_or_die("yt-dlp")
    if platform == "youtube":
        if probe(meta["source"]) is None:  # ended: the live URL is now the VOD
            return f"https://www.youtube.com/watch?v={meta.get('stream_id') or meta['video_id']}"
        return None
    if platform in ("twitch", "kick") and login:
        listing = (f"https://www.twitch.tv/{login}/videos?filter=archives&sort=time" if platform == "twitch"
                   else f"https://kick.com/{login}/videos")
        p = subprocess.run([ytdlp, "-J", "--flat-playlist", "--playlist-items", "1:3", listing],
                           capture_output=True, text=True, encoding="utf-8", errors="replace")
        if p.returncode != 0:
            return None
        try:
            entries = json.loads(p.stdout).get("entries") or []
        except ValueError:
            return None
        started = meta.get("stream_started_at")
        for e in entries:
            url = e.get("url") or e.get("webpage_url")
            if not url:
                continue
            ts = e.get("timestamp")
            if ts is None:
                q = subprocess.run([ytdlp, "-J", "--skip-download", url], capture_output=True, text=True,
                                   encoding="utf-8", errors="replace")
                ts = json.loads(q.stdout).get("timestamp") if q.returncode == 0 else None
            if not started or (ts and abs(float(ts) - float(started)) < 900):
                return url
    return None


def _vod_audio(jd: Path, vod_url: str, start: float, dur: float) -> np.ndarray:
    """`dur` seconds of the VOD's audio from VOD time `start`, as float32 16 kHz mono."""
    from . import media
    from .download import resolve

    st, _ = resolve(vod_url)
    fmt = st.get("audio") or st["video"]
    inputs = media._remote_input(jd, fmt, max(0.0, start), start + dur)
    p = subprocess.run(["ffmpeg", "-v", "error", *inputs, "-t", f"{dur:.3f}", "-vn", "-ac", "1", "-ar", str(SR),
                        "-f", "s16le", "-"], capture_output=True, check=True)
    media._clean_pieces(jd)
    return np.frombuffer(p.stdout, dtype=np.int16).astype(np.float32)


def _ref_audio(jd: Path, start: float, dur: float) -> np.ndarray:
    with wave.open(str(jd / "audio.wav"), "rb") as w:
        w.setpos(max(0, int(start * SR)))
        return np.frombuffer(w.readframes(int(dur * SR)), dtype=np.int16).astype(np.float32)


def _xcorr_lag(ref: np.ndarray, hay: np.ndarray) -> tuple[int, float]:
    """Position of `ref` inside `hay` (samples) and how clearly it stands out (peak / typical)."""
    ref = (ref - ref.mean()) / (ref.std() + 1e-6)
    hay = (hay - hay.mean()) / (hay.std() + 1e-6)
    n = 1 << int(np.ceil(np.log2(len(hay) + len(ref))))
    corr = np.fft.irfft(np.fft.rfft(hay, n) * np.conj(np.fft.rfft(ref, n)), n)[: len(hay) - len(ref) + 1]
    i = int(np.argmax(corr))
    return i, float(corr[i] / (np.median(np.abs(corr)) + 1e-6))


def vod_offset_at(jd: Path, t: float) -> float:
    """VOD time = recording time + offset, measured around recording time `t` by matching our audio against the
    VOD's (ad breaks and reconnects make the offset drift, so it is measured near each clip and cached)."""
    meta = read_json(jd / "meta.json")
    if not meta.get("vod_url"):
        url = find_vod(meta)
        if not url:
            raise RuntimeError("This part was recorded as audio only and the VOD isn't available yet "
                               "(Twitch: VODs must be enabled; YouTube / Kick: after the stream ends).")
        meta["vod_url"] = url
        write_json(jd / "meta.json", meta)
    cache_path = jd / "rec" / "vod_align.json"
    cache = read_json(cache_path) if cache_path.exists() else {}
    near = [(abs(float(k) - t), v) for k, v in cache.items()]
    close = [v for d, v in near if d < 120]
    if close:
        return close[0]
    # first guess: the nearest measured point, else wall clock (part start vs stream start)
    if near:
        guess = min(near)[1]
    else:
        rec = read_json(jd / "rec" / "segments.json")
        part = next((p for p in reversed(rec["parts"]) if p["start"] <= t), rec["parts"][0])
        started = float(meta.get("stream_started_at") or part["wall"])
        guess = (part["wall"] - started) - part["start"]
    ref_start = max(0.0, t - 10.0)
    ref = _ref_audio(jd, ref_start, 20.0)
    for margin in (45.0, 240.0):  # small search first; ads / reconnects can need a wide one
        hay_start = max(0.0, ref_start + guess - margin)
        hay = _vod_audio(jd, meta["vod_url"], hay_start, 2 * margin + 20.0)
        if len(hay) <= len(ref):
            continue
        i, clarity = _xcorr_lag(ref, hay)
        if clarity > 8.0:
            off = round(hay_start + i / SR - ref_start, 3)
            cache[str(round(t, 1))] = off
            write_json(cache_path, cache)
            log(f"VOD aligned at {fmt_ts(t)}: offset {off:+.2f}s (clarity {clarity:.0f})")
            return off
    log(f"Couldn't align the VOD near {fmt_ts(t)} by audio; using the wall-clock estimate {guess:+.1f}s")
    return guess


# ------------------------------------------------------------------ main loop

def _settings(jd: Path, defaults: dict) -> dict:
    p = jd / "rec" / "control.json"
    if p.exists():
        try:
            return {**defaults, **read_json(p)}
        except (ValueError, OSError):
            pass
    return dict(defaults)


def run_live(source: str, audio: bool | None = None, quality: str | None = None, game_mode: bool = True) -> None:
    cfg = lcfg()
    existing = jobs_dir() / source
    if re.fullmatch(r"[\w.-]+", source) and (existing / "meta.json").exists():
        jd, job_id = existing, source
        meta = read_json(jd / "meta.json")
        url = meta["source"]
        log(f"Resuming live job {job_id}")
    else:
        url = source
        info = {"id": "fake", "title": Path(source).stem, "uploader": "fake", "extractor_key": "fake"} \
            if os.environ.get("CLIP_FAKE_LIVE") else probe(url)
        if not info:
            sys.exit("This link isn't live right now. For a finished stream, use `clip.py prepare <vod link>`.")
        job_id, jd = create_job(url, info)
        meta = read_json(jd / "meta.json")
    say("job", job_id=job_id, platform=meta["platform"], video_id=meta["video_id"], title=meta.get("title"),
        channel=meta.get("channel"), duration=meta.get("duration"), live=True)
    print(f"LIVE job={job_id}", flush=True)
    (jd / "rec").mkdir(exist_ok=True)
    (jd / "rec" / "stop").unlink(missing_ok=True)
    (jd / "rec" / "ended").unlink(missing_ok=True)

    defaults = {"audio": cfg["video"] == "audio" if audio is None else audio,
                "quality": quality or cfg["quality"], "game_mode": game_mode}
    ctl = jd / "rec" / "control.json"
    current = _settings(jd, defaults) if ctl.exists() else dict(defaults)
    if audio is not None:
        current["audio"] = audio
    if quality:
        current["quality"] = quality
    if not game_mode:
        current["game_mode"] = False
    write_json(ctl, current)
    settings = dict(current)
    lower_own_priority(settings["game_mode"])

    stop = threading.Event()
    rec = Recording(jd)
    proc = Processor(jd, rec, settings)
    chat = Chat(jd, meta["platform"], url, meta.get("login") or "", meta["video_id"], stop)
    chat.start()

    def on_sigint(*_):
        stop.set()
    signal.signal(signal.SIGINT, on_sigint)
    if not IS_WINDOWS:
        signal.signal(signal.SIGTERM, on_sigint)

    recorder: Recorder | None = None
    ctl_mtime = ctl.stat().st_mtime if ctl.exists() else 0.0
    last_report, last_bytes, last_t = 0.0, rec.disk_bytes(), time.time()
    offline_since: float | None = None
    stats = {"kbps": 0.0}

    def watcher() -> None:
        nonlocal last_report, last_bytes, last_t
        while not stop.is_set():
            if recorder is not None and recorder.part >= 0:
                rec.scan(recorder.part)
            now = time.time()
            if now - last_report >= 5:
                b = rec.disk_bytes()
                if now > last_t:
                    stats["kbps"] = max(0.0, (b - last_bytes) * 8 / 1000 / (now - last_t))
                last_bytes, last_t, last_report = b, now, now
                say("live", step="tick", recording=bool(recorder and recorder.alive()), recorded=round(rec.edge(), 1),
                    processed=round(proc.processed, 1), disk=b + int((jd / "audio.wav").stat().st_size
                                                                     if (jd / "audio.wav").exists() else 0),
                    kbps=round(stats["kbps"]), chat=chat.count, chat_per_min=len(chat.recent),
                    audio=settings["audio"], quality=settings["quality"], game_mode=settings["game_mode"],
                    part=recorder.part if recorder else None)
            stop.wait(2)

    threading.Thread(target=watcher, daemon=True, name="watcher").start()
    worker: threading.Thread | None = None
    failures = {"n": 0}

    def process() -> None:
        try:
            proc.update()
            failures["n"] = 0
        except Exception as e:  # keep recording whatever happens; try again on the next chunk
            failures["n"] += 1
            log(f"Processing failed ({e.__class__.__name__}: {e}); retrying later")
            say("live", step="error", msg=f"Processing failed: {e}")
            stop.wait(min(600, 30 * failures["n"]))

    try:
        while not stop.is_set():
            if (jd / "rec" / "stop").exists():
                log("Stop requested")
                break
            # settings switched from the web app: start a new part with them
            if ctl.exists() and ctl.stat().st_mtime != ctl_mtime:
                ctl_mtime = ctl.stat().st_mtime
                new = _settings(jd, defaults)
                restart = (new["audio"], new["quality"]) != (settings["audio"], settings["quality"])
                if new["game_mode"] != settings["game_mode"]:
                    lower_own_priority(new["game_mode"])
                settings.update(new)
                proc.settings = settings
                if restart and recorder and recorder.alive():
                    log("Switching to " + ("audio only" if settings["audio"] else settings["quality"]))
                    recorder.stop()
                    rec.scan(recorder.part)
                    recorder = None
            if recorder is None or not recorder.alive():
                if recorder is not None:
                    rec.scan(recorder.part)
                    recorder = None
                    if os.environ.get("CLIP_FAKE_LIVE"):
                        log("Fake stream ended")
                        break
                    if offline_since is None:
                        offline_since = time.time()
                        log("Stream connection ended; checking whether it's still live")
                if offline_since is not None and not os.environ.get("CLIP_FAKE_LIVE"):
                    if probe(url) is None:
                        if time.time() - offline_since > 60 * float(cfg["reconnect_min"]):
                            log("The stream has ended")
                            break
                        say("live", step="offline", msg="Stream offline, waiting for it to come back")
                        stop.wait(30)
                        continue
                    offline_since = None
                recorder = Recorder(jd, rec, url, meta["platform"], settings["audio"], settings["quality"])
                recorder.start()
            if proc.due() and (worker is None or not worker.is_alive()):
                worker = threading.Thread(target=process, daemon=True, name="processor")
                worker.start()
            stop.wait(2)
    finally:
        stop.set()
        if worker is not None:
            worker.join()
        if recorder:
            recorder.stop()
            rec.scan(recorder.part)
        log("Processing the rest of the recording")
        say("live", step="finishing", msg="Processing the last minutes")
        try:
            proc.update(final=True)
        finally:
            (jd / "rec" / "ended").write_text(str(time.time()), encoding="utf-8")
            (jd / "rec" / "stop").unlink(missing_ok=True)
        say("live", step="ended", recorded=round(rec.end, 1), processed=round(proc.processed, 1))
        print(f"LIVE_ENDED job={job_id} duration={fmt_ts(rec.end)}", flush=True)
