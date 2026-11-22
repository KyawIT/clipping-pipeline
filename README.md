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
| 💻 **A Linux or Windows computer** | Ubuntu or similar (64-bit), or Windows 10/11 (64-bit) |
| 🎮 **An NVIDIA graphics card** | Recommended, makes it much faster. It also works without one, just slower. |
| 🤖 **A Claude Pro account** | [claude.ai](https://claude.ai), the normal subscription. No API key needed. |
| 💾 **Free disk space** | About 1-2 GB per long stream (only the audio and the clips are downloaded) |

---

## Install (one time)

### Linux

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

### Windows

Open **PowerShell** or **Terminal** and copy-paste these lines one at a time:

```powershell
# 1. Basic tools (then close the window and open a new one, so they are found)
winget install Gyan.FFmpeg OpenJS.NodeJS.LTS Python.Python.3.12 Git.Git

# 2. Download this tool
git clone https://github.com/KyawIT/clipping-pipeline.git
cd clipping-pipeline

# 3. Set everything up (takes 5-15 minutes)
.\setup.cmd

# 4. Install Claude Code and log in with your Claude account
npm install -g @anthropic-ai/claude-code
claude
```

When Claude opens, type `/login`, log in in your browser, then type `/exit`.

> 💡 With an NVIDIA card, `setup.cmd` installs the GPU version of PyTorch for you (a ~2-3 GB download). Without one, set `model: small` in `config.yaml`.

---

## Make clips

### The easy way: the web app

From the `clipping-pipeline` folder, run `.\web.cmd` (Windows) or `./web.sh` (Linux). Your browser opens at `http://127.0.0.1:8765`.

- **New clips:** paste a Twitch, YouTube or Kick link and press *Clip it*. A live dashboard shows every step: the audio download, speech-to-text, a "hype over time" chart of the whole stream, and what Claude is doing right now ("Fine-tuning the cut 1:02:10–1:02:40"). When Claude's picks are ready, untick the clips you don't want and press *Render*. Each clip fills up as it renders and then shows its thumbnail. You can paste more links meanwhile: they queue up.
- **Library:** all rendered clips, grouped by creator and video. Each clip shows its virality score, a thumbnail (click it to show the file in Explorer, ▶ to play it), and title / description / hashtags with copy buttons. Tick **TikTok / Reels / Shorts** once you've uploaded a clip, then filter by "Not on TikTok yet".
- **Description link:** set a default link (e.g. your Linktree) under ⚙ Settings. It's added to every description between the text and the hashtags. You can change it per pasted link or per video, or leave it empty for none.
- **Deleting:** the 🗑 button on a clip deletes its video, thumbnail and text. Under each video in the Library, *Free Up Space* deletes the downloaded audio, cached pieces and recordings but keeps the finished clips, and *Delete Video* removes the whole folder. A run page has *Delete* too, which also cleans up runs that never got to the Library. Deleting is permanent: the dialog shows what goes and how much space that frees.

### Live streams

Paste the link of a stream that is **live right now** (Twitch, YouTube or Kick). Instead of an error you get live options:

- **Video source:** *Record Locally* (default) saves the stream in 5-minute files while it runs. This is the most reliable option and works even if the streamer has VODs turned off; 720p uses about 3 Mbps and 1.4 GB per hour. *Audio Only* records just the sound (about 0.16 Mbps, 70 MB per hour), and the picture for each clip is fetched from the VOD afterwards. Twitch VODs need to be enabled for that; YouTube and Kick VODs exist only after the stream ends. You can switch between the two, and change the quality (1080p / 720p / 480p), at any time while it runs.
- **Game mode** (default on) is for gaming on the same PC: everything runs at below-normal priority, speech-to-text runs on the CPU so the GPU stays free, and clips you approve wait to render until the stream ends. *Render Now* starts them anyway.

While the stream runs, the app transcribes the new audio every 10 minutes and keeps the hype chart up to date. About every 45 minutes, and a few minutes after a big chat or voice spike, Claude looks at **only the new stretch** and suggests up to 3 clips, which you keep or skip right there. *Pick Now* asks for a round straight away. When the stream ends (or you press *Stop Recording*), a final round covers the last stretch. When everything is rendered, *Delete Recording, Keep Clips* gets the disk space back. The timings are under `live:` in `config.yaml`.

From the terminal: `clip.py live <link>` records and transcribes until the stream ends (Ctrl+C stops it), and `/clip <job>` picks clips from it at any time.

The same video is never added twice: the library knows each video by its YouTube / Twitch / Kick id. The Claude step runs in the background with your normal Claude Code login (Pro plan); log in once with `claude` → `/login`. Everything stays on your computer: the clips in `jobs/`, the library in `jobs/library.db`.

### From the terminal

From the `clipping-pipeline` folder, run:

```bash
./clip.sh https://www.youtube.com/watch?v=YOUR_VIDEO
```

Twitch and Kick work the same way: `./clip.sh https://www.twitch.tv/videos/1234567890`

On **Windows**, run `.\clip.cmd` and paste the link when it asks. (You can also write `.\clip.cmd "<link>"`. If the link contains `&`, paste it instead: PowerShell breaks those links when it passes them on.)

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

On Windows, use `.venv\Scripts\python clip.py ...` instead of `.venv/bin/python clip.py ...` (here and below).

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
| **Windows: "ffmpeg / node / python missing" right after installing it** | Close the terminal and open a new one, so Windows picks up the new programs. |
| **Claude asks to use an API key / bills you** | Run `unset ANTHROPIC_API_KEY` (Windows PowerShell: `Remove-Item Env:ANTHROPIC_API_KEY`) and try again. This tool uses your normal subscription. |
| **Preparing is very slow** | Normal without an NVIDIA card. Open `config.yaml` and make sure `model: small`. Leave it running overnight. |
| **Twitch video not found** | Twitch deletes old videos after 7-60 days. Clip soon after the stream. The video stays online until you render, so render before it disappears. |
| **Rendering fails while "fetching" a clip** | The site blocks partial downloads. Set `download.video: full` in `config.yaml` and run `prepare` again (the transcript is kept). |
| **The face is cut off in `split`** | The webcam box for that channel is wrong. Ask Claude: *"redo the webcam layout for this channel"*. |
| **"Out of memory" while preparing** | In `config.yaml`, set `batch_size: 4`. |
| **Rendering fails to start** | Inside the `remotion` folder, run `npx remotion browser ensure`, then try again. |

---

<details>
<summary><b>🔧 Advanced: all commands, settings and how it works</b></summary>

### How it works

```
Link ─► download only the audio (+ Twitch chat)                 ┐
     ─► speech to text with word timings (WhisperX)             │ on your computer, free
     ─► score moments: chat spikes, emotes, loud voice          ┘
     ─► Claude picks ~8 clips, exact cuts, hooks, thumbnail text   ← your Claude plan
     ─► download just those clip intervals of the video         ┐ on your computer
     ─► render vertical video and thumbnail (Remotion)          ┘
```

The full video is never downloaded: preparing fetches the audio track (a few % of the size), and rendering fetches only the seconds each clip needs, straight from the online video. Downloaded clip pieces are kept in `jobs/<name>/segments/`, so re-rendering a clip with a new hook costs no download. If a site doesn't allow this, or you want the whole file anyway, set `download.video: full` in `config.yaml`. Local files are used as they are.

> Because the video stays online, render your clips while it is still up (Twitch deletes VODs after 7-60 days).

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
clip.py live <url|job> [--audio] [--quality 720p]   record + transcribe a live stream while it runs
clip.py candidates <job> --from 1:00:00 [--to ..]   candidate moments of one time window only
clip.py delete <job> [--clip clip_03] [--keep-clips] [--yes]   delete a clip, the working files, or the job
clip.py web [--port 8765]                           the web app
```

Run each with `.venv/bin/python clip.py ...`. Preparing several videos overnight and clipping them later saves Claude usage:

```bash
.venv/bin/python clip.py prepare <link 1>
.venv/bin/python clip.py prepare <link 2>
claude "/clip <job name>"
```

### Settings (`config.yaml`)

- `transcribe.model`: `large-v3` (best, needs a GPU), `medium`, or `small` (fast, for CPU)
- `transcribe.separate_vocals: true`: cleaner speech-to-text on loud games/music (install with `./setup.sh --demucs`, or `.\setup.cmd -Demucs` on Windows; slow without a GPU)
- `signals.hype_keywords`: add your streamer's own emotes
- `clips.target`: how many clips per video (default 8)
- `render.hook_seconds`, `caption_words_per_page`, `split_cam_ratio`: look and timing
- Quality: the webcam and gameplay regions are cropped and upscaled by ffmpeg (Lanczos, noise cleanup, sharpening) before Remotion places them, so a small webcam isn't blown up blurry in the browser. `render.panel_sharpen` sets how strong the sharpening is, `render.crf` / `x264_preset` / `frame_format` the final encode, `render.fps: 60` gives smoother gameplay, and `download.max_height` how sharp the source is (2160 = sharpest webcam if the video exists in 4K). Faster but softer: `frame_format: jpeg`, `x264_preset: medium`.
- AI upscaling: with an NVIDIA (or Apple) GPU, strongly enlarged regions, usually the webcam, are upscaled with [Real-ESRGAN](https://github.com/xinntao/Real-ESRGAN) first. That gives real detail instead of a smooth blur. It uses the PyTorch that is already installed; the ~10 MB of model weights download on first use into `models/`. `render.ai_upscale: off` turns it off, `on` forces it on a CPU too (slow). `render.ai_denoise` (0-1) sets how hard it cleans compression blocks and noise.

### Speaker labels for podcasts (optional)

Get a free token at [huggingface.co/settings/tokens](https://huggingface.co/settings/tokens), accept the terms of `pyannote/speaker-diarization-3.1` and `pyannote/segmentation-3.0`, then `export HF_TOKEN=hf_...` (Windows PowerShell: `$env:HF_TOKEN="hf_..."`) before preparing.

### Changing the look

Captions, hook and thumbnail styles live in `remotion/src/` (`Captions.tsx`, `Hook.tsx`, `Thumb.tsx`, `Clip.tsx`). Preview with `cd remotion && npm run studio`.

### Fewer permission prompts

Claude Code asks before running commands. `.claude/settings.json` already allows the tool's own commands; choose "don't ask again" for anything else you trust.

</details>

---

## Credits and license

This project is released under the [MIT License](LICENSE).

It builds on: yt-dlp (Unlicense), TwitchDownloader (MIT), WhisperX (BSD-2), OpenCV (Apache-2), Real-ESRGAN (BSD-3, model weights downloaded on first use), Montserrat font (SIL OFL, bundled). **Remotion** has its own license: free for individuals and companies of up to 3 people, larger companies need a license from [remotion.dev/license](https://remotion.dev/license).

**Only clip videos you own or have permission to use.**
