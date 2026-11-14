"""Compose the recorded frames into the final demo video: titles, highlight badges and English subtitles.

    python compose.py --frames <frames dir> --out pulsar_demo.mp4            # with burned-in subtitles
    python compose.py --frames <frames dir> --out clean.mp4 --no-subs        # without (for editing)
    python compose.py --frames <frames dir> --preview 12.0 --out still.png   # one frame, to check layout

Also writes pulsar_demo.srt (the same subtitles as a separate track, for YouTube) next to this script.
Team name, presenter and URL come from video.json.
"""
import argparse
import json
import subprocess
from pathlib import Path

import imageio_ffmpeg
from PIL import Image, ImageDraw, ImageFont

from shots import FPS, H, N_SCENES, SHOTS, W

HERE = Path(__file__).parent
CFG = json.loads((HERE / 'video.json').read_text(encoding='utf-8'))
DATES = [s['date'] for s in json.loads((HERE / '../../web/public/data/sar_manaus.json').read_text())['scenes']]
FONTS = 'C:/Windows/Fonts/'


def font(size, w='sb'):
    return ImageFont.truetype(FONTS + {'r': 'segoeui.ttf', 'sb': 'seguisb.ttf', 'b': 'segoeuib.ttf',
                                       'bl': 'seguibl.ttf', 'l': 'segoeuil.ttf'}[w], size)


# where each shot starts (frame index) in the final cut
START, t0 = {}, 0
for s in SHOTS:
    START[s['name']] = t0
    t0 += s['frames']
TOTAL = t0
MAP_CX = 940  # middle of the map between the app's side panels at 1920 px

# ------------------------------------------------------------------ subtitles (narration script)
SUBS = [
    (0.0, 2.0, f"Hi, I'm {CFG['name']}, from team {CFG['team']}."),
    (2.0, 5.0, f"Our challenge: {CFG['challenge']}."),
    (5.0, 10.0, "This is PulSAR: the Amazon's pulse, seen by radar."),
    (10.0, 12.6, "NISAR's L-band radar sees through clouds, even under the forest canopy."),
    (12.6, 15.0, "As El Niño set in, the flooded forest near Manaus shrank by 97%."),
    (15.0, 20.0, "We add NASA FIRMS fires, NASA POWER sun and rain, and the people at risk."),
    (20.0, 25.0, "We forecast rivers, fire and hydropower months ahead,"),
    (25.0, TOTAL / FPS, "so the Amazon can prepare before the drought hits."),
]


def srt():
    def ts(t):
        ms = round(t * 1000)
        return f'{ms // 3600000:02d}:{ms // 60000 % 60:02d}:{ms // 1000 % 60:02d},{ms % 1000:03d}'
    return '\n'.join(f'{i}\n{ts(a)} --> {ts(b)}\n{txt}\n' for i, (a, b, txt) in enumerate(SUBS, 1))


# ------------------------------------------------------------------ drawing helpers
def fade(t, a, b, d=0.35):
    """Opacity 0→1→0 for an element shown between a and b seconds."""
    if t < a or t > b:
        return 0.0
    return max(0.0, min(1.0, (t - a) / d, (b - t) / d))


def wrap(draw, text, f, maxw):
    words = text.split()
    if draw.textlength(text, font=f) > maxw and len(words) > 3:
        # two balanced lines read better than one long line plus an orphan
        best = min(range(1, len(words)), key=lambda k: max(draw.textlength(' '.join(words[:k]), font=f),
                                                           draw.textlength(' '.join(words[k:]), font=f)))
        lines = [' '.join(words[:best]), ' '.join(words[best:])]
        if max(draw.textlength(l, font=f) for l in lines) <= maxw:
            return lines
    lines, cur = [], ''
    for w in text.split():
        nxt = (cur + ' ' + w).strip()
        if draw.textlength(nxt, font=f) <= maxw:
            cur = nxt
        else:
            lines.append(cur)
            cur = w
    return lines + [cur]


def text_box(layer, cx, y, text, f, alpha, pad=(26, 14), fill=(255, 255, 255), bg=(8, 12, 20), bga=200,
             maxw=1300, accent=None, anchor_bottom=False):
    if alpha <= 0:
        return
    d = ImageDraw.Draw(layer)
    lines = wrap(d, text, f, maxw)
    lh = f.size * 1.28
    w = max(d.textlength(l, font=f) for l in lines) + 2 * pad[0]
    h = lh * len(lines) + 2 * pad[1]
    if anchor_bottom:
        y -= h
    x = cx - w / 2
    d.rounded_rectangle([x, y, x + w, y + h], radius=14, fill=bg + (int(bga * alpha),))
    if accent:
        d.rounded_rectangle([x, y, x + 6, y + h], radius=3, fill=accent + (int(255 * alpha),))
    for i, l in enumerate(lines):
        lw = d.textlength(l, font=f)
        d.text((cx - lw / 2, y + pad[1] + i * lh), l, font=f, fill=fill + (int(255 * alpha),))


