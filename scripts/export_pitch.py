"""Export the 7-slide demo (web/public/pitch/index.html) to PNGs and a PDF.

Each slide is captured at 1280×720 by a headless Chromium browser (Edge or Chrome) through the deck's
?slide=N mode, then the images are joined into web/public/pitch/PulSAR-demo.pdf (a few MB).

Needs the site running, e.g. `cd web && npm run dev` (default URL below) or the published site:
  python scripts/export_pitch.py
  python scripts/export_pitch.py --url https://ricardo-ramos1.github.io/pulsar-amazonia/pitch/index.html
"""
import argparse
import shutil
import subprocess
import tempfile
from pathlib import Path

from PIL import Image

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "web" / "public" / "pitch" / "PulSAR-demo.pdf"
BROWSERS = [
    r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe",
    r"C:\Program Files\Google\Chrome\Application\chrome.exe",
    "msedge", "google-chrome", "chromium", "chromium-browser",
]


def browser() -> str:
    for b in BROWSERS:
        if Path(b).exists() or shutil.which(b):
            return b
    raise SystemExit("No Edge/Chrome found: pass --browser <path>")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--url", default="http://localhost:5173/pitch/index.html")
    ap.add_argument("--browser", default=None)
    ap.add_argument("--slides", type=int, default=7)
    a = ap.parse_args()
    exe = a.browser or browser()
    pages = []
    with tempfile.TemporaryDirectory() as tmp:
        for n in range(1, a.slides + 1):
            png = Path(tmp) / f"s{n}.png"
            subprocess.run([exe, "--headless=new", "--hide-scrollbars", "--window-size=1280,720",
                            "--virtual-time-budget=8000", f"--screenshot={png}", f"{a.url}?slide={n}"],
                           check=False, capture_output=True, timeout=120)
            if not png.exists():
                raise SystemExit(f"slide {n}: capture failed (is the site running at {a.url}?)")
            pages.append(Image.open(png).convert("RGB"))
            print(f"  slide {n} ok")
        pages[0].save(OUT, save_all=True, append_images=pages[1:], resolution=96)
    print(f"-> {OUT.relative_to(ROOT)} ({OUT.stat().st_size / 1e6:.1f} MB)")


if __name__ == "__main__":
    main()
