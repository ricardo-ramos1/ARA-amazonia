"""Validate the NISAR open-water mask against Sentinel-2 optical imagery (NDWI).

For every processed NISAR scene (web/public/data/sar_<aoi>.json), over the whole radar grid (AOI ± ~1°):
  1. find Sentinel-2 L2A images within ±MAX_DAYS on Earth Search (AWS, public, no login)
  2. mosaic the closest / least-cloudy ones onto the same ~100 m grid as the SAR overlay,
     reading COG overviews (~80 m) so only a few MB per band are fetched
  3. mask clouds/shadows with the Scene Classification Layer (SCL)
  4. optical water = MNDWI (green − SWIR1)/(green + SWIR1) > NDWI_T   (Xu 2006). MNDWI, not NDWI:
     sediment-laden "white water" (Solimões) is bright in NIR, so NDWI misses it; SWIR is absorbed by any water.
  5. compare with SAR water (HH < per-pass threshold): precision, recall, F1, IoU, accuracy — for the whole
     area and for the original AOI window (comparable with earlier runs)
Also writes a true-colour PNG so the app can show radar vs optical side by side.

Output: web/public/data/validation_<aoi>.json and web/public/data/optical/<aoi>_<date>.webp
Usage:  python pipeline/validate_optical.py --aoi manaus [--workers 3]
"""
import argparse
import datetime as dt
import json
from concurrent.futures import ProcessPoolExecutor, ThreadPoolExecutor, as_completed

import numpy as np  # noqa: E402
import rasterio  # noqa: E402
import requests  # noqa: E402
from rasterio.warp import Resampling, reproject, transform_bounds  # noqa: E402
from rasterio.windows import from_bounds  # noqa: E402

from common import (DATA_PROCESSED, NISAR_DATASET, WEB_DATA, WEB_RES_DEG, aoi_crop, load_aois,  # noqa: E402
                    read_scene, sar_grid, save_overlay, write_json)

STAC = "https://earth-search.aws.element84.com/v1/search"
MAX_DAYS = 7
MAX_CLOUD = 40
NDWI_T = 0.0
REF_MIN_PERM_WATER = 0.7    # optical must see >=70% of WorldCover permanent water as water to be trusted
MAX_ITEMS = 40              # the wide area spans ~12 Sentinel-2 tiles
CACHE = NISAR_DATASET.parent / "cache"  # MNDWI mosaics (outside OneDrive)
DECIMATE = 8                # read 10 m bands at ~80 m via COG overviews
CLOUD_SCL = {0, 1, 3, 8, 9, 10}  # no-data, saturated, shadow, cloud medium/high, cirrus


def search(bbox, date: str):
    d = dt.date.fromisoformat(date)
    rng = f"{d - dt.timedelta(days=MAX_DAYS)}T00:00:00Z/{d + dt.timedelta(days=MAX_DAYS)}T23:59:59Z"
    r = requests.post(STAC, json={"collections": ["sentinel-2-l2a"], "bbox": bbox, "datetime": rng, "limit": 200,
                                  "query": {"eo:cloud_cover": {"lt": MAX_CLOUD}}}, timeout=90)
    r.raise_for_status()
    feats = r.json()["features"]
    for f in feats:
        f["_days"] = abs((dt.date.fromisoformat(f["properties"]["datetime"][:10]) - d).days)
    return sorted(feats, key=lambda f: (f["_days"], f["properties"]["eo:cloud_cover"]))


