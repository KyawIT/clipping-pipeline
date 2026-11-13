---
description: Pick the most viral moments from a prepared job and render 9:16 clips
argument-hint: <job-id | twitch/youtube URL | local file>
---

Turn this into short-form clips: $ARGUMENTS

Follow CLAUDE.md ("Picking clips" and "Usage budget"). Steps:

1. **Resolve the job.**
   - If the argument is an existing folder in `jobs/`, use it.
   - If it is a URL or file path: run `.venv/bin/python clip.py prepare "<arg>"` in the background
     (it can take a long time) and poll occasionally with `sleep 60` plus a look at the tail of the output.
     Wait until it prints `READY job=...`. Tell the user they can run `prepare` in their own terminal next time
     to save usage.
   - Run `.venv/bin/python clip.py status <job>` once.

2. **Webcam layout (stream mode only, once per channel).** If `status` shows no layout preset:
   - Run `.venv/bin/python clip.py layout-detect <job>` and look at the 3 `frames/layout_*.jpg` images
     (the cyan grid lines are labeled in % of width/height; the red box is the guess).
   - Decide the real webcam overlay rectangle (the whole cam frame, not just the face) and save it with
     `.venv/bin/python clip.py layout-save <job> --cam x,y,w,h` (fractions 0-1). Add `--game x,y,w,h` only if
     the gameplay does not fill the frame. If there is no webcam at all, save without `--cam`
     and use `fullgame`/`center` layouts only.
   - Look at `frames/layout_saved.jpg` once to confirm.

3. **Read `jobs/<job>/candidates.md`** (not transcript.json). Shortlist about 12 candidates that best meet the
   clip criteria in CLAUDE.md.

4. **Refine boundaries** for each one you shortlisted with
   `.venv/bin/python clip.py words <job> --from <t> --to <t>` (word timestamps to 0.1 s). Start on the hook,
   end right after the payoff. Only if the visuals matter (gameplay moment, reaction face), check 1-2 frames with
   `.venv/bin/python clip.py frames <job> --at t1,t2`.

5. **Write `jobs/<job>/clips.json`** with the best `clips.target` clips from config.yaml (default 8), ranked:
   ```json
   {"clips": [
     {"id": "clip_01", "start": "1:02:11.4", "end": "1:02:58.0",
      "layout": "split", "hook": "He thought chat *wasn't* watching...",
      "emphasis": ["never", "insane"],
      "thumb_text": "CHAT WAS *WATCHING*", "thumb_at": "1:02:40.2",
      "title": "...", "description": "...", "hashtags": ["#twitch", "#gaming"],
      "score": 92, "reason": "chat 6x spike, standalone story with a payoff"}
   ]}
   ```
   Text motion and thumbnail fields (see "Text motion and thumbnails" in CLAUDE.md):
   - `hook`: wrap the 1-2 punch words in `*stars*` (they get a highlighter bar).
   - `emphasis`: 1-4 words that are actually spoken in the clip (take them from the `words` output); they pop in the captions.
   - `thumb_text`: 2-4 words, ALL CAPS, one `*starred*` word. Make it different from the hook.
   - `thumb_at`: the strongest face or reaction moment inside the clip. Use a timestamp you already saw in `words`
     or `frames`; don't pull extra frames just for this. Without it, the thumbnail uses the frame 35% into the clip.
   - `thumb_layout` (optional): a `split` clip uses `fullcam` for the thumbnail automatically. Override it only
     when the gameplay is the point.
   layouts: `split` (cam top / gameplay bottom, the default for streams), `fullcam` (big reaction or just chatting),
   `fullgame` (the play itself is the clip; cam bubble), `podcast_follow`, `podcast_stack` (two people), `center`.

6. **Validate** with `.venv/bin/python clip.py validate <job>` and fix anything it reports.

7. **Show the user a short table** (id, time, length, layout, hook, why) and ask whether to render all of them or
   change something. If they already said to just go ahead, skip asking. Then run
   `.venv/bin/python clip.py render <job>` in the background (it can take minutes per clip) and report where
   the files are: `jobs/<job>/out/` (`clip_XX.mp4`, `clip_XX_thumb.jpg`, `clip_XX.txt`). To redo only the thumbnails,
   run `render <job> --thumbs`.
