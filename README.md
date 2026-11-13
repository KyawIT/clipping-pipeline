# Clipping Pipeline

**Turn a long video into short, viral-style clips for TikTok, Instagram Reels and YouTube Shorts.**

Paste a link to a Twitch stream, a YouTube video or a podcast. You get back about 8 ready-to-post vertical clips, each with:

- 🎬 **A 9:16 video** with animated captions that pop word by word as they are spoken
- ✨ **A hook text** at the top for the first few seconds, so people stop scrolling
- 🖼️ **A thumbnail** with a big bold title, ready to use as the cover
- 📝 **A text file** with a title, a description and hashtags to copy and paste

An AI (Claude) watches for the best moments, decides where each clip starts and ends, and writes the hooks and titles. Everything else runs on your own computer.

---

## What you need

| | |
|---|---|
| 💻 **A Linux computer** | Ubuntu or similar (64-bit) |
| 🎮 **An NVIDIA graphics card** | Recommended, makes it much faster. It also works without one, just slower. |
| 🤖 **A Claude Pro account** | [claude.ai](https://claude.ai), the normal subscription. No API key needed. |
| 💾 **Free disk space** | About 10 GB per long stream |

---

## Install (one time)

Open a terminal and copy-paste these lines one at a time:

```bash
# 1. Basic tools
sudo apt install ffmpeg unzip curl git nodejs npm python3 python3-venv

# 2. Download this tool
git clone https://github.com/KyawIT/clipping-pipeline.git
cd clipping-pipeline

# 3. Set everything up (takes 5-15 minutes)
./setup.sh

# 4. Install Claude Code and log in with your Claude account
npm install -g @anthropic-ai/claude-code
claude
```

When Claude opens, type `/login`, log in in your browser, then type `/exit`.

> ⚠️ Node.js 18 or newer is needed. If `node -v` shows something older, install a newer one from [nodejs.org](https://nodejs.org).

---

## Make clips

From the `clipping-pipeline` folder, run:

```bash
./clip.sh https://www.youtube.com/watch?v=YOUR_VIDEO
```

Twitch works the same way: `./clip.sh https://www.twitch.tv/videos/1234567890`

What happens next:

1. **Your computer prepares the video** (download, speech-to-text, finding exciting moments). This can take a while for long videos, and it doesn't use any of your Claude usage.
2. **Claude opens** and picks the best moments. It shows you a list of clips.
3. **Say "go"** (or ask for changes, like "make clip 3 shorter"). It then makes the videos.

### Where are my clips?

In the folder `jobs/<video name>/out/`:

```
clip_01.mp4          ← the video, ready to upload
clip_01_thumb.jpg    ← the thumbnail / cover image
clip_01.txt          ← title, description and hashtags
clip_02.mp4
...
```

---

## Change a clip

Every clip's settings are in `jobs/<video name>/clips.json`. Open it in any text editor.

| Setting | What it does | Example |
|---|---|---|
| `start` / `end` | Where the clip starts and ends (in seconds, or `1:02:30`) | `"start": 125.5` |
| `hook` | The text at the top in the first seconds. Put `*stars*` around a word to highlight it in yellow. | `"He did *NOT* expect this"` |
| `emphasis` | Words that pop extra big in the captions | `["insane", "never"]` |
| `thumb_text` | The big text on the thumbnail. `*Stars*` make a word yellow. | `"I *KILLED* THE SERVER"` |
| `thumb_at` | Which moment of the video to use for the thumbnail | `"thumb_at": 140.2` |
| `layout` | How the video is framed (see below) | `"split"` |

Then make the clip again:

```bash
.venv/bin/python clip.py render <video name> --only clip_03     # redo one clip
.venv/bin/python clip.py render <video name> --thumbs           # redo only the thumbnails (fast)
```

Not sure what `<video name>` is? Run `.venv/bin/python clip.py list`.

Or just ask Claude: open `claude` in this folder and say *"change the hook of clip 2 to ... and render it again"*.

### Layouts

| Layout | Looks like | Good for |
|---|---|---|
| `split` | Webcam on top, game below | Streams (the default) |
| `fullcam` | Only the face, full screen | Big reactions, just chatting |
| `fullgame` | The whole game or screen in the middle | Plays where you need to see everything |
| `podcast_follow` | Follows the person who is talking | Podcasts and talking-head videos |
| `podcast_stack` | Two people, one above the other | Two people sitting side by side |
| `center` | Simple middle crop | Anything else |

---

## Problems?

| Problem | Fix |
|---|---|
| **"command not found"** | Make sure you are inside the `clipping-pipeline` folder (`cd clipping-pipeline`). |
| **Claude asks to use an API key / bills you** | Run `unset ANTHROPIC_API_KEY` and try again. This tool uses your normal subscription. |
| **Preparing is very slow** | Normal without an NVIDIA card. Open `config.yaml` and make sure `model: small`. Leave it running overnight. |
| **Twitch video not found** | Twitch deletes old videos after 7-60 days. Clip soon after the stream. |
| **The face is cut off in `split`** | The webcam box for that channel is wrong. Ask Claude: *"redo the webcam layout for this channel"*. |
| **"Out of memory" while preparing** | In `config.yaml`, set `batch_size: 4`. |
| **Rendering fails to start** | Run `cd remotion && npx remotion browser ensure`, then try again. |

---

<details>
<summary><b>🔧 Advanced: all commands, settings and how it works</b></summary>

### How it works

```
Link ─► download video (+ Twitch chat)                          ┐
     ─► speech to text with word timings (WhisperX)             │ on your computer, free
     ─► score moments: chat spikes, emotes, loud voice          ┘
     ─► Claude picks ~8 clips, exact cuts, hooks, thumbnail text   ← your Claude plan
     ─► cut + render vertical video and thumbnail (Remotion)    ← on your computer
```

Claude never reads the full transcript, only about 25 short candidate moments, so one video uses only a handful of prompts' worth of your plan.

### All commands

```
clip.py prepare <url|file> [--mode stream|podcast]  download + transcribe + score (no Claude usage)
clip.py list | status <job>                         overview
clip.py words  <job> --from 1:02:10 --to 1:02:40    transcript with word timings
clip.py frames <job> --at 1:02:15,1:02:30 [--grid]  snapshot images
clip.py layout-detect <job>                         guess the webcam box
clip.py layout-save   <job> --cam x,y,w,h           save the webcam box for that channel (fractions 0-1)
clip.py validate <job>                              check clips.json
clip.py render   <job> [--only clip_01] [--thumbs]  make the videos and thumbnails
```

Run each with `.venv/bin/python clip.py ...`. Preparing several videos overnight and clipping them later saves Claude usage:

```bash
.venv/bin/python clip.py prepare <link 1>
.venv/bin/python clip.py prepare <link 2>
claude "/clip <job name>"
```

### Settings (`config.yaml`)

- `transcribe.model`: `large-v3` (best, needs a GPU), `medium`, or `small` (fast, for CPU)
- `transcribe.separate_vocals: true`: cleaner speech-to-text on loud games/music (install with `./setup.sh --demucs`, slow without a GPU)
- `signals.hype_keywords`: add your streamer's own emotes
- `clips.target`: how many clips per video (default 8)
- `render.hook_seconds`, `caption_words_per_page`, `split_cam_ratio`: look and timing

### Speaker labels for podcasts (optional)

Get a free token at [huggingface.co/settings/tokens](https://huggingface.co/settings/tokens), accept the terms of `pyannote/speaker-diarization-3.1` and `pyannote/segmentation-3.0`, then `export HF_TOKEN=hf_...` before preparing.

### Changing the look

Captions, hook and thumbnail styles live in `remotion/src/` (`Captions.tsx`, `Hook.tsx`, `Thumb.tsx`, `Clip.tsx`). Preview with `cd remotion && npm run studio`.

### Fewer permission prompts

Claude Code asks before running commands. `.claude/settings.json` already allows the tool's own commands; choose "don't ask again" for anything else you trust.

</details>

---

## Credits and license

This project is released under the [MIT License](LICENSE).

It builds on: yt-dlp (Unlicense), TwitchDownloader (MIT), WhisperX (BSD-2), OpenCV (Apache-2), Montserrat font (SIL OFL, bundled). **Remotion** has its own license: free for individuals and companies of up to 3 people, larger companies need a license from [remotion.dev/license](https://remotion.dev/license).

**Only clip videos you own or have permission to use.**
