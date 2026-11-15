"""NISAR L2 GCOV -> local 100 m dataset (wide area, every date) -> classification, web overlays, exposure.

Two stages:

  ingest   (remote, nothing raw is stored) for every date in <dataset>/<aoi>/plan.json
           (made by `search_nisar.py --plan`: all passes of the AOI's main track + a filler track ≤12 days away)
    1. open each GCOV frame over HTTPS and read ONLY the HH/HV chunks inside the wide grid
       (frequencyB, 80 m — ~1/20 of the bytes of the 20 m band; frequencyA block-averaged to 80 m when B is absent)
    2. average LINEAR power onto the ~100 m WGS84 grid (multilooking), frames of one pass mosaicked, dB
    3. trim the noisy swath edges of each pass
    4. main pass first; holes filled by the planned filler passes, then by reserve passes while the measured
       coverage is below TARGET_COVERAGE (band 3 records the source pass of every pixel: 1 = main, 2.. = fillers)
    -> ~/spaceapps_data/nisar/<aoi>/<date>_100m.tif (int16 dB×10: HH, HV, source) + manifest.json (granules, md5, bytes read)

  classify (always, from the local dataset)
       open water        HH < per-pass threshold (histogram valley, Otsu fallback)
       flooded forest    HH > FLOODED_FOREST_DB     (water + trunks -> double bounce, very bright)
       urban             bright pixels where WorldCover says built-up (buildings double-bounce too)
    -> web/public/data/sar/<aoi>_<date>.webp, class areas and per-cell fractions on the wide 1 km population grid

Check the dataset against the ASF catalogue: python pipeline/search_nisar.py --validate
Usage: python pipeline/process_gcov.py --aoi manaus [--reingest] [--workers 3]
"""
import argparse
import datetime as dt
import hashlib
import json
import re
from concurrent.futures import ProcessPoolExecutor, as_completed
from pathlib import Path

import numpy as np  # noqa: E402
import rasterio  # noqa: E402
from rasterio.crs import CRS  # noqa: E402
from rasterio.transform import from_origin  # noqa: E402
from rasterio.warp import Resampling, reproject, transform_bounds  # noqa: E402

from common import (DATA_PROCESSED, DB_SCALE, NISAR_DATASET as DATASET, WEB_DATA, WEB_RES_DEG, save_overlay,  # noqa: E402
                    aoi_crop, load_aois, read_scene, sar_grid, write_json)

WATER_DB_RANGE = (-16.0, -10.0)  # threshold is clipped to this range
FLOODED_FOREST_DB = -3.0
EDGE_PX = 25  # pixels (~100 m each) trimmed from the swath edges of each pass
URBAN_FRAC = 0.2  # WorldCover built-up fraction above which bright pixels count as urban
TARGET_COVERAGE = 0.985  # keep reading reserve passes until the mosaic covers this much of the grid
MAX_FILL_PASSES = 4      # at most this many filler passes per date (source ids 2..5)
MIN_POWER = 1e-4  # -40 dB: below the L-band noise floor (~-30 dB) -> not a measurement
STRIP = 2048  # rows per read of the 20 m band (multiple of the 512 chunk and of the 4x block)


# ------------------------------------------------------------------ reading
def open_grid(h5):
    """frequencyB (80 m) when it has HH, else frequencyA (20 m)."""
    for band in ("LSAR", "SSAR"):
        for fq in ("frequencyB", "frequencyA"):
            p = f"/science/{band}/GCOV/grids/{fq}"
            if p in h5 and "HHHH" in h5[p]:
                return h5[p], fq
    raise KeyError("no HH in this GCOV")


