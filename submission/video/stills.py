"""Clean, matching stills for the slide deck (same camera, first vs last radar date)."""
import sys
from pathlib import Path

from playwright.sync_api import sync_playwright

import record
from shots import N_SCENES, cam

CAM = cam(-60.0, -3.11, 8.55, 52, -18)
STILLS = [
    {'name': 'before', 'mode': 'rios', 'clean': True, 'frames': 1, 'setup': {'#date': 0}, 'from': CAM, 'to': CAM},
    {'name': 'after', 'mode': 'rios', 'clean': True, 'frames': 1, 'setup': {'#date': N_SCENES - 1}, 'from': CAM, 'to': CAM},
    {'name': 'fireclean', 'mode': 'fogo', 'clean': True, 'frames': 1,
     'from': cam(-59.95, -3.15, 8.2, 55, -15), 'to': cam(-59.95, -3.15, 8.2, 55, -15)},
]

out = Path(sys.argv[1])
with sync_playwright() as p:
    b = p.chromium.launch(channel='msedge', args=['--use-angle=d3d11', '--ignore-gpu-blocklist'])
    page = b.new_page(viewport={'width': record.W, 'height': record.H})
    page.goto('http://localhost:5173/?capture#rivers')
    page.add_style_tag(content=record.CLEAN_CSS)
    page.wait_for_function('window.__pulsarMap && window.__pulsarMap.loaded()', timeout=60000)
    page.wait_for_timeout(6000)
    for s in STILLS:
        record.record_shot(page, s, out, False)
    b.close()