def badge(layer, t, a, b, title, body, accent, cx=MAP_CX, y=96):
    al = fade(t, a, b)
    if al <= 0:
        return
    d = ImageDraw.Draw(layer)
    ft, fb = font(22, 'b'), font(30, 'sb')
    w = max(d.textlength(title.upper(), font=ft), d.textlength(body, font=fb)) + 56
    x = cx - w / 2
    d.rounded_rectangle([x, y, x + w, y + 104], radius=14, fill=(8, 12, 20, int(215 * al)))
    d.rounded_rectangle([x, y, x + 6, y + 104], radius=3, fill=accent + (int(255 * al),))
    d.text((x + 28, y + 16), title.upper(), font=ft, fill=accent + (int(255 * al),))
    d.text((x + 28, y + 50), body, font=fb, fill=(255, 255, 255, int(255 * al)))


def big_title(layer, t, a, b, cx, cy, title, sub, size=150):
    al = fade(t, a, b, 0.5)
    if al <= 0:
        return
    d = ImageDraw.Draw(layer)
    ft, fs = font(size, 'bl'), font(size // 4, 'sb')
    tw = d.textlength(title, font=ft)
    d.text((cx - tw / 2 + 4, cy - size * 0.75 + 5), title, font=ft, fill=(0, 0, 0, int(160 * al)))
    d.text((cx - tw / 2, cy - size * 0.75), title, font=ft, fill=(255, 255, 255, int(255 * al)))
    sw = d.textlength(sub, font=fs)
    d.text((cx - sw / 2, cy + size * 0.42), sub, font=fs, fill=(190, 230, 255, int(255 * al)))


def date_label(i):
    """Radar date shown in the time-lapse (mirrors the slider function in shots.py)."""
    k = i - START['timelapse']
    t = k / (SHOTS[2]['frames'] - 1)
    idx = min(N_SCENES - 1, int(t * 1.15 * N_SCENES))
    y, m, dd = DATES[idx].split('-')
    mon = ['Jan', 'Feb', 'Mar', 'Apr', 'May', 'Jun', 'Jul', 'Aug', 'Sep', 'Oct', 'Nov', 'Dec'][int(m) - 1]
    return f'{int(dd)} {mon} {y}'


# ------------------------------------------------------------------ one frame
def frame_at(i, frames_dir, subs=True):
    shot = next(s for s in reversed(SHOTS) if START[s['name']] <= i)
    k = i - START[shot['name']]
    img = Image.open(frames_dir / shot['name'] / f'{k:04d}.jpg').convert('RGBA')
    t = i / FPS
    lay = Image.new('RGBA', (W, H), (0, 0, 0, 0))
    d = ImageDraw.Draw(lay)
    name = shot['name']

    if name == 'intro':
        # soft vignette so the text reads over the radar
        for yy in range(0, 360, 4):
            d.rectangle([0, yy, W, yy + 4], fill=(4, 8, 14, int(170 * (1 - yy / 360))))
        al = fade(t, 0.2, 5.2, 0.5)
        d.text((80, 64), 'NASA SPACE APPS CHALLENGE 2026', font=font(30, 'b'), fill=(160, 200, 230, int(255 * al)))
        a2 = fade(t, 0.3, 5.2, 0.4)
        d.text((80, 112), f"Team {CFG['team']}", font=font(64, 'b'), fill=(255, 255, 255, int(255 * a2)))
        a3 = fade(t, 2.0, 5.2, 0.4)
        d.text((80, 200), 'Challenge', font=font(28, 'sb'), fill=(26, 217, 191, int(255 * a3)))
        d.text((80, 234), CFG['challenge'], font=font(56, 'sb'), fill=(255, 255, 255, int(255 * a3)))

    elif name == 'app':
        big_title(lay, t, 5.3, 10.0, MAP_CX, 520, 'PulSAR', "Feel the Amazon's pulse from space")

    elif name == 'timelapse':
        badge(lay, t, 10.1, 15.0, 'NISAR L-band radar · Manaus', 'Water seen through clouds and under the canopy',
              (31, 120, 242))
        al = fade(t, 10.1, 15.0)
        d.text((MAP_CX - 160, 750), date_label(i), font=font(64, 'b'), fill=(255, 255, 255, int(255 * al)),
               stroke_width=3, stroke_fill=(0, 0, 0, int(200 * al)))
        a2 = fade(t, 12.4, 15.0)
        d.text((MAP_CX - 160, 695), 'Flooded forest  6,041 → 157 km²', font=font(36, 'sb'),
               fill=(26, 217, 191, int(255 * a2)), stroke_width=3, stroke_fill=(0, 0, 0, int(200 * a2)))
        text_box(lay, MAP_CX, 210, 'Radar water validated against ESA Sentinel-2 · F1 0.89', font(24, 'sb'),
                 fade(t, 11.0, 15.0), accent=(34, 197, 94))

    elif name == 'fire':
        badge(lay, t, 15.05, 17.5, 'NASA FIRMS + NISAR radar', '222 burn scars · 92% confirmed by fires (12% at random)',
              (255, 59, 31))

    elif name == 'firenow':
        badge(lay, t, 17.5, 20.0, 'Fire now · next 72 h', 'Fire risk, wind and smoke over the people in its path',
              (249, 115, 22))

    elif name == 'energy':
        badge(lay, t, 20.05, 25.0, 'Hydropower in drought', '2024 drought: Belo Monte, Santo Antônio and Jirau at 54% of normal',
              (245, 158, 11))
        text_box(lay, MAP_CX, 210, 'ML forecasts 1–6 months ahead · river level skill +0.25 over climatology',
                 font(24, 'sb'), fade(t, 21.0, 25.0), accent=(26, 217, 191))

    elif name == 'outro':
        al = min(1.0, (t - 25.0) / 0.6)
        d.rectangle([0, 0, W, H], fill=(4, 8, 14, int(185 * al)))
        big_title(lay, t, 25.1, 99, W / 2, 400, 'PulSAR', "Feel the Amazon's pulse from space", size=170)
        a2 = fade(t, 25.6, 99, 0.5)
        f = font(40, 'sb')
        tw = d.textlength(CFG['url'], font=f)
        d.text((W / 2 - tw / 2, 600), CFG['url'], font=f, fill=(26, 217, 191, int(255 * a2)))
        a3 = fade(t, 26.0, 99, 0.5)
        for j, line in enumerate([
            'NISAR (NASA–ISRO) · NASA FIRMS · NASA POWER · ESA Sentinel-2 & WorldCover · NOAA · ANA · ONS · WorldPop',
            f"Team {CFG['team']} · NASA Space Apps Challenge 2026 · {CFG['challenge']}",
            'Screen captures of the PulSAR app · video edited with AI assistance (Claude)',
        ]):
            ff = font(26 if j < 2 else 20, 'r')
            lw = d.textlength(line, font=ff)
            d.text((W / 2 - lw / 2, 700 + j * 44), line, font=ff, fill=(200, 210, 225, int(235 * a3)))

    if subs:
        sub = next((s for s in SUBS if s[0] <= t < s[1]), None)
        if sub:
            dock = name in ('firenow', 'energy')
            text_box(lay, MAP_CX if name not in ('intro', 'outro') else W / 2, 878 if dock else 1040, sub[2],
                     font(40, 'sb'), fade(t, sub[0], sub[1], 0.12), bga=205, anchor_bottom=True)

    out = Image.alpha_composite(img, lay).convert('RGB')
    if i < 12:  # fade in from black
        out = Image.blend(Image.new('RGB', (W, H)), out, i / 12)
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--frames', required=True)
    ap.add_argument('--out', required=True)
    ap.add_argument('--no-subs', action='store_true')
    ap.add_argument('--preview', type=float, nargs='*')
    a = ap.parse_args()
    frames_dir = Path(a.frames)
    (HERE / 'pulsar_demo.srt').write_text(srt(), encoding='utf-8')
    if a.preview:
        for j, t in enumerate(a.preview):
            out = Path(a.out)
            frame_at(min(TOTAL - 1, round(t * FPS)), frames_dir, not a.no_subs).save(
                out.with_name(f'{out.stem}_{t:05.2f}{out.suffix}'))
        return
    cmd = [imageio_ffmpeg.get_ffmpeg_exe(), '-y', '-f', 'rawvideo', '-pix_fmt', 'rgb24', '-s', f'{W}x{H}',
           '-r', str(FPS), '-i', '-', '-c:v', 'libx264', '-preset', 'slow', '-crf', '18', '-pix_fmt', 'yuv420p',
           '-movflags', '+faststart', a.out]
    p = subprocess.Popen(cmd, stdin=subprocess.PIPE, stderr=subprocess.DEVNULL)
    for i in range(TOTAL):
        p.stdin.write(frame_at(i, frames_dir, not a.no_subs).tobytes())
    p.stdin.close()
    p.wait()
    print(f'{a.out}: {TOTAL} frames, {TOTAL / FPS:.2f} s')


if __name__ == '__main__':
    main()