def block_mean(a, k):
    h, w = a.shape[0] // k * k, a.shape[1] // k * k
    with np.errstate(invalid="ignore"), __import__("warnings").catch_warnings():
        __import__("warnings").simplefilter("ignore", RuntimeWarning)
        return np.nanmean(a[:h, :w].reshape(h // k, k, w // k, k), axis=(1, 3))


def read_frame(f, h5, bbox):
    """HH/HV linear power inside bbox at ~80 m: (bands, transform, crs, freq) or None if outside."""
    g, fq = open_grid(h5)
    epsg = int(g["projection"].attrs.get("epsg_code", g["projection"][()]))
    x, y = g["xCoordinates"][:], g["yCoordinates"][:]
    W, S, E, N = transform_bounds("EPSG:4326", f"EPSG:{epsg}", *bbox)
    xi, yi = np.where((x >= W) & (x <= E))[0], np.where((y >= S) & (y <= N))[0]
    if xi.size < 8 or yi.size < 8:
        return None
    rows, cols = slice(yi.min(), yi.max() + 1), slice(xi.min(), xi.max() + 1)
    k = 4 if fq == "frequencyA" else 1  # 20 m -> 80 m, same footing as frequencyB
    step = STRIP if k > 1 else rows.stop - rows.start
    bands = {}
    for term in ("HHHH", "HVHV"):
        if term not in g:
            continue
        d, parts = g[term], []
        for r0 in range(rows.start, rows.stop, step):
            r1 = min(rows.stop, r0 + step)
            if f is not None:
                f.prefetch(d, slice(r0, r1), cols)
            a = d[r0:r1, cols].astype("float32")
            if f is not None:
                f.drop_prefetch()
            a[~(a > MIN_POWER)] = np.nan  # fill / below the noise floor (seen as ~-54 dB in some A-only frames)
            parts.append(block_mean(a, k) if k > 1 else a)
        bands[term[:2]] = np.vstack(parts)
    dx, dy = float(x[1] - x[0]), float(y[1] - y[0])
    kept = bands["HH"].shape[0] * k
    if dy < 0:
        top = y[rows.start] - dy / 2
    else:  # y ascending -> flip so row 0 is north
        bands = {b: a[::-1] for b, a in bands.items()}
        top = y[rows.start + kept - 1] + dy / 2
    tr = from_origin(x[cols.start] - dx / 2, top, dx * k, abs(dy) * k)
    return bands, tr, CRS.from_epsg(epsg), fq


def warp(arr, src_tr, src_crs, dst_tr, shape, resampling=Resampling.bilinear):
    out = np.full(shape, np.nan, "float32")
    reproject(arr, out, src_transform=src_tr, src_crs=src_crs, dst_transform=dst_tr,
              dst_crs="EPSG:4326", resampling=resampling, src_nodata=np.nan, dst_nodata=np.nan)
    return out


def to_db(a):
    with np.errstate(divide="ignore", invalid="ignore"):
        d = 10 * np.log10(a)
    d[~np.isfinite(d)] = np.nan
    return d


def read_pass(granules, sess, bbox, web_tr, shape):
    """Frames of one pass -> (hh_db, hv_db, records) on the web grid, swath edges trimmed."""
    import h5py

    from remote_h5 import RemoteFile
    from scipy.ndimage import binary_erosion
    sums = {b: np.zeros(shape, "float64") for b in ("HH", "HV")}
    cnt = {b: np.zeros(shape, "uint8") for b in ("HH", "HV")}
    used = []
    for gr in granules:
        with RemoteFile(gr["url"], sess) as f, h5py.File(f, "r") as h5:
            out = read_frame(f, h5, bbox)
            if out is None:
                continue
            bands, tr, crs, fq = out
            for b, a in bands.items():
                wv = warp(a, tr, crs, web_tr, shape, Resampling.average)
                ok = np.isfinite(wv)
                sums[b][ok] += wv[ok]
                cnt[b][ok] += 1
            used.append({**gr, "freq": fq, "mb_read": round(f.fetched / 1e6, 1)})
    with np.errstate(invalid="ignore", divide="ignore"):
        hh, hv = (to_db((sums[b] / cnt[b]).astype("float32")) for b in ("HH", "HV"))
    edge = ~binary_erosion(np.isfinite(hh), iterations=EDGE_PX)
    hh[edge] = np.nan
    hv[edge | ~np.isfinite(hh)] = np.nan
    return hh, hv, used


def sha256(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def ingest_date(aoi_key: str, date: str, rec: dict) -> dict:
    """Worker: one date -> <dataset>/<aoi>/<date>_100m.tif; returns its manifest record."""
    from search_nisar import session
    aoi = load_aois()[aoi_key]
    bbox, web_tr, shape, _ = sar_grid(aoi)
    sess = session()
    t0 = dt.datetime.now()
    hh, hv, prim = read_pass(rec["primary"], sess, bbox, web_tr, shape)
    src = np.where(np.isfinite(hh), 1, 0).astype("uint8")
    fill_used, fillers = [], []
    # fillers in plan order: each only fills what is still empty; its source id is 2, 3, …
    plan_fillers = rec.get("fillers") or ([{"path": rec.get("secondary_path"), "date": rec.get("secondary_date"),
                                            "granules": rec["secondary"]}] if rec.get("secondary") else [])
    # then reserve passes, only while the REAL coverage is short (footprints overstate it)
    reserve = rec.get("reserve", [])
    queue = [(f, True) for f in plan_fillers] + [(f, False) for f in reserve]
    tried = [g["file"] for g in rec["primary"]]
    for f, planned in queue:
        if not planned and (float((src > 0).mean()) >= TARGET_COVERAGE or len(fillers) >= MAX_FILL_PASSES):
            break
        hh2, hv2, used = read_pass(f["granules"], sess, bbox, web_tr, shape)
        tried += [g["file"] for g in f["granules"]]
        fill = ~np.isfinite(hh) & np.isfinite(hh2)
        if fill.mean() < 0.001:  # adds nothing visible: not part of the mosaic
            continue
        k = 2 + len(fillers)
        hh[fill], hv[fill], src[fill] = hh2[fill], hv2[fill], k
        fill_used += used
        fillers.append({"source": k, "path": f["path"], "date": f["date"], "coverage": round(float(fill.mean()), 3),
                        "reserve": not planned})
    out_dir = DATASET / aoi_key
    out_dir.mkdir(parents=True, exist_ok=True)
    tif = out_dir / f"{date}_100m.tif"
    q = lambda a: np.where(np.isfinite(a), np.round(a * DB_SCALE), -32768).astype("int16")  # noqa: E731
    prof = dict(driver="GTiff", height=shape[0], width=shape[1], count=3, dtype="int16", crs="EPSG:4326",
                transform=web_tr, nodata=-32768, compress="deflate", predictor=2, tiled=True, zlevel=9)
    with rasterio.open(tif, "w", **prof) as dst:
        dst.write(np.stack([q(hh), q(hv), src.astype("int16")]))
        for i, d in enumerate(("HH gamma0 dB x10", "HV gamma0 dB x10", "source 1=main 2+=filler passes"), 1):
            dst.set_band_description(i, d)
        dst.update_tags(date=date, product="NISAR L2 GCOV", scale=str(DB_SCALE))
    mb = sum(g["mb_read"] for g in prim + fill_used)
    print(f"  {aoi_key} {date}: {len(prim)}+{len(fill_used)} frames, {mb:.0f} MB read, "
          f"coverage {float((src > 0).mean()):.0%}, {(dt.datetime.now() - t0).seconds}s", flush=True)
    return {"granules": prim, "filler": fill_used, "fillers": fillers, "tried": sorted(set(tried)),
            "filler_dates": [f["date"] for f in fillers if f["coverage"] > 0], "coverage": round(float((src > 0).mean()), 3),
            "coverage_main": round(float((src == 1).mean()), 3), "mb_read": round(mb, 1), "tif": tif.name,
            "tif_sha256": sha256(tif), "ingested": dt.datetime.now().isoformat(timespec="seconds")}


# ------------------------------------------------------------------ classification
def otsu(values, lo=-30.0, hi=0.0, bins=240) -> float:
    """Threshold that best separates the two modes (water vs land) of the HH dB histogram."""
    h, e = np.histogram(values[np.isfinite(values)], bins=bins, range=(lo, hi))
    c = (e[:-1] + e[1:]) / 2
    w0 = np.cumsum(h)
    w1 = w0[-1] - w0
    m0 = np.cumsum(h * c) / np.maximum(w0, 1)
    m1 = (np.sum(h * c) - np.cumsum(h * c)) / np.maximum(w1, 1)
    return float(c[np.argmax(w0 * w1 * (m0 - m1) ** 2)])


def valley(values, water=(-26.0, -14.0), land=(-10.0, -3.0)) -> float | None:
    """Minimum of the smoothed HH dB histogram between the water peak and the land peak.

    Otsu is biased toward the much larger land class; the valley is the physical split.
    Returns None when there is no clear water peak (then we fall back to Otsu).
    """
    h, e = np.histogram(values[np.isfinite(values)], bins=160, range=(-30, 2))  # 0.2 dB bins
    c = (e[:-1] + e[1:]) / 2
    k = np.exp(-0.5 * (np.arange(-7, 8) / 2.5) ** 2)
    hs = np.convolve(h, k / k.sum(), mode="same")
    wi = np.where((c >= water[0]) & (c <= water[1]))[0]
    li = np.where((c >= land[0]) & (c <= land[1]))[0]
    pw, pl = wi[np.argmax(hs[wi])], li[np.argmax(hs[li])]
    v = pw + np.argmin(hs[pw:pl + 1])
    if hs[pw] < 1.5 * hs[v]:  # water "peak" not distinct from the valley
        return None
    return float(c[v])


def water_threshold(hh, fallback: float | None = None) -> float:
    """Histogram valley; without a clear water peak use `fallback` (the main pass's threshold, for a filler
    pass that sees little open water) or, for the main pass, Otsu (biased toward land, hence the clip)."""
    v = valley(hh)
    if v is None:
        v = fallback if fallback is not None else otsu(hh)
    return float(np.clip(v, *WATER_DB_RANGE))


def overlay_image(hh_db, cls, path: Path):
    """Map overlay over the whole wide grid, so every date is the same rectangle:
    classes in strong colors; dry land as a dim, semi-transparent backscatter texture (context, not the message);
    pixels no pass covered as faint diagonal hatching ("no radar coverage"), not a transparent hole."""
    valid = np.isfinite(hh_db)
    gray = 0.10 + 0.32 * np.clip((np.nan_to_num(hh_db, nan=-25) + 25) / 25, 0, 1)
    rgba = np.dstack([gray, gray * 1.04, gray * 1.1, np.where(valid, 0.55, 0.0)])
    rgba[cls == 1] = (0.12, 0.47, 0.95, 1.0)   # open water
    rgba[cls == 2] = (0.10, 0.85, 0.75, 1.0)   # flooded forest
    rgba[cls == 3] = (0.75, 0.55, 0.95, 1.0)   # urban double-bounce (excluded from flooded forest)
    r, c = np.indices(hh_db.shape)
    stripe = ((r + c) // 6) % 4 == 0
    rgba[~valid] = (0.55, 0.60, 0.68, 0.0)
    rgba[~valid & stripe, 3] = 0.28
    save_overlay(path, rgba)


def classify(aoi_key: str, aoi: dict, man: dict) -> None:
    bbox, web_tr, shape, _ = sar_grid(aoi)
    w, s, e, n = bbox
    with rasterio.open(DATA_PROCESSED / f"pop_{aoi_key}_wide.tif") as p:
        pop, pop_tr, pop_shape = p.read(1), p.transform, p.shape
    inhabited = np.argwhere(pop > 0.5)  # same order as population_<aoi>_wide.json cells
    (WEB_DATA / "sar").mkdir(exist_ok=True)
    lc_path = DATA_PROCESSED / f"landcover_{aoi_key}.npz"
    built = np.nan_to_num(np.load(lc_path)["built"]) if lc_path.exists() else None
    if built is not None and built.shape != shape:
        print("  ! land cover is on another grid (run fetch_landcover.py): no urban mask")
        built = None
    km2 = (WEB_RES_DEG * 111.32) ** 2 * np.cos(np.radians((n + s) / 2))
    for old in [*(WEB_DATA / "sar").glob(f"{aoi_key}_20*.png"), *(WEB_DATA / "sar").glob(f"{aoi_key}_20*.webp")]:
        old.unlink()  # derived overlays of dates no longer in the dataset
    scenes = []
    for date, rec in sorted(man["scenes"].items()):
        tif = DATASET / aoi_key / rec["tif"]
        if rec.get("tif_sha256") and rec["tif_sha256"] != sha256(tif):
            print(f"  ! {tif.name}: sha256 differs from the manifest (file modified?)")
        hh, hv, src = read_scene(aoi_key, date)
        cls = np.zeros(shape, "uint8")
        thr = {}
        for sid in sorted(int(v) for v in np.unique(src) if v > 0):  # each pass: own geometry / wind -> own threshold
            m = src == sid
            if m.sum() < 2000:  # too few pixels for a histogram: use the main pass's threshold
                thr[sid] = thr.get(1, -13.0)
            else:
                thr[sid] = water_threshold(hh[m], thr.get(1) if sid > 1 else None)
            cls[m & (hh < thr[sid])] = 1
        cls[hh > FLOODED_FOREST_DB] = 2
        if built is not None:
            cls[(cls == 2) & (built >= URBAN_FRAC)] = 3
        valid = np.isfinite(hh)
        png = WEB_DATA / "sar" / f"{aoi_key}_{date}.webp"
        overlay_image(hh, cls, png)

        frac = {}
        for k, c in (("water", 1), ("flooded", 2)):
            m = np.where(valid, (cls == c).astype("float32"), np.nan)
            frac[k] = np.nan_to_num(warp(m, web_tr, "EPSG:4326", pop_tr, pop_shape, Resampling.average))
        a_cls, a_valid = aoi_crop(cls, aoi), aoi_crop(valid, aoi)
        cells = [[i, round(float(frac["water"][r, c]), 3), round(float(frac["flooded"][r, c]), 3)]
                 for i, (r, c) in enumerate(inhabited)
                 if frac["water"][r, c] > 0.005 or frac["flooded"][r, c] > 0.005]
        sc = {
            "date": date, "png": f"data/sar/{png.name}", "water_db": round(thr.get(1, np.nan), 1),
            "coverage": rec.get("coverage"),
            "filler_dates": rec.get("filler_dates") or ([rec["filler_date"]] if rec.get("filler_date") else []),
            "water_db_by_source": {str(k): round(v, 1) for k, v in thr.items()},
            "water_km2": round(float((cls == 1).sum() * km2), 1),
            "flooded_forest_km2": round(float((cls == 2).sum() * km2), 1),
            "urban_bright_km2": round(float((cls == 3).sum() * km2), 1),
            "aoi": {"water_km2": round(float((a_cls == 1).sum() * km2), 1),
                    "flooded_forest_km2": round(float((a_cls == 2).sum() * km2), 1),
                    "coverage": round(float(a_valid.mean()), 3)},
            "pop_near_water": round(float(np.nansum(pop * (frac["water"] > 0.05)))),
            "cells": cells,
        }
        opt = WEB_DATA / "optical" / f"{aoi_key}_{date}.webp"  # keep the link made by validate_optical.py
        if opt.exists():
            sc["optical_png"] = f"data/optical/{opt.name}"
        scenes.append(sc)
        print(f"  {date}: threshold {thr} dB | water {sc['water_km2']} km² (AOI {sc['aoi']['water_km2']}) | "
              f"flooded forest {sc['flooded_forest_km2']} km² | coverage {rec.get('coverage', 0):.0%}")
    write_json(WEB_DATA / f"sar_{aoi_key}.json", {
        "bounds": bbox, "aoi_bounds": aoi["bbox"], "cells_grid": f"population_{aoi_key}_wide.json",
        "thresholds_db": {"water_range": WATER_DB_RANGE, "flooded_forest": FLOODED_FOREST_DB},
        "scenes": scenes})


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--aoi", default="manaus")
    ap.add_argument("--reingest", action="store_true", help="re-ingest every date of the plan")
    ap.add_argument("--workers", type=int, default=3, help="dates read in parallel")
    ap.add_argument("--dates", nargs="*", help="re-ingest only these dates (YYYY-MM-DD)")
    a = ap.parse_args()
    aoi = load_aois()[a.aoi]
    man_path = DATASET / a.aoi / "manifest.json"
    man = json.loads(man_path.read_text(encoding="utf-8")) if man_path.exists() else {}
    if "scenes" not in man or any("granules" not in r for r in man["scenes"].values()):
        man = {"scenes": {}}  # older single-frame AOI-only dataset: rebuilt on the wide grid
    plan_path = DATASET / a.aoi / "plan.json"
    if plan_path.exists():
        plan = json.loads(plan_path.read_text(encoding="utf-8"))["dates"]
        files = lambda passes: {g["file"] for f in passes for g in f["granules"]}  # noqa: E731
        core = lambda r: ({g["file"] for g in r["primary"] + r.get("secondary", [])}  # noqa: E731
                          | files(r.get("fillers", [])))
        tried = lambda m: set(m.get("tried") or [g["file"] for g in m.get("granules", []) + m.get("filler", [])])  # noqa: E731

        def stale(d, r):
            """Re-ingest when a planned pass was never tried, or coverage is short and an untried reserve remains."""
            m = man["scenes"][d]
            short = (m.get("coverage") or 0) < TARGET_COVERAGE and len(m.get("fillers", [])) < MAX_FILL_PASSES
            return not core(r) <= tried(m) or (short and not files(r.get("reserve", [])) <= tried(m))

        todo = {d: r for d, r in plan.items()
                if (a.dates and d in a.dates) or (not a.dates and (
                    a.reingest or d not in man["scenes"] or not (DATASET / a.aoi / f"{d}_100m.tif").exists()
                    or stale(d, r)))}
        if todo:
            print(f"remote ingest of {len(todo)} dates ({a.workers} in parallel)…")
            with ProcessPoolExecutor(a.workers) as ex:
                futs = {ex.submit(ingest_date, a.aoi, d, r): d for d, r in sorted(todo.items())}
                for fut in as_completed(futs):
                    d = futs[fut]
                    try:
                        man["scenes"][d] = fut.result()
                    except Exception as err:  # noqa: BLE001
                        print(f"  ! {d}: failed ({err}) — run again to retry only the missing dates")
                        continue
                    man["description"] = (
                        "NISAR L2 GCOV over the AOI ±1°, read remotely (HH/HV only, frequencyB 80 m or A 20 m→80 m), "
                        "linear-power mean on a ~100 m WGS84 grid; int16 dB×10: band 1 HH, 2 HV, 3 source "
                        "(1 main track, 2 filler track ≤12 days away)")
                    man["params"] = {"res_deg": WEB_RES_DEG, "edge_px": EDGE_PX, "bbox": sar_grid(aoi)[0]}
                    man["scenes"] = dict(sorted(man["scenes"].items()))
                    write_json(man_path, man)  # after each date, so an interrupted run keeps what it got
    if not man["scenes"]:
        raise SystemExit("Empty dataset: run `python pipeline/search_nisar.py --plan`, then this script.")
    classify(a.aoi, aoi, man)


if __name__ == "__main__":
    main()
