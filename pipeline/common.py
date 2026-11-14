"""Shared paths, AOI config and HTTP helpers for the pipeline."""
from __future__ import annotations

import json
import os
import sys
import time
from pathlib import Path

import requests

for _s in (sys.stdout, sys.stderr):  # Windows consoles default to cp1252
    if hasattr(_s, "reconfigure"):
        _s.reconfigure(encoding="utf-8", errors="replace")

ROOT = Path(__file__).resolve().parents[1]
# Raw downloads (NISAR ~4 GB/scene) live OUTSIDE the project so OneDrive doesn't sync them.
# Override with the env var SPACEAPPS_DATA_RAW.
DATA_RAW = Path(os.getenv("SPACEAPPS_DATA_RAW", Path.home() / "spaceapps_data" / "raw"))
DATA_PROCESSED = ROOT / "data" / "processed"
WEB_DATA = ROOT / "web" / "public" / "data"  # served by Vite as /data/...

for _d in (DATA_RAW, DATA_PROCESSED, WEB_DATA):
    _d.mkdir(parents=True, exist_ok=True)


def load_aois() -> dict:
    with open(ROOT / "config" / "aois.json", encoding="utf-8") as f:
        return json.load(f)


WEB_RES_DEG = 0.0009  # ~100 m: the radar / land-cover analysis grid
WIDE_DEG = 1.0        # fire, energy and (now) radar views cover the AOI ± this many degrees


def sar_grid(aoi: dict):
    """Radar analysis grid: AOI ± ~1°, snapped so the AOI is an exact pixel window inside it.

    Returns (bbox, transform, shape, aoi_window) with aoi_window = (row0, col0, height, width).
    """
    from rasterio.transform import from_origin
    w, s, e, n = aoi["bbox"]
    k = round(WIDE_DEG / WEB_RES_DEG)
    h, wd = int(round((n - s) / WEB_RES_DEG)), int(round((e - w) / WEB_RES_DEG))
    W, N = w - k * WEB_RES_DEG, n + k * WEB_RES_DEG
    shape = (h + 2 * k, wd + 2 * k)
    bbox = [W, N - shape[0] * WEB_RES_DEG, W + shape[1] * WEB_RES_DEG, N]
    return bbox, from_origin(W, N, WEB_RES_DEG, WEB_RES_DEG), shape, (k, k, h, wd)


def aoi_crop(arr, aoi: dict):
    """Cut the original AOI window out of an array on the wide radar grid."""
    r, c, h, w = sar_grid(aoi)[3]
    return arr[r:r + h, c:c + w]


# Local NISAR dataset (~17 MB per scene on the wide grid): outside OneDrive, like the raw downloads.
NISAR_DATASET = Path(os.getenv("SPACEAPPS_NISAR_DATASET", Path.home() / "spaceapps_data" / "nisar"))
DB_SCALE = 10  # dataset stores dB × 10 as int16 (0.1 dB steps — finer is speckle; nodata = -32768)


def read_scene(aoi_key: str, date: str):
    """One scene of the local NISAR dataset on the wide radar grid: (hh_db, hv_db, source).

    source: 0 = no data, 1 = main track (that date), 2 = filler track (another date ≤ 12 days away).
    """
    import numpy as np
    import rasterio
    with rasterio.open(NISAR_DATASET / aoi_key / f"{date}_100m.tif") as src:
        a = src.read().astype("float32")
    hh, hv = (np.where(a[i] == -32768, np.nan, a[i] / DB_SCALE) for i in (0, 1))
    return hh, hv, a[2].astype("uint8")


def save_overlay(path: Path, rgba, quality: int = 75) -> Path:
    """Map overlay (float RGBA 0–1) as lossy WebP with alpha: ~1/6–1/20 of a PNG for these noisy 100 m rasters."""
    import numpy as np
    from PIL import Image
    a = np.nan_to_num(np.asarray(rgba, dtype="float32"))
    img = Image.fromarray((np.clip(a, 0, 1) * 255 + 0.5).astype("uint8"), "RGBA")
    path = path.with_suffix(".webp")
    img.save(path, "WEBP", quality=quality, method=4)
    return path


def bbox_wkt(bbox: list[float]) -> str:
    w, s, e, n = bbox
    return f"POLYGON(({w} {s},{e} {s},{e} {n},{w} {n},{w} {s}))"


def http_get(url: str, params: dict | None = None, retries: int = 4, timeout: int = 120, **kw) -> requests.Response:
    """GET with exponential backoff (ANA and other public services are flaky)."""
    last = None
    for attempt in range(retries):
        try:
            r = requests.get(url, params=params, timeout=timeout, **kw)
            if r.status_code < 500:
                r.raise_for_status()
                return r
            last = RuntimeError(f"HTTP {r.status_code}")
        except requests.RequestException as e:  # noqa: PERF203
            last = e
        wait = 5 * 2**attempt
        print(f"  attempt {attempt + 1}/{retries} failed ({last}); retrying in {wait}s")
        time.sleep(wait)
    raise RuntimeError(f"Download failed {url}: {last}")


def write_json(path: Path, obj) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(obj, f, ensure_ascii=False, separators=(",", ":"))
    print(f"  -> {path.relative_to(ROOT) if path.is_relative_to(ROOT) else path}")
