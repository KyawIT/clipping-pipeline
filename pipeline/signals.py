"""Step 4: local scoring (free, no Claude usage).

Builds per-second signals (chat rate, chat hype, voice energy, speech rate), combines them,
picks the top-N candidate windows and writes a compact candidates.md for Claude to read.
"""
from __future__ import annotations

import re
import wave
from collections import Counter
from pathlib import Path

import numpy as np

from .common import CFG, fmt_ts, log, progress, read_json, write_json


# ---------------------------------------------------------------- raw signals

def audio_energy(wav_path: Path, n_sec: int) -> np.ndarray:
    """RMS loudness in dB per second, streamed so 3h files don't need 350 MB of RAM."""
    out = np.full(n_sec, -90.0, dtype=np.float32)
    with wave.open(str(wav_path), "rb") as wf:
        sr, width, ch = wf.getframerate(), wf.getsampwidth(), wf.getnchannels()
        if width != 2:
            raise ValueError("expected 16-bit PCM wav")
        for i in range(n_sec):
            buf = wf.readframes(sr)
            if not buf:
                break
            x = np.frombuffer(buf, dtype=np.int16).astype(np.float32)
            if ch > 1:
                x = x.reshape(-1, ch).mean(axis=1)
            rms = np.sqrt(np.mean(x * x) + 1e-9) / 32768.0
            out[i] = 20 * np.log10(rms + 1e-9)
    return out


def load_chat(chat_path: Path) -> list[tuple[float, str]]:
    if not chat_path.exists():
        return []
    data = read_json(chat_path)
    comments = data.get("comments", data if isinstance(data, list) else [])
    msgs = []
    for c in comments:
        t = c.get("content_offset_seconds")
        if t is None:
            continue
        m = c.get("message") or {}
        body = m.get("body") if isinstance(m, dict) else str(m)
        if body is None and isinstance(m, dict):
            body = "".join(f.get("text", "") for f in m.get("fragments", []) or [])
        msgs.append((float(t), (body or "").strip()))
    return msgs


def _hype_score(body: str, single: set[str], multi: list[str]) -> float:
    up = body.upper()
    tokens = re.findall(r"[A-Z0-9+]+", up)
    score = 0.0
    if any(t in single for t in tokens):
        score += 1.0
    if any(k in up for k in multi):
        score += 1.0
    letters = [ch for ch in body if ch.isalpha()]
    if len(letters) >= 4 and sum(ch.isupper() for ch in letters) / len(letters) > 0.8:
        score += 0.5  # shouting in caps
    if re.search(r"(.)\1{3,}", body) or "!!!" in body or "???" in body:
        score += 0.5  # "NOOOOO", "?????"
    return min(score, 2.0)


def chat_signals(msgs, n_sec: int):
    rate = np.zeros(n_sec, dtype=np.float32)
    hype = np.zeros(n_sec, dtype=np.float32)
    kws = [k.upper() for k in CFG["signals"]["hype_keywords"]]
    single = {k for k in kws if " " not in k}
    multi = [k for k in kws if " " in k]
    for t, body in msgs:
        i = int(t)
        if 0 <= i < n_sec:
            rate[i] += 1
            hype[i] += _hype_score(body, single, multi)
    return rate, hype


def speech_rate(words, n_sec: int) -> np.ndarray:
    r = np.zeros(n_sec, dtype=np.float32)
    for w in words:
        i = int(w["s"])
        if 0 <= i < n_sec:
            r[i] += 1
    return r


# ---------------------------------------------------------------- helpers

def smooth(x: np.ndarray, win: int) -> np.ndarray:
    if win <= 1 or len(x) == 0:
        return x.astype(np.float32)
    k = np.ones(win, dtype=np.float32) / win
    return np.convolve(x, k, mode="same").astype(np.float32)


