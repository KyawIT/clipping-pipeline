"""Step 2+3: optional vocal separation (Demucs) and WhisperX transcription with word timestamps."""
from __future__ import annotations

import gc
import inspect
import os
import sys
from pathlib import Path

from .common import CFG, gpu_lock, log, progress, read_json, run, write_json


def separate_vocals(jd: Path) -> Path:
    out = jd / "vocals.wav"
    if out.exists():
        return out
    log("Separating vocals with Demucs (slow on CPU)...")
    run([sys.executable, "-m", "demucs", "--two-stems", "vocals", "-n", "htdemucs",
         "-o", jd / "demucs", jd / "audio.wav"])
    stem = jd / "demucs" / "htdemucs" / "audio" / "vocals.wav"
    run(["ffmpeg", "-y", "-loglevel", "error", "-i", stem, "-ac", "1", "-ar", "16000", out])
    return out


def _device(override: str | None = None) -> tuple[str, str]:
    c = CFG["transcribe"]
    device = override or c.get("device", "auto")
    if device == "auto":
        try:
            import torch
            device = "cuda" if torch.cuda.is_available() else "cpu"
        except Exception:
            device = "cpu"
    compute = c.get("compute_type", "auto")
    if compute == "auto":
        compute = "float16" if device == "cuda" else "int8"
    return device, compute


def transcribe(jd: Path) -> Path:
    out = jd / "transcript.json"
    if out.exists():
        log("transcript.json exists, skipping transcription")
        progress("transcribe", 100, "Already transcribed")
        return out

    c = CFG["transcribe"]
    meta = read_json(jd / "meta.json")
    audio_path = jd / "audio.wav"
    if c.get("separate_vocals"):
        audio_path = separate_vocals(jd)
    data = _whisperx(audio_path, meta.get("mode"))
    write_json(out, data)
    progress("transcribe", 100, f"{len(data['segments'])} segments transcribed")
    log(f"Wrote {out}")
    return out


def _whisperx(audio_path: Path, mode: str | None, device: str | None = None, threads: int | None = None,
              language: str | None = None, quiet: bool = False) -> dict:
    """WhisperX transcription + word alignment (+ diarization for podcasts) -> the compact transcript format."""
    c = CFG["transcribe"]
    import whisperx  # heavy import, keep lazy

    device, compute = _device(device)
    model_name = c.get("model", "large-v3")
    if device == "cpu" and model_name.startswith("large"):
        log("Running WhisperX on CPU: large models are very slow. Consider model: small/medium in config.yaml")
    log(f"WhisperX {model_name} on {device} ({compute})")
    say = (lambda *a, **k: None) if quiet else progress

    with gpu_lock(device == "cuda"):
        say("transcribe", 0, f"Loading WhisperX {model_name} on {device.upper()}", sub="load")
        audio = whisperx.load_audio(str(audio_path))
        kw_load = {"threads": int(threads)} if threads else {}
        model = whisperx.load_model(model_name, device, compute_type=compute,
                                    language=language or c.get("language"), **kw_load)
        say("transcribe", 1, "Turning speech into text", sub="transcribe")
        kw = {}
        if not quiet and "print_progress" in inspect.signature(model.transcribe).parameters:
            kw["print_progress"] = True  # prints "Progress: x%..." lines, which the web app reads
        result = model.transcribe(audio, batch_size=int(c.get("batch_size", 16)), **kw)
        language = result.get("language") or language or c.get("language") or "en"
        del model
        gc.collect()
        _free_gpu()

        log(f"Aligning words (language={language})")
        say("transcribe", 85, f"Timing every word ({language})", sub="align")
        align_model, align_meta = whisperx.load_align_model(language_code=language, device=device)
        result = whisperx.align(result["segments"], align_model, align_meta, audio, device,
                                return_char_alignments=False)
        del align_model
        gc.collect()
        _free_gpu()

        if mode == "podcast" and c.get("diarize_podcasts", True):
            token = os.environ.get("HF_TOKEN")
            if not token:
                log("HF_TOKEN not set -> skipping speaker diarization (see README)")
            else:
                say("transcribe", 95, "Telling the speakers apart", sub="diarize")
                result = _diarize(whisperx, audio, result, token, device)
    return _normalize(result, language)


