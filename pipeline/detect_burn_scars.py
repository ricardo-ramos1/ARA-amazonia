"""Forest-disturbance (burn-scar) candidates from NISAR L-band HV change, cross-checked with FIRMS fires.

Physics: HV (cross-pol) comes from volume scattering in the canopy. When forest burns or is cleared,
branches/leaves are lost and HV drops by several dB. Healthy forest HV is very stable (MAD ~0.25 dB
between June and November), so a drop much larger than the scene-wide forest median stands out.

Steps (first vs last processed date of the AOI):
  1. 3x3 multilook in linear power, then ΔHV = HV_last − HV_first (dB)
  2. forest pixels only (WorldCover tree > 80 %, no water, not flooded/dark in HH), seen by the MAIN track on
     both dates (same viewing geometry; filler-track pixels would show geometry, not change)
  3. candidate = ΔHV < median(ΔHV_forest) − DROP_DB; connected patches >= MIN_PX pixels (~0.04 km²)
  4. a patch is "burn scar (FIRMS-confirmed)" if a MODIS fire was detected within CONFIRM_KM between dates
Output: web/public/data/scars_<aoi>.json (patches + stats) and web/public/data/sar/<aoi>_dhv.png (overlay)
"""
import argparse
import json

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402
import numpy as np  # noqa: E402
import pandas as pd  # noqa: E402
from scipy.ndimage import find_objects, label, uniform_filter  # noqa: E402

from common import DATA_PROCESSED, WEB_DATA, WEB_RES_DEG, load_aois, read_scene, write_json  # noqa: E402

DROP_DB = 3.0
MIN_PX = 5
CONFIRM_KM = 1.5


