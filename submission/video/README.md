# Demo video (29.5 s)

The video is recorded from the real app, frame by frame, and then composed with titles, highlight badges and
English subtitles. Space Apps rules: **30 s or less**, English subtitles if narration is in another language,
public link with no login.

## Narration script (matches the subtitles)

| Time | Line | Shot |
|---|---|---|
| 0.0–2.0 | Hi, I'm *name*, from team *team*. | radar over Manaus, team name on screen |
| 2.0–5.0 | Our challenge: Dancing with the SARs. | same, challenge name on screen |
| 5.0–10.0 | This is PulSAR: the Amazon's pulse, seen by radar. | the app, Rivers view |
| 10.0–12.6 | NISAR's L-band radar sees through clouds, even under the forest canopy. | radar time-lapse Jun → Nov |
| 12.6–15.0 | As El Niño set in, the flooded forest near Manaus shrank by 97%. | time-lapse ends dry |
| 15.0–20.0 | We add NASA FIRMS fires, NASA POWER sun and rain, and the people at risk. | Fire, then Fire now |
| 20.0–25.0 | We forecast rivers, fire and hydropower months ahead, | Energy |
| 25.0–29.5 | so the Amazon can prepare before the drought hits. | end card |

About 70 words: speak calmly, about 2.4 words per second. If you narrate in Portuguese, the English subtitles
stay as they are (Space Apps allows that).

## Rebuild

```bash
# 1. serve the app (npm --prefix web run dev), then record the frames (~30 min)
python record.py --url http://localhost:5173/ --out <frames>
# 2. edit video.json (name, team), then compose
python compose.py --frames <frames> --out pulsar_demo.mp4
python compose.py --frames <frames> --out pulsar_demo_clean.mp4 --no-subs
```

Needs `playwright`, `imageio-ffmpeg` and `pillow`, plus Microsoft Edge. `shots.py` holds the camera moves and
`compose.py` the overlays and subtitles. `pulsar_demo.srt` is the same subtitles as a separate file for YouTube.

The frames are screen captures of the PulSAR app. The recording and composing scripts were written with AI
assistance (Claude), which the end card states.
