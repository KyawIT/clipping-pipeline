"""Step 2+3: optional vocal separation (Demucs) and WhisperX transcription with word timestamps."""
from __future__ import annotations

import gc
import inspect
import os
import sys
from pathlib import Path

from .common import CFG, log, progress, read_json, run, write_json


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


def _device() -> tuple[str, str]:
    c = CFG["transcribe"]
    device = c.get("device", "auto")
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

    import whisperx  # heavy import, keep lazy

    device, compute = _device()
    model_name = c.get("model", "large-v3")
    if device == "cpu" and model_name.startswith("large"):
        log("No CUDA GPU found: large models on CPU are very slow. Consider model: small/medium in config.yaml")
    log(f"WhisperX {model_name} on {device} ({compute})")

    progress("transcribe", 0, f"Loading WhisperX {model_name} on {device.upper()}", sub="load")
    audio = whisperx.load_audio(str(audio_path))
    model = whisperx.load_model(model_name, device, compute_type=compute, language=c.get("language"))
    progress("transcribe", 1, "Turning speech into text", sub="transcribe")
    kw = {}
    if "print_progress" in inspect.signature(model.transcribe).parameters:
        kw["print_progress"] = True  # prints "Progress: x%..." lines, which the web app reads
    result = model.transcribe(audio, batch_size=int(c.get("batch_size", 16)), **kw)
    language = result.get("language") or c.get("language") or "en"
    del model
    gc.collect()
    _free_gpu()

    log(f"Aligning words (language={language})")
    progress("transcribe", 85, f"Timing every word ({language})", sub="align")
    align_model, align_meta = whisperx.load_align_model(language_code=language, device=device)
    result = whisperx.align(result["segments"], align_model, align_meta, audio, device,
                            return_char_alignments=False)
    del align_model
    gc.collect()
    _free_gpu()

    if meta.get("mode") == "podcast" and c.get("diarize_podcasts", True):
        token = os.environ.get("HF_TOKEN")
        if not token:
            log("HF_TOKEN not set -> skipping speaker diarization (see README)")
        else:
            progress("transcribe", 95, "Telling the speakers apart", sub="diarize")
            result = _diarize(whisperx, audio, result, token, device)

    write_json(out, _normalize(result, language))
    progress("transcribe", 100, f"{len(result.get('segments', []))} segments transcribed")
    log(f"Wrote {out}")
    return out


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