def multilook(db, k=3):
    lin = 10 ** (db / 10)
    m = np.isfinite(lin)
    v = uniform_filter(np.nan_to_num(lin), k)
    w = uniform_filter(m.astype(float), k)
    with np.errstate(divide="ignore", invalid="ignore"):
        return 10 * np.log10(np.where(w > 0.5, v / np.maximum(w, 1e-9), np.nan))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--aoi", default="manaus")
    a = ap.parse_args()
    sar = json.loads((WEB_DATA / f"sar_{a.aoi}.json").read_text(encoding="utf-8"))
    w, s, e, n = sar["bounds"]  # wide radar grid (AOI ± ~1°)
    d0, d1 = sar["scenes"][0]["date"], sar["scenes"][-1]["date"]
    (hh0, hv0, src0), (hh1, hv1, src1) = (read_scene(a.aoi, d) for d in (d0, d1))
    lc = np.load(DATA_PROCESSED / f"landcover_{a.aoi}.npz")
    tree, water = np.nan_to_num(lc["tree"]), np.nan_to_num(lc["water"])
    if tree.shape != hh0.shape:
        raise SystemExit("landcover em outra grade: rode pipeline/fetch_landcover.py")

    dhv = multilook(hv1) - multilook(hv0)
    forest = ((tree > 0.8) & (water < 0.05) & np.isfinite(dhv) & (hh0 > -10) & (hh1 > -10)
              & (src0 == 1) & (src1 == 1))
    med = float(np.nanmedian(dhv[forest]))
    mad = float(np.nanmedian(np.abs(dhv[forest] - med)))
    cand = forest & (dhv < med - DROP_DB)
    lab, _ = label(cand)

    fires = pd.DataFrame()
    fp = DATA_PROCESSED / "fires_points.csv"
    if fp.exists():
        fires = pd.read_csv(fp, parse_dates=["acq_date"])
        fires = fires[fires.acq_date.between(d0, d1) & fires.longitude.between(w, e) & fires.latitude.between(s, n)]
    has_fires_for_period = len(fires) > 0 or (fp.exists() and pd.read_csv(fp, usecols=["acq_date"]).acq_date.max() >= d1)

    patches = []
    for i, sl in enumerate(find_objects(lab), start=1):
        m = lab[sl] == i
        if m.sum() < MIN_PX:
            continue
        rows, cols = np.nonzero(m)
        r0, c0 = rows.mean() + sl[0].start, cols.mean() + sl[1].start
        lon, lat = w + (c0 + 0.5) * WEB_RES_DEG, n - (r0 + 0.5) * WEB_RES_DEG
        n_fire = 0
        if len(fires):
            dkm = np.hypot((fires.longitude - lon) * 111.32 * np.cos(np.radians(lat)), (fires.latitude - lat) * 111.32)
            n_fire = int((dkm <= CONFIRM_KM + np.sqrt(m.sum()) * 0.1).sum())
        patches.append({"lon": round(lon, 4), "lat": round(lat, 4), "km2": round(m.sum() * (WEB_RES_DEG * 111.32) ** 2, 2),
                        "dhv_db": round(float(np.nanmean(dhv[sl][m]) - med), 1), "fires": n_fire,
                        "label": ("queimada (confirmada FIRMS)" if n_fire else
                                  "distúrbio sem foco (desmatamento? fogo de sub-bosque?)" if has_fires_for_period
                                  else "distúrbio (sem dados FIRMS do período)")})
    patches.sort(key=lambda p: -p["km2"])

    # overlay: red = strong HV drop in forest, transparent elsewhere
    rgba = np.zeros(dhv.shape + (4,))
    rel = np.clip((med - dhv) / 6, 0, 1)
    show = forest & (dhv < med - 1.5)
    rgba[show] = np.column_stack([np.ones(show.sum()), 0.35 * (1 - rel[show]), 0.1 * np.ones(show.sum()),
                                  0.35 + 0.65 * rel[show]])
    plt.imsave(WEB_DATA / "sar" / f"{a.aoi}_dhv.png", rgba)

    conf = [p for p in patches if p["fires"]]

    # Control: how often does a RANDOM forest location get a fire within the same radius by chance?
    # If scars match fires far more often than random forest does, the radar is really seeing burns.
    control = None
    if len(fires):
        rr, cc = np.nonzero(forest)
        pick = np.random.default_rng(0).choice(len(rr), min(2000, len(rr)), replace=False)
        lat_r, lon_r = n - (rr[pick] + 0.5) * WEB_RES_DEG, w + (cc[pick] + 0.5) * WEB_RES_DEG
        fx, fy = fires.longitude.to_numpy(), fires.latitude.to_numpy()
        radius = CONFIRM_KM + np.sqrt(MIN_PX) * 0.1
        hits = [(np.hypot((fx - x) * 111.32 * np.cos(np.radians(y)), (fy - y) * 111.32) <= radius).any()
                for x, y in zip(lon_r, lat_r)]
        control = round(float(np.mean(hits)), 3)
    out = {"from": d0, "to": d1, "forest_dhv_median_db": round(med, 2), "forest_dhv_mad_db": round(mad, 2),
           "drop_db": DROP_DB, "png": f"data/sar/{a.aoi}_dhv.png", "bounds": [w, s, e, n],
           "n_patches": len(patches), "km2": round(sum(p["km2"] for p in patches), 2),
           "n_fire_confirmed": len(conf), "km2_fire_confirmed": round(sum(p["km2"] for p in conf), 2),
           "fires_in_period": int(len(fires)), "fires_data_covers_period": bool(has_fires_for_period),
           "confirm_rate": round(len(conf) / len(patches), 3) if patches else None,
           "control_rate_random_forest": control,
           "patches": patches}
    write_json(WEB_DATA / f"scars_{a.aoi}.json", out)
    print(f"{a.aoi} {d0}→{d1}: ΔHV floresta mediana {med:+.2f} dB (MAD {mad:.2f}); "
          f"{len(patches)} manchas, {out['km2']} km²; focos FIRMS no período: {len(fires)}; "
          f"confirmadas por fogo: {len(conf)} ({out['km2_fire_confirmed']} km²)"
          + (f" | controle aleatório: {control:.0%}" if control is not None else ""))


if __name__ == "__main__":
    main()