def transcribe_chunk(jd: Path, wav: Path, offset: float, keep_from: float, device: str | None = None,
                     threads: int | None = None) -> int:
    """Live jobs: transcribe one chunk (`wav` starts at source time `offset`) and merge it into transcript.json.
    Words before `keep_from` are dropped from the chunk (they are the overlap the previous chunk already has);
    the old transcript keeps only words before `keep_from`. -> number of words added."""
    out = jd / "transcript.json"
    old = read_json(out) if out.exists() else {"language": None, "words": [], "segments": []}
    meta = read_json(jd / "meta.json")
    new = _whisperx(wav, meta.get("mode"), device=device, threads=threads, language=old.get("language"), quiet=True)
    words = [{**w, "s": round(w["s"] + offset, 3), "e": round(w["e"] + offset, 3)} for w in new["words"]]
    segs = [{**s, "s": round(s["s"] + offset, 3), "e": round(s["e"] + offset, 3)} for s in new["segments"]]
    words = [w for w in words if w["s"] >= keep_from]
    segs = [s for s in segs if s["e"] > keep_from]
    if segs and segs[0]["s"] < keep_from:  # a segment straddling the seam: keep only its new words' text
        first = segs[0]
        tail = [w["w"] for w in words if w["s"] < first["e"]]
        segs[0] = {**first, "s": keep_from, "text": " ".join(tail)}
    merged = {
        "language": old.get("language") or new.get("language"),
        "words": [w for w in old["words"] if w["s"] < keep_from] + words,
        "segments": [s for s in old["segments"] if s["s"] < keep_from] + segs,
    }
    write_json(out, merged)
    return len(words)


def _diarize(whisperx, audio, result, token: str, device: str):
    log("Speaker diarization (pyannote)")
    try:
        try:
            from whisperx.diarize import DiarizationPipeline
        except ImportError:
            DiarizationPipeline = whisperx.DiarizationPipeline
        try:
            pipe = DiarizationPipeline(use_auth_token=token, device=device)
        except TypeError:
            pipe = DiarizationPipeline(token=token, device=device)
        segs = pipe(audio)
        return whisperx.assign_word_speakers(segs, result)
    except Exception as e:  # diarization is a nice-to-have
        log(f"Diarization failed ({e}); continuing without speaker labels")
        return result


def _free_gpu() -> None:
    try:
        import torch
        if torch.cuda.is_available():
            torch.cuda.empty_cache()
    except Exception:
        pass


def _normalize(result: dict, language: str) -> dict:
    """Compact format: words [{w,s,e,spk}], segments [{s,e,text,spk}]. Fills missing word times."""
    words, segments = [], []
    for seg in result.get("segments", []):
        seg_words = seg.get("words") or []
        s0, e0 = float(seg.get("start", 0.0)), float(seg.get("end", 0.0))
        # interpolate words that WhisperX could not align (numbers, symbols)
        n = len(seg_words)
        for i, w in enumerate(seg_words):
            text = (w.get("word") or "").strip()
            if not text:
                continue
            ws, we = w.get("start"), w.get("end")
            if ws is None or we is None:
                ws = s0 + (e0 - s0) * i / max(n, 1)
                we = s0 + (e0 - s0) * (i + 1) / max(n, 1)
            words.append({"w": text, "s": round(float(ws), 3), "e": round(float(we), 3),
                          "spk": w.get("speaker") or seg.get("speaker")})
        segments.append({"s": round(s0, 3), "e": round(e0, 3), "text": (seg.get("text") or "").strip(),
                         "spk": seg.get("speaker")})
    return {"language": language, "words": words, "segments": segments}
