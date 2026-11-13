# Clipping pipeline: Twitch streams and podcasts to TikTok / Reels

Paste a Twitch VOD (or YouTube / local file) and get ranked 9:16 clips with animated word-by-word captions
and a hook text overlay. Everything runs locally. The only cloud part is **Claude Code on your Pro plan**,
which picks the moments. No API keys needed.

```
URL ─► yt-dlp (video) + TwitchDownloaderCLI (chat replay)
    ─► WhisperX (word-level transcript)                       ┐ local + free
    ─► local scorer: chat spikes + emotes + voice energy      ┘ "clip.py prepare"
    ─► top-25 windows (candidates.md)
    ─► Claude Code /clip: webcam box, picks ~8 clips, exact cuts, hooks, titles   ← Pro plan
    ─► ffmpeg cut ─► Remotion render (split / fullcam / fullgame / podcast) ─► jobs/<job>/out/*.mp4
```

## Setup (once)

Requirements: Linux x64, `ffmpeg`, Node 18+, Python 3.10-3.12, ~10 GB free disk per long stream.
An NVIDIA GPU (8 GB+) is strongly recommended for WhisperX. Without one, see "CPU only" below.

```bash
sudo apt install ffmpeg unzip curl        # if missing
./setup.sh                                # venv, WhisperX, yt-dlp, TwitchDownloaderCLI, Remotion
npm install -g @anthropic-ai/claude-code  # if Claude Code isn't installed yet
claude                                    # then /login with your Claude Pro account
```

**Make sure `ANTHROPIC_API_KEY` is not set** (`unset ANTHROPIC_API_KEY`). Otherwise Claude Code bills the API
instead of using your subscription. `clip.sh` refuses to run if it's set.

## Usage

**One command:**
```bash
./clip.sh https://www.twitch.tv/videos/2345678901
```
This runs the free local prep (download, transcribe, score; a 3 h stream takes roughly 10-30 min on a GPU), then
opens Claude Code with `/clip <job>`. Claude:
1. On the first stream of a channel, looks at 3 frames and saves the **webcam box** to
   `layouts/twitch_<channel>.json`. Every later stream of that channel reuses it.
2. Reads the top candidate windows, refines cut points to word level, writes `jobs/<job>/clips.json`.
3. Shows you a table of clips, then renders them on your OK.

Finished clips: `jobs/<job>/out/clip_01.mp4` plus `clip_01.txt` (title, description, hashtags).

**Step by step** (handy for queuing several VODs overnight, since prep uses no Claude usage):
```bash
.venv/bin/python clip.py prepare https://www.twitch.tv/videos/111 
.venv/bin/python clip.py prepare https://www.twitch.tv/videos/222
.venv/bin/python clip.py list
claude "/clip twitch_v111"
```

**Podcasts** (YouTube auto-detects as podcast; or force it):
```bash
export HF_TOKEN=hf_...   # optional, for speaker labels: free token at huggingface.co/settings/tokens,
                         # accept the terms of pyannote/speaker-diarization-3.1 and pyannote/segmentation-3.0
./clip.sh "https://www.youtube.com/watch?v=..." --mode podcast
```

**Tweak and re-render** without Claude: edit `jobs/<job>/clips.json` (times, hook text, layout), then
```bash
.venv/bin/python clip.py render <job> --only clip_03
```

## Layouts

| layout | what it looks like | use for |
|---|---|---|
| `split` | webcam on top (38%), gameplay below, captions at the seam | default for streams |
| `fullcam` | webcam fills the whole 9:16 frame | big reactions, just chatting |
| `fullgame` | blurred background, full-width gameplay, rounded cam bubble above it | plays where you need to see the game |
| `podcast_follow` | 9:16 crop that smoothly follows the main face | single-camera podcasts |
| `podcast_stack` | left person on top, right person on the bottom | two people side by side in one shot |
| `center` | plain center crop | fallback |

Change look and feel in `remotion/src/` (`Captions.tsx` colors/size, `Hook.tsx`, `Clip.tsx` layouts). Preview
with `cd remotion && npm run studio`.

## Commands

```
clip.py prepare <url|file> [--mode stream|podcast]  download + transcribe + score
clip.py list | status <job>                         overview
clip.py words  <job> --from 1:02:10 --to 1:02:40    word timestamps (for exact cuts)
clip.py frames <job> --at 1:02:15,1:02:30 [--grid]  JPG snapshots
clip.py layout-detect <job>                         propose webcam box (draws it on frames)
clip.py layout-save   <job> --cam x,y,w,h           save the channel preset (fractions 0-1)
clip.py validate <job>                              check clips.json
clip.py render   <job> [--only clip_01,clip_02]     render
```

## Keeping it within Claude Pro limits

Pro has a 5-hour usage window plus a weekly cap, shared with claude.ai chat. This pipeline keeps Claude's part small:
- All heavy lifting (transcription, scoring, rendering) is local. Claude never reads the full transcript or chat,
  only ~25 short candidate windows (`candidates.count` in `config.yaml`).
- Frames are small 640 px JPGs, and only a few per video.
- It's an **interactive** session, not `claude -p`, so it counts against your normal plan limits rather than the
  separate headless credit pool.
- Run `/usage` in Claude Code to see where you stand. Expect a handful of prompts' worth per stream. If you run
  short, lower `candidates.count` to 15.

Claude Code asks before running each command. Choose "don't ask again" for `.venv/bin/python clip.py` commands
the first time, or add this to `.claude/settings.json` yourself:
```json
{ "permissions": { "allow": ["Bash(.venv/bin/python clip.py:*)", "Bash(sleep:*)", "Edit(jobs/**)"] } }
```

## Tuning (config.yaml)

- `transcribe.model`: `large-v3` (best), `medium` or `small` for CPU.
- `transcribe.separate_vocals: true`: runs Demucs first (install with `./setup.sh --demucs`). It helps with loud
  game audio or music, but it's slow without a GPU.
- `signals.weights` / `hype_keywords`: add your streamer's own emotes (e.g. channel emotes) to `hype_keywords`.
- `signals.chat_lag_sec`: how far chat lags behind the moment (default 6 s).
- `render.split_cam_ratio`, `caption_words_per_page`, `hook_seconds`.

## CPU only (no NVIDIA GPU)

Set `transcribe.model: small` (or `medium`) in `config.yaml`. A 3 h stream on CPU still takes a long time to
transcribe, so run `prepare` overnight.

## Troubleshooting

- **Twitch VOD gone:** VODs expire (7-60 days). Prepare soon after the stream.
- **No chat signal:** check `bin/TwitchDownloaderCLI` exists (rerun `./setup.sh`). The pipeline still works on
  audio and transcript.
- **CUDA out of memory:** lower `transcribe.batch_size` to 4-8.
- **Wrong webcam box / streamer changed overlay:** `clip.py layout-detect <job>` then `layout-save` again.
- **Remotion can't start Chrome:** `cd remotion && npx remotion browser ensure`. On minimal systems install the
  usual Chrome libs (`libnss3 libatk-bridge2.0-0 libgbm1 libxkbcommon0 libasound2`).

## Credits / licenses

yt-dlp (Unlicense), TwitchDownloader (MIT), WhisperX (BSD-2), OpenCV (Apache-2), Montserrat font (SIL OFL,
bundled in `remotion/public/fonts`). **Remotion** is source-available: free for individuals and companies of
up to 3 people; larger companies need a license (remotion.dev/license).

Only clip content you own or have permission to repurpose.
