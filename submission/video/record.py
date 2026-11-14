"""Record the PulSAR demo video frame by frame from the running web app.

Each frame is rendered deterministically: the script moves the map camera with jumpTo, waits until
tiles and layers have drawn, then takes a screenshot. That gives a smooth 30 fps video no matter how
slow the machine is. Overlays and subtitles are added afterwards by compose.py.

    python record.py --url http://localhost:5173/ --out <frames dir>            # every shot
    python record.py --url ... --out ... --shot timelapse                          # one shot
    python record.py --url ... --out ... --stills                                  # first/last frame only

Needs: playwright (uses the installed Microsoft Edge), the app served with ?capture support.
"""
import argparse
import math
import time
from pathlib import Path

from playwright.sync_api import sync_playwright

from shots import FPS, SHOTS, W, H

CLEAN_CSS = """
body.cap-clean #panel, body.cap-clean #charts, body.cap-clean #dock, body.cap-clean .tabs { display: none !important; }
.maplibregl-ctrl-bottom-left { display: none !important; }
"""

WAIT_JS = """
async (cam) => {
  const m = window.__pulsarMap;
  m.stop();
  m.jumpTo(cam);
  await new Promise((r) => {
    const t = setTimeout(r, 8000);
    m.once('idle', () => { clearTimeout(t); r(); });
    m.triggerRepaint();
  });
  await new Promise((r) => requestAnimationFrame(() => requestAnimationFrame(r)));
}
"""


def ease(t):
    return t * t * (3 - 2 * t)


def lerp_cam(a, b, t):
    t = ease(t)
    return {
        'center': [a['center'][0] + (b['center'][0] - a['center'][0]) * t,
                   a['center'][1] + (b['center'][1] - a['center'][1]) * t],
        'zoom': a['zoom'] + (b['zoom'] - a['zoom']) * t,
        'pitch': a['pitch'] + (b['pitch'] - a['pitch']) * t,
        'bearing': a['bearing'] + (b['bearing'] - a['bearing']) * t,
    }


def set_slider(page, sel, value):
    page.evaluate("""([sel, v]) => { const e = document.querySelector(sel);
        if (+e.value !== v) { e.value = v; e.dispatchEvent(new Event('input', { bubbles: true })); } }""", [sel, value])


def record_shot(page, shot, out, stills):
    page.evaluate("([m, clean]) => { document.querySelector(`.tabs button[data-tab='${m}']`).click();"
                  " document.body.classList.toggle('cap-clean', clean); }", [shot['mode'], shot.get('clean', False)])
    for sel, v in shot.get('setup', {}).items():
        set_slider(page, sel, v)
    time.sleep(2.5)  # mode switch: charts, dock cards and images settle
    d = out / shot['name']
    d.mkdir(parents=True, exist_ok=True)
    n = shot['frames']
    frames = [0, n - 1] if stills else range(n)
    for i in frames:
        t = i / max(1, n - 1)
        for sel, fn in shot.get('sliders', {}).items():
            before = page.evaluate(f"+document.querySelector('{sel}').value")
            v = fn(t)
            if v != before:
                set_slider(page, sel, v)
                time.sleep(0.35)  # new radar scene images decode
        page.evaluate(WAIT_JS, lerp_cam(shot['from'], shot['to'], t))
        page.screenshot(path=str(d / f'{i:04d}.jpg'), type='jpeg', quality=94)
    print(f"{shot['name']}: {len(list(frames)) if stills else n} frames", flush=True)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--url', default='http://localhost:5173/')
    ap.add_argument('--out', required=True)
    ap.add_argument('--shot', action='append')
    ap.add_argument('--stills', action='store_true')
    ap.add_argument('--headed', action='store_true')
    a = ap.parse_args()
    out = Path(a.out)
    with sync_playwright() as p:
        b = p.chromium.launch(channel='msedge', headless=not a.headed,
                              args=['--use-angle=d3d11', '--enable-gpu-rasterization', '--ignore-gpu-blocklist'])
        page = b.new_page(viewport={'width': W, 'height': H}, device_scale_factor=1)
        url = a.url + ('&' if '?' in a.url else '?') + 'capture#rivers'
        page.goto(url)
        page.add_style_tag(content=CLEAN_CSS)
        page.wait_for_function('window.__pulsarMap && window.__pulsarMap.loaded()', timeout=60000)
        time.sleep(6)  # data, scene images and charts
        if a.shot == ['camera']:  # print the app's own framing of each view, to tune shots.py
            for m in ['rios', 'fogo', 'agora', 'energia']:
                page.evaluate(f"document.querySelector(`.tabs button[data-tab='{m}']`).click()")
                time.sleep(2)
                print(m, page.evaluate("(() => { const m = window.__pulsarMap; const c = m.getCenter();"
                                       " return {center: [c.lng, c.lat], zoom: m.getZoom(), pitch: m.getPitch(), bearing: m.getBearing()}; })()"))
            return
        for shot in SHOTS:
            if a.shot and shot['name'] not in a.shot:
                continue
            record_shot(page, shot, out, a.stills)
        b.close()


if __name__ == '__main__':
    main()
