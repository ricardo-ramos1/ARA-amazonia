"""Population grid for the 3D map (WorldPop 2020, ~1 km, UN-adjusted, Brazil).

Writes per AOI:
  data/processed/pop_<aoi>.tif     — 1 km grid clipped to the AOI (used to aggregate SAR masks)
  web/data/population_<aoi>.json   — cells [lon, lat, pop] for deck.gl
"""
import numpy as np
import rasterio
from rasterio.windows import from_bounds

from common import DATA_PROCESSED, DATA_RAW, WEB_DATA, http_get, load_aois, write_json

URL = ("https://data.worldpop.org/GIS/Population/Global_2000_2020_1km_UNadj/2020/BRA/"
       "bra_ppp_2020_1km_Aggregated_UNadj.tif")
SRC = DATA_RAW / "worldpop_bra_2020_1km.tif"
WIDE_DEG = 1.0  # must match AOI_BUFFER_DEG in fetch_fires.py


def download():
    if SRC.exists():
        return
    print("WorldPop Brasil 2020 (~44 MB)…")
    r = http_get(URL, stream=True, timeout=300)
    with open(SRC, "wb") as f:
        for chunk in r.iter_content(1 << 20):
            f.write(chunk)


def main():
    download()
    jobs = []
    for key, aoi in load_aois().items():
        w, s, e, n = aoi["bbox"]
        jobs.append((key, aoi["bbox"]))  # radar footprint (river view)
        # wider window = same ±1° buffer used for fires/smoke (fire views)
        jobs.append((f"{key}_wide", [w - WIDE_DEG, s - WIDE_DEG, e + WIDE_DEG, n + WIDE_DEG]))
    with rasterio.open(SRC) as src:
        for key, bbox in jobs:
            win = from_bounds(*bbox, transform=src.transform).round_offsets().round_lengths()
            arr = src.read(1, window=win).astype("float32")
            arr[(arr < 0) | ~np.isfinite(arr)] = 0
            tr = src.window_transform(win)
            prof = src.profile | dict(height=arr.shape[0], width=arr.shape[1], transform=tr,
                                      dtype="float32", nodata=0, compress="deflate")
            with rasterio.open(DATA_PROCESSED / f"pop_{key}.tif", "w", **prof) as dst:
                dst.write(arr, 1)

            rows, cols = np.nonzero(arr > 0.5)
            xs, ys = rasterio.transform.xy(tr, rows, cols)
            cells = [[round(x, 4), round(y, 4), round(float(p), 1)]
                     for x, y, p in zip(xs, ys, arr[rows, cols])]
            write_json(WEB_DATA / f"population_{key}.json",
                       {"cell_deg": abs(tr.a), "total": round(float(arr.sum())), "cells": cells})
            print(f"  {key}: {len(cells)} células, {arr.sum():,.0f} habitantes")


if __name__ == "__main__":
    main()
