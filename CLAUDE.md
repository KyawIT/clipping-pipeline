# Clipping pipeline: instructions for Claude Code

This repo turns long videos (mostly Twitch streams with a webcam overlay, sometimes podcasts) into 9:16
TikTok / Reels / Shorts clips with animated word-by-word captions and a hook text overlay.

Heavy work runs locally in Python and Remotion. **Claude's job is only the judgment:** finding the webcam box,
choosing moments, setting exact cut points, writing hooks and titles. The main entry point is `/clip <job>`.

## Layout
- `clip.py`: CLI (prepare, status, list, words, frames, layout-detect, layout-save, validate, render)
- `pipeline/`: download (yt-dlp + TwitchDownloaderCLI), transcribe (WhisperX), signals (local scoring),
  vision (frames, webcam detection, face tracking), render (cut + Remotion)
- `remotion/`: React compositions (`src/Clip.tsx` layouts, `Captions.tsx`, `Hook.tsx`)
- `web/`: local web app (`clip.py web`, FastAPI + plain JS in `web/static/`). `runner.py` runs the same CLI stages
  as subprocesses and reads `@@{json}` progress lines (`pipeline.common.progress`, only printed when
  `CLIP_PROGRESS=1`) plus yt-dlp / WhisperX / Remotion output. The Claude step is headless
  `claude -p "/clip <job> ..." --output-format stream-json` with steps 1-6 only; the user reviews in the browser and
  the web app renders. `db.py` keeps the library in `jobs/library.db` (videos unique by platform + video id,
  per-platform upload checkmarks). If you add a pipeline stage, emit `progress()` events for it.
  The frontend's look and motion follow the `apple-design` skill (`.claude/skills/apple-design`): system type with
  size-specific tracking, translucent chrome, one accent colour (blue = action/selection), spring easing tokens
  (`--spring`), press feedback, reduced motion / transparency / contrast. Keep new UI consistent with it.
- `pipeline/panels.py`: ffmpeg pre-crops and upscales each layout region (cam, game, main...) to its exact on-screen
  size, so Remotion draws it 1:1 instead of upscaling in Chrome. Its geometry mirrors `Clip.tsx` / `CropView.tsx`:
  change a layout in both places. Small regions enlarged 1.4x or more (the webcam, <= 0.4 MP) go through Real-ESRGAN first
  (`pipeline/upscale.py`, GPU only unless `render.ai_upscale: on`).
- `jobs/<job>/`: audio.wav, chat.json, transcript.json, candidates.md, clips.json, out/, plus either
  `stream.json` (online video, the default: `download.video: sections`) or `source.mp4` (local files / `full`).
  `pipeline/media.py` hides the difference: frames and clip intervals (`segments/`, cached) are fetched from
  the stream only when needed, so every `frames` / `layout-detect` call is a small network fetch.
- `layouts/<platform>_<channel>.json`: saved webcam presets, reused for every stream of that channel
- Always use the venv: `.venv/bin/python clip.py ...` (Linux/macOS) or `.venv/Scripts/python.exe clip.py ...` (Windows)
- Setup / one-shot launchers: `setup.sh` + `clip.sh` (Linux), `setup.cmd` + `clip.cmd` (Windows, wrapping the `.ps1` files).
  Keep the `.ps1` files ASCII-only (PowerShell 5.1 reads them in the ANSI code page) and start external
  tools from Python with argument lists, never through a shell.

## Usage budget (Claude Pro plan, keep this tight)
- Never read `transcript.json`, `chat.json` or `signals.json` directly: they are huge. Use `candidates.md`
  and `clip.py words` for small ranges.
- Look at frames sparingly: at most ~2 per clip and only when the visual matters. They are 640 px JPGs.
- Run long commands (`prepare`, `render`) in the background and poll rarely (`sleep 60`) instead of
  printing their logs.
- Do the whole selection in one pass. Don't re-read files you already have in context.