def robust_z(x: np.ndarray) -> np.ndarray:
    if len(x) == 0 or np.allclose(x, x[0]):
        return np.zeros_like(x, dtype=np.float32)
    med = np.median(x)
    mad = np.median(np.abs(x - med)) * 1.4826
    if mad < 1e-6:
        mad = np.std(x) + 1e-6
    return np.clip((x - med) / mad, -3, 6).astype(np.float32)


def shift_earlier(x: np.ndarray, sec: int) -> np.ndarray:
    if sec <= 0:
        return x
    out = np.zeros_like(x)
    out[:-sec] = x[sec:]
    return out


def pick_peaks(score: np.ndarray, count: int, min_gap: int) -> list[int]:
    order = np.argsort(-score)
    chosen: list[int] = []
    for idx in order:
        if all(abs(int(idx) - c) >= min_gap for c in chosen):
            chosen.append(int(idx))
            if len(chosen) >= count:
                break
    return chosen  # strongest first


# ---------------------------------------------------------------- main step

def _existing_clips(jd: Path) -> list[tuple[str, float, float]]:
    from .common import parse_ts
    f = jd / "clips.json"
    if not f.exists():
        return []
    data = read_json(f)
    clips = data["clips"] if isinstance(data, dict) else data
    return [(c.get("id") or f"clip_{i:02d}", parse_ts(c["start"]), parse_ts(c["end"])) for i, c in enumerate(clips, 1)]


def build_candidates(jd: Path, window: tuple[float, float | None] | None = None, write_md: bool = True) -> Path:
    """Score the whole video and write candidates.md / candidates.json.

    With `window=(from, to)` (live pick rounds) the scores are still normalized over the whole stream, but only
    peaks inside the window that don't overlap an existing clip are listed, and candidates.json (the full list the
    web app draws) is left alone."""
    meta = read_json(jd / "meta.json")
    tr = read_json(jd / "transcript.json")
    n_sec = int(np.ceil(meta["duration"])) + 1
    sc, cc = CFG["signals"], CFG["candidates"]
    w = sc["weights"]

    wav = jd / "vocals.wav" if (jd / "vocals.wav").exists() else jd / "audio.wav"
    log("Computing audio energy")
    progress("score", 10, "Measuring loudness, chat spikes and speech")
    energy = audio_energy(wav, n_sec)
    msgs = load_chat(jd / "chat.json")
    rate, hype = chat_signals(msgs, n_sec)
    words_ps = speech_rate(tr["words"], n_sec)

    lag = int(sc.get("chat_lag_sec", 6))
    z_rate = robust_z(shift_earlier(smooth(rate, 5), lag))
    z_hype = robust_z(shift_earlier(smooth(hype, 5), lag))
    z_energy = robust_z(smooth(energy, 3))
    z_speech = robust_z(smooth(words_ps, 5))

    has_chat = len(msgs) > 50
    combined = w["audio_energy"] * z_energy + w["speech_rate"] * z_speech
    if has_chat:
        combined += w["chat_rate"] * z_rate + w["chat_hype"] * z_hype
    combined = smooth(combined, 9)
    log(f"Signals ready (chat messages: {len(msgs)}, words: {len(tr['words'])})")

    existing = _existing_clips(jd) if window else []
    if window:
        lo, hi = int(max(0, window[0])), int(min(n_sec, window[1] if window[1] is not None else n_sec))
        masked = np.full_like(combined, -1e9)
        masked[lo:hi] = combined[lo:hi]
        for _, s, e in existing:  # never offer a moment that is already a clip
            masked[max(0, int(s) - 20): int(e) + 20] = -1e9
        count = int(CFG.get("live", {}).get("round_candidates", 10))
        peaks = [p for p in pick_peaks(masked, count, int(cc["min_gap_sec"])) if masked[p] > -1e8]
    else:
        peaks = pick_peaks(combined, int(cc["count"]), int(cc["min_gap_sec"]))
    baseline_rate = float(np.mean(rate[rate > 0])) if has_chat and np.any(rate > 0) else 0.0

    cands = []
    for k, p in enumerate(peaks, 1):
        start = max(0.0, p - float(cc["before_sec"]))
        end = min(float(meta["duration"]), p + float(cc["after_sec"]))
        chat_window = [b for t, b in msgs if p - 5 <= t - lag <= p + 15] if has_chat else []
        top = Counter(_norm_msg(b) for b in chat_window if b).most_common(6)
        local_rate = float(np.mean(rate[max(0, p - 5 + lag): p + 15 + lag])) if has_chat else 0.0
        cands.append({
            "id": f"C{k:02d}", "start": round(start, 2), "end": round(end, 2), "peak": p,
            "score": round(float(combined[p]), 2),
            "chat_x_baseline": round(local_rate / baseline_rate, 1) if baseline_rate else None,
            "chat_top": top,
        })

    write_json(jd / "signals.json", {
        "has_chat": has_chat, "chat_lag_sec": lag,
        "combined": [round(float(v), 2) for v in combined],
    })
    md = jd / "candidates.md"
    text = _render_md(meta, tr, cands, has_chat)
    if window:
        head = [f"**Live pick round: window {fmt_ts(window[0])} - {fmt_ts(window[1] if window[1] is not None else meta['duration'])}.** "
                "Only these candidates are new. Keep every clip already in clips.json exactly as it is and only "
                "append new ones."]
        if existing:
            head.append("Existing clips (don't overlap): " + ", ".join(f"{cid} {fmt_ts(s)}-{fmt_ts(e)}"
                                                                        for cid, s, e in existing))
        if not cands:
            head.append("No new candidates in this window: add no clips.")
        title, rest = text.split("\n", 1)
        text = title + "\n" + "\n".join(head) + "\n" + rest
        write_json(jd / "candidates_window.json", cands)
    else:
        write_json(jd / "candidates.json", cands)
    if write_md:  # live jobs rescore every chunk without touching the candidates.md a pick round may be reading
        md.write_text(text, encoding="utf-8")
    log(f"Wrote {len(cands)} candidates" + (f" -> {md}" if write_md else ""))
    progress("score", 100, f"{len(cands)} hot moments found", candidates=len(cands), has_chat=has_chat)
    return md