def read_to_grid(href, bbox, dst_tr, shape, band=1, decimate=DECIMATE, resampling=Resampling.average):
    out = np.full(shape, np.nan, "float32")
    with rasterio.open("/vsicurl/" + href) as src:
        b = transform_bounds("EPSG:4326", src.crs, *bbox)
        b = (max(b[0], src.bounds.left), max(b[1], src.bounds.bottom), min(b[2], src.bounds.right), min(b[3], src.bounds.top))
        if b[0] >= b[2] or b[1] >= b[3]:
            return out
        win = from_bounds(*b, transform=src.transform).round_offsets().round_lengths()
        oh, ow = max(1, int(win.height // decimate)), max(1, int(win.width // decimate))
        arr = src.read(band, window=win, out_shape=(oh, ow), resampling=resampling).astype("float32")
        tr = src.window_transform(win) * rasterio.Affine.scale(win.width / ow, win.height / oh)
        nod = src.nodata
        if nod is not None:
            arr[arr == nod] = np.nan
        reproject(arr, out, src_transform=tr, src_crs=src.crs, dst_transform=dst_tr, dst_crs="EPSG:4326",
                  resampling=resampling, src_nodata=np.nan, dst_nodata=np.nan)
    return out


def scale_of(asset, props):
    rb = (asset.get("raster:bands") or [{}])[0]
    # Earth Search already removed the -1000 BOA offset when this flag is set; applying it again breaks NDWI
    offset = 0.0 if props.get("earthsearch:boa_offset_applied") else rb.get("offset", 0.0)
    return rb.get("scale", 1.0), offset


def mosaic(items, bbox, dst_tr, shape, need):
    ndwi = np.full(shape, np.nan, "float32")
    rgb = np.full(shape + (3,), np.nan, "float32")
    used = []
    pool = ThreadPoolExecutor(6)  # the bands of one image are fetched in parallel (GDAL releases the GIL)
    for it in items[:MAX_ITEMS]:
        a = it["assets"]
        # skip images that cannot add anything: no still-missing pixel inside their footprint bbox
        ib = it.get("bbox")
        if ib:
            c0, c1 = int((ib[0] - bbox[0]) / WEB_RES_DEG), int((ib[2] - bbox[0]) / WEB_RES_DEG) + 1
            r0, r1 = int((bbox[3] - ib[3]) / WEB_RES_DEG), int((bbox[3] - ib[1]) / WEB_RES_DEG) + 1
            sub = (slice(max(r0, 0), max(r1, 0)), slice(max(c0, 0), max(c1, 0)))
            if not (need[sub] & np.isnan(ndwi[sub])).any():
                continue
        g, n, scl = pool.map(lambda job: read_to_grid(*job[0], **job[1]), [
            ((a["green"]["href"], bbox, dst_tr, shape), {}),
            ((a["swir16"]["href"], bbox, dst_tr, shape), {"decimate": DECIMATE // 2}),  # 20 m band
            ((a["scl"]["href"], bbox, dst_tr, shape), {"decimate": DECIMATE // 2, "resampling": Resampling.nearest})])
        (gs, go), (ns, no) = scale_of(a["green"], it["properties"]), scale_of(a["swir16"], it["properties"])
        g, n = g * gs + go, n * ns + no
        clear = np.isfinite(g) & np.isfinite(n) & np.isfinite(scl) & ~np.isin(scl, list(CLOUD_SCL))
        fill = clear & np.isnan(ndwi)
        if not fill.any():
            continue
        with np.errstate(divide="ignore", invalid="ignore"):
            ndwi[fill] = ((g - n) / (g + n))[fill]
        vis = np.dstack(list(pool.map(lambda b: read_to_grid(a["visual"]["href"], bbox, dst_tr, shape, band=b),
                                      (1, 2, 3))))
        rgb[fill] = vis[fill]
        used.append(f'{it["properties"]["datetime"][:10]} {it["properties"].get("s2:mgrs_tile", "")}')
        cov = np.isfinite(ndwi[need]).mean()
        print(f"    + {used[-1]} (clouds {it['properties']['eo:cloud_cover']:.0f}%) -> coverage {cov:.0%}")
        if cov > 0.97:
            break
    pool.shutdown()
    return ndwi, rgb, used


def scores(sar, opt):
    tp, fp = np.sum(sar & opt), np.sum(sar & ~opt)
    fn, tn = np.sum(~sar & opt), np.sum(~sar & ~opt)
    p, r = tp / max(tp + fp, 1), tp / max(tp + fn, 1)
    return {"precision": round(float(p), 3), "recall": round(float(r), 3),
            "f1": round(float(2 * p * r / max(p + r, 1e-9)), 3), "iou": round(float(tp / max(tp + fp + fn, 1)), 3),
            "accuracy": round(float((tp + tn) / max(tp + fp + fn + tn, 1)), 3), "n_pixels": int(tp + fp + fn + tn)}


def sar_water(aoi_key, sc):
    """Radar water mask with each pass's own threshold (main track, then filler passes)."""
    hh, _, src = read_scene(aoi_key, sc["date"])
    thr = {int(k): v for k, v in (sc.get("water_db_by_source") or {"1": sc["water_db"]}).items()}
    water = np.zeros(hh.shape, bool)
    for sid, t in thr.items():
        water |= (src == sid) & (hh < t)
    return water, np.isfinite(hh)


def validate_date(aoi_key: str, sc: dict, refresh: bool, cached_items):
    """Worker: one NISAR date -> metrics (whole area + AOI window) and the optical PNG."""
    aoi = load_aois()[aoi_key]
    bbox, dst_tr, shape, _ = sar_grid(aoi)
    water, need = sar_water(aoi_key, sc)
    cache = CACHE / f"ndwi_{aoi_key}_{sc['date']}.npy"
    png = WEB_DATA / "optical" / f"{aoi_key}_{sc['date']}.webp"
    ndwi = np.load(cache).astype("float32") if cache.exists() and png.exists() and not refresh else None
    if ndwi is not None and ndwi.shape == shape and cached_items is not None:
        used = cached_items
    else:
        items = search(bbox, sc["date"])
        if not items:
            return None
        ndwi, rgb, used = mosaic(items, bbox, dst_tr, shape, need)
        CACHE.mkdir(parents=True, exist_ok=True)
        np.save(cache, ndwi.astype("float16"))
        img = np.clip(rgb / 255.0 * 1.6, 0, 1)  # brighten: TCI is dark over forest
        alpha = np.isfinite(rgb).all(axis=2).astype("float32")
        save_overlay(png, np.dstack([np.nan_to_num(img), alpha]))
    both = need & np.isfinite(ndwi)
    opt = ndwi > NDWI_T
    m = scores(water[both], opt[both])
    ab = aoi_crop(both, aoi)
    # original AOI window: only when the optical image actually sees enough of it (else no score, not "0")
    m["aoi"] = ({k: v for k, v in scores(aoi_crop(water, aoi)[ab], aoi_crop(opt, aoi)[ab]).items() if k != "n_pixels"}
                if ab.sum() >= 5000 else None)
    # Reference quality: does the optical index even see the permanent river as water?
    # Smoke/haze (fire season) and thin cloud make MNDWI fail over water -> the reference, not the radar, is wrong.
    ref_ok = None
    lc_path = DATA_PROCESSED / f"landcover_{aoi_key}.npz"
    if lc_path.exists():
        perm = np.nan_to_num(np.load(lc_path)["water"]) > 0.8
        if perm.shape == shape:
            pw = perm & np.isfinite(ndwi)
            ref_ok = float((ndwi[pw] > NDWI_T).mean()) if pw.any() else None
            # same test inside the original AOI: if its river is clouded the AOI score is undefined, not 0
            apw = aoi_crop(pw, aoi)
            if m["aoi"] is not None and (apw.sum() < 500 or (aoi_crop(ndwi, aoi)[apw] > NDWI_T).mean() < REF_MIN_PERM_WATER):
                m["aoi"] = None
    m.update(sar_date=sc["date"], s2_images=used, coverage=round(float(both.sum() / max(need.sum(), 1)), 3),
             sar_water_km2=sc["water_km2"],
             ref_perm_water_detected=None if ref_ok is None else round(ref_ok, 3),
             ref_reliable=None if ref_ok is None else bool(ref_ok >= REF_MIN_PERM_WATER))
    return m


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--aoi", default="manaus")
    ap.add_argument("--date", help="a single NISAR date (YYYY-MM-DD)")
    ap.add_argument("--refresh", action="store_true", help="download the Sentinel-2 mosaic again")
    ap.add_argument("--workers", type=int, default=3)
    a = ap.parse_args()
    sar_json = WEB_DATA / f"sar_{a.aoi}.json"
    sar = json.loads(sar_json.read_text(encoding="utf-8"))
    (WEB_DATA / "optical").mkdir(exist_ok=True)
    val_path = WEB_DATA / f"validation_{a.aoi}.json"
    old = {s["sar_date"]: s for s in json.loads(val_path.read_text(encoding="utf-8"))["scenes"]} if val_path.exists() else {}
    dates = {sc["date"] for sc in sar["scenes"]}
    old = {d: v for d, v in old.items() if d in dates}  # drop dates no longer in the dataset
    todo = [sc for sc in sar["scenes"] if not a.date or sc["date"] == a.date]
    with ProcessPoolExecutor(a.workers) as ex:
        # results made on the wide grid (they have the "aoi" block) can reuse their cached mosaic
        futs = {ex.submit(validate_date, a.aoi, sc, a.refresh,
                          old[sc["date"]]["s2_images"] if "aoi" in old.get(sc["date"], {}) else None): sc["date"]
                for sc in todo}
        for fut in as_completed(futs):
            d = futs[fut]
            try:
                m = fut.result()
            except Exception as err:  # noqa: BLE001
                print(f"  ! {d}: failed ({err})")
                continue
            if m is None:
                print(f"  {d}: no Sentinel-2 image nearby")
                continue
            old[d] = m
            flag = "" if m["ref_reliable"] in (True, None) else "  ⚠ optical reference unreliable"
            print(f"  {d}: F1={m['f1']} (AOI {(m['aoi'] or {}).get('f1', '—')}) IoU={m['iou']} coverage {m['coverage']:.0%}, "
                  f"optical sees {m['ref_perm_water_detected'] or 0:.0%} of permanent water{flag}", flush=True)
    for sc in sar["scenes"]:
        png = WEB_DATA / "optical" / f"{a.aoi}_{sc['date']}.webp"
        if png.exists():
            sc["optical_png"] = f"data/optical/{png.name}"

    scenes = sorted(old.values(), key=lambda s: s["sar_date"])
    good = [s for s in scenes if s.get("ref_reliable")]
    summary = {k: round(float(np.mean([s[k] for s in good])), 3) for k in ("f1", "iou", "precision", "recall")} if good else {}
    aoi_good = [s["aoi"]["f1"] for s in good if s.get("aoi")]
    if aoi_good:
        summary["aoi_f1"] = round(float(np.mean(aoi_good)), 3)
    summary["n_reliable"], summary["n_total"] = len(good), len(scenes)
    write_json(val_path, {"index": "MNDWI", "threshold": NDWI_T, "max_days": MAX_DAYS,
                          "ref_min_perm_water": REF_MIN_PERM_WATER, "summary": summary, "scenes": scenes})
    write_json(sar_json, sar)
    print(f"  summary (reliable references {summary['n_reliable']}/{summary['n_total']}): "
          + ", ".join(f"{k}={v}" for k, v in summary.items() if not k.startswith("n_")))


if __name__ == "__main__":
    main()