## Picking clips (what goes viral on TikTok / Reels)
A good clip:
1. **Hooks in the first 1-2 seconds.** Start on the most surprising line, reaction, or a question,
   never on filler ("so yeah", "anyway", "uh"). Start mid-scene if needed; captions and the hook text add the context.
2. **Stands alone.** Someone who has never seen the streamer understands it without the previous hour.
3. **Has a payoff:** a punchline, a fail or clutch moment, a reveal, a strong reaction, a hot take.
   End 0.5-1.5 s after the payoff, before the energy drops. Don't end mid-sentence.
4. **Is 20-60 s** (hard limits from config: 15-90 s). Shorter is better when the moment is simple.
5. **Has energy:** a chat spike (the `chat Nx normal` value and spammed emotes like KEKW/OMEGALUL/CLIP IT
   are strong evidence), yelling or laughing, fast back-and-forth.

Strong types for streams: rage or fail moments, clutch plays, unexpected donations or chat interactions, funny
or wholesome reactions, hot takes or confessions, a "story time" with a punchline.
For podcasts: contrarian claims, a personal story with a twist, a heated disagreement, a quotable one-liner, a
surprising fact or number.

Avoid: moments that depend on context from earlier, long silent gameplay, reading chat names, sponsor reads,
anything that is only funny with sound that isn't in the transcript and that you haven't checked in frames,
and overlapping clips.

**Hook text** (the overlay for the first ~3 s): 3-9 words, in the speaker's language, creates curiosity without
spoiling the payoff, no hashtags, at most one emoji. Examples: "He did NOT expect chat to do this",
"Worst timing in streaming history", "She said what about her boss?".

**Layouts:** streams use `split` by default. Use `fullcam` when the face or reaction is the whole point,
or during just-chatting segments where the cam is already full screen in the source. Use `fullgame` when a play
needs the full gameplay view. Podcasts: `podcast_follow` (one person on screen at a time) or `podcast_stack`
(two people side by side in the source).

## Text motion and thumbnails
Hook words crash in one at a time from oversize; `*starred*` hook words get a yellow highlighter. Caption words
land as they are spoken; `emphasis` words stay yellow, land bigger and shake. Each clip also gets
`out/<id>_thumb.jpg`: the frame at `thumb_at` with a colour grade and huge tilted `thumb_text`.
**Thumbnail text:** 2-4 words, a promise or a tension rather than a summary, with exactly one starred word
(e.g. "I *KILLED* THE SERVER", "MY AI GOT *CREEPY*", "HE *LOST* EVERYTHING"). Pick a `thumb_at` with an expressive face
(open mouth, wide eyes, a pointing hand) over a neutral one.

## Motion design (motionmaxxing skill)
Installed at `~/.claude/skills/motionmaxxing`. Load it only when changing how things *move*: the animations in
`remotion/src/Hook.tsx` / `Captions.tsx`, or a one-off sting, intro or promo for a channel. **Never load it during
`/clip`**: picking moments is not motion design, and SKILL.md alone is ~20 KB of the usage budget.
- Apply its craft laws to the Remotion code; don't move to its GSAP/HyperFrames runtime. Remotion stays the renderer.
- The laws that matter most here: land decelerates with no bounce (most of the travel in the first 4-8 f), arrival
  comes from oversize and never grows from zero, words appear at speech rhythm (2-4 f apart for a fast hook), almost
  nothing fades, and one accent colour carries one meaning.
- Its "never ship" HUD/chrome rules apply to overlays too: no corner labels, counters or progress bars on clips.

## Notes
- Timestamps in clips.json can be seconds (`3725.4`) or `h:mm:ss.s` strings.
- Chat reacts ~6 s after the moment; the scorer already compensates (`signals.chat_lag_sec`).
- If the webcam overlay of a channel changes, run `layout-detect` / `layout-save` again: it overwrites the preset.
