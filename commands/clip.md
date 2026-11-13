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
      "layout": "split", "hook": "He thought chat wasn't watching...",
      "title": "...", "description": "...", "hashtags": ["#twitch", "#gaming"],
      "score": 92, "reason": "chat 6x spike, standalone story with a payoff"}
   ]}
   ```
   layouts: `split` (cam top / gameplay bottom, the default for streams), `fullcam` (big reaction or just chatting),
   `fullgame` (the play itself is the clip; cam bubble), `podcast_follow`, `podcast_stack` (two people), `center`.

6. **Validate** with `.venv/bin/python clip.py validate <job>` and fix anything it reports.

7. **Show the user a short table** (id, time, length, layout, hook, why) and ask whether to render all of them or
   change something. If they already said to just go ahead, skip asking. Then run
   `.venv/bin/python clip.py render <job>` in the background (it can take minutes per clip) and report where
   the files are: `jobs/<job>/out/`.
