"""ESA WorldCover 2021 (10 m) -> class fractions on the ~100 m radar grid of each AOI (AOI ± ~1°).

Read straight from the public Cloud-Optimized GeoTIFFs on AWS (only the window is fetched, sampled at
~30 m from the COG overviews: plenty for fractions on a 100 m grid and ~9x fewer bytes than 10 m).
Used by process_gcov.py to:
  * stop cities from being labelled "flooded forest" (buildings also cause double-bounce, very bright HH)
  * provide a reference for validation (permanent water, herbaceous wetland)

Output: data/processed/landcover_<aoi>.npz with arrays built, water, wetland, tree (fraction 0–1).
"""
import math

import numpy as np
import rasterio
from rasterio.warp import Resampling, reproject
from rasterio.windows import from_bounds

from common import DATA_PROCESSED, load_aois, sar_grid

URL = "/vsicurl/https://esa-worldcover.s3.eu-central-1.amazonaws.com/v200/2021/map/ESA_WorldCover_10m_2021_v200_{}_Map.tif"
CLASSES = {"tree": 10, "built": 50, "water": 80, "wetland": 90}
DECIMATE = 3  # 10 m -> ~30 m samples


def tile_names(bbox):
    w, s, e, n = bbox
    for lat in range(math.floor(s / 3) * 3, math.ceil(n / 3) * 3, 3):
        for lon in range(math.floor(w / 3) * 3, math.ceil(e / 3) * 3, 3):
            yield f"{'N' if lat >= 0 else 'S'}{abs(lat):02d}{'E' if lon >= 0 else 'W'}{abs(lon):03d}"


def main():
    for key, aoi in load_aois().items():
        bbox, dst_tr, shape, _ = sar_grid(aoi)
        out = {k: np.full(shape, np.nan, "float32") for k in CLASSES}
        for t in tile_names(bbox):
            with rasterio.open(URL.format(t)) as src:
                tb = src.bounds
                ib = (max(bbox[0], tb.left), max(bbox[1], tb.bottom), min(bbox[2], tb.right), min(bbox[3], tb.top))
                if ib[0] >= ib[2] or ib[1] >= ib[3]:
                    continue
                win = from_bounds(*ib, transform=src.transform).round_offsets().round_lengths()
                oh, ow = max(1, int(win.height) // DECIMATE), max(1, int(win.width) // DECIMATE)
                lc = src.read(1, window=win, out_shape=(oh, ow), resampling=Resampling.nearest)
                tr = src.window_transform(win) * rasterio.Affine.scale(win.width / ow, win.height / oh)
                print(f"  {key}: tile {t} {lc.shape}")
            for k, code in CLASSES.items():
                frac = np.full(shape, np.nan, "float32")
                reproject((lc == code).astype("float32"), frac, src_transform=tr, src_crs="EPSG:4326",
                          dst_transform=dst_tr, dst_crs="EPSG:4326", resampling=Resampling.average,
                          dst_nodata=np.nan)
                out[k] = np.where(np.isfinite(frac), frac, out[k])
        np.savez_compressed(DATA_PROCESSED / f"landcover_{key}.npz", **out)
        print(f"  {key}: urbano {np.nanmean(out['built']) * 100:.1f}% | água {np.nanmean(out['water']) * 100:.1f}%"
              f" | área úmida {np.nanmean(out['wetland']) * 100:.1f}% | árvores {np.nanmean(out['tree']) * 100:.1f}%")


if __name__ == "__main__":
    main()