def _norm_msg(body: str) -> str:
    b = re.sub(r"\s+", " ", body.strip())
    b = re.sub(r"(.)\1{3,}", r"\1\1\1", b)  # NOOOOOO -> NOOO
    return b[:60]


def _render_md(meta, tr, cands, has_chat: bool) -> str:
    segs = tr["segments"]
    lines = [
        f"# Candidates: {meta.get('title')}",
        f"channel: {meta.get('channel')} | mode: {meta.get('mode')} | duration: {fmt_ts(meta['duration'])} "
        f"| source: {meta.get('width')}x{meta.get('height')} | chat signal: {'yes' if has_chat else 'no'}",
        "",
        "Each candidate is a window around a local peak in chat/voice activity, strongest first (C01 = highest "
        "score). Timestamps are absolute (h:mm:ss in the source). Scores are relative within this video only. "
        "Pick exact clip boundaries inside or across windows.",
        "",
    ]
    for c in cands:
        head = f"## {c['id']}  {fmt_ts(c['start'])} - {fmt_ts(c['end'])}  (score {c['score']}, peak {fmt_ts(c['peak'])}"
        if c["chat_x_baseline"]:
            head += f", chat {c['chat_x_baseline']}x normal"
        lines.append(head + ")")
        if c["chat_top"]:
            lines.append("chat: " + ", ".join(f'"{m}" x{n}' for m, n in c["chat_top"]))
        for s in segs:
            if s["e"] < c["start"] or s["s"] > c["end"] or not s["text"]:
                continue
            spk = f"{s['spk']}: " if s.get("spk") else ""
            lines.append(f"[{fmt_ts(s['s'])}] {spk}{s['text']}")
        lines.append("")
    return "\n".join(lines)
