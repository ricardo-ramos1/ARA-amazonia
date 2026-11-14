"""Active-fire detections (NASA FIRMS, MODIS Terra+Aqua) for the Amazon study regions.

Why MODIS and not VIIRS: MODIS has a CONSISTENT record since 2000 (VIIRS only since 2012 and detects
~10x more small fires), so a 2000→today trend/model must not mix sensors. (Harmonising the two is
literally another Space Apps 2026 challenge.)

Sources
  2000–last full year : yearly country archives  firms.modaps.eosdis.nasa.gov/data/country/modis/<yr>/  (no key)
  current year         : FIRMS area API (needs a free MAP_KEY -> env var FIRMS_MAP_KEY)
  Amazonas state shape : IBGE malhas API

Filters: type == 0 (presumed vegetation fire), confidence >= MIN_CONF.

Outputs
  data/processed/fires_points.csv      every kept detection in Amazonas or near an AOI
  web/public/data/fires.json           monthly counts per region (amazonas, manaus, tefe)
  web/public/data/fires_points_<aoi>.json   detections around each AOI (map layer)

Usage: python pipeline/fetch_fires.py            (download what is missing, then aggregate)
       python pipeline/fetch_fires.py --download-only
"""
import argparse
import datetime as dt
import io
import json
import os

import numpy as np
import pandas as pd
from matplotlib.path import Path as MplPath

from common import DATA_PROCESSED, DATA_RAW, WEB_DATA, http_get, load_aois, write_json

ARCHIVE = "https://firms.modaps.eosdis.nasa.gov/data/country/modis/{y}/modis_{y}_Brazil.csv"
API = "https://firms.modaps.eosdis.nasa.gov/api/area/csv/{key}/{src}/{bbox}/{days}/{date}"
IBGE_AM = "https://servicodados.ibge.gov.br/api/v3/malhas/estados/13?formato=application/vnd.geo%2Bjson&qualidade=intermediaria"
FIRST_YEAR = 2000
MIN_CONF = 30
AOI_BUFFER_DEG = 1.0      # detections this far around an AOI count as "local" (smoke reaches the city)
COLS = ["latitude", "longitude", "acq_date", "confidence", "frp", "type", "satellite"]
RAW = DATA_RAW / "firms"


def user_env(name):
    if os.getenv(name):
        return os.environ[name]
    try:
        import winreg
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, "Environment") as k:
            return winreg.QueryValueEx(k, name)[0]
    except (ImportError, OSError):
        return None


def download_archives():
    RAW.mkdir(parents=True, exist_ok=True)
    for y in range(FIRST_YEAR, dt.date.today().year):
        out = RAW / f"modis_{y}_Brazil.csv"
        if out.exists() and out.stat().st_size > 1000:
            continue
        print(f"  FIRMS MODIS {y}…")
        r = http_get(ARCHIVE.format(y=y), stream=True, timeout=600)
        tmp = out.with_suffix(".part")
        with open(tmp, "wb") as f:
            for chunk in r.iter_content(1 << 20):
                f.write(chunk)
        tmp.replace(out)


def amazonas_polygon() -> MplPath:
    cache = DATA_PROCESSED / "amazonas.geojson"
    if not cache.exists():
        cache.write_text(http_get(IBGE_AM).text, encoding="utf-8")
    gj = json.loads(cache.read_text(encoding="utf-8"))
    geom = gj["features"][0]["geometry"]
    rings = geom["coordinates"] if geom["type"] == "Polygon" else [p[0] for p in geom["coordinates"]]
    verts, codes = [], []
    for ring in (rings if geom["type"] == "MultiPolygon" else [rings[0]]):
        verts += ring
        codes += [MplPath.MOVETO] + [MplPath.LINETO] * (len(ring) - 2) + [MplPath.CLOSEPOLY]
    return MplPath(np.array(verts), codes)


def current_year(bbox) -> pd.DataFrame:
    """Current-year detections via the FIRMS API (standard product where available, else NRT)."""
    key = user_env("FIRMS_MAP_KEY")
    if not key:
        print("  ! FIRMS_MAP_KEY not set: no current-year fires")
        return pd.DataFrame(columns=COLS)
    cache = RAW / f"api_{dt.date.today().year}.csv"
    start = dt.date(dt.date.today().year, 1, 1)
    have = pd.read_csv(cache) if cache.exists() else pd.DataFrame(columns=COLS)
    if len(have):
        start = pd.to_datetime(have["acq_date"]).max().date() + dt.timedelta(days=1)
    frames, day = [have], start
    b = ",".join(str(v) for v in bbox)
    while day <= dt.date.today():
        n = min(5, (dt.date.today() - day).days + 1)
        got = None
        for src in ("MODIS_SP", "MODIS_NRT"):
            txt = http_get(API.format(key=key.strip(), src=src, bbox=b, days=n, date=day.isoformat()), timeout=120).text
            if txt.startswith("latitude"):
                df = pd.read_csv(io.StringIO(txt))
                if len(df):
                    got = df
                    break
            elif "Invalid" in txt or "error" in txt.lower():
                raise SystemExit(f"FIRMS API: {txt[:200]}")
        if got is not None:
            got["type"] = got.get("type", 0)
            frames.append(got[[c for c in COLS if c in got]])
        print(f"  API {day} (+{n}d): {0 if got is None else len(got)} fires")
        day += dt.timedelta(days=n)
    out = pd.concat(frames, ignore_index=True).drop_duplicates()
    out.to_csv(cache, index=False)
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--download-only", action="store_true")
    a = ap.parse_args()
    download_archives()
    if a.download_only:
        return

    poly = amazonas_polygon()
    am_bbox = [*poly.vertices.min(axis=0), *poly.vertices.max(axis=0)]
    aois = load_aois()
    boxes = {k: [v["bbox"][0] - AOI_BUFFER_DEG, v["bbox"][1] - AOI_BUFFER_DEG,
                 v["bbox"][2] + AOI_BUFFER_DEG, v["bbox"][3] + AOI_BUFFER_DEG] for k, v in aois.items()}

    def keep(df):
        df = df[(df["type"] == 0) & (pd.to_numeric(df["confidence"], errors="coerce") >= MIN_CONF)]
        w, s, e, n = am_bbox
        return df[(df.longitude >= w - 2) & (df.longitude <= e + 2) & (df.latitude >= s - 2) & (df.latitude <= n + 2)]

    frames = []
    for y in range(FIRST_YEAR, dt.date.today().year):
        f = RAW / f"modis_{y}_Brazil.csv"
        if f.exists():
            frames.append(keep(pd.read_csv(f, usecols=COLS)))
    frames.append(keep(current_year([round(v, 2) for v in am_bbox])))
    pts = pd.concat(frames, ignore_index=True)
    pts["acq_date"] = pd.to_datetime(pts["acq_date"])
    pts["in_amazonas"] = poly.contains_points(pts[["longitude", "latitude"]].to_numpy())
    for k, (w, s, e, n) in boxes.items():
        pts[f"near_{k}"] = pts.longitude.between(w, e) & pts.latitude.between(s, n)
    pts = pts[pts.in_amazonas | pts[[f"near_{k}" for k in boxes]].any(axis=1)]
    pts.to_csv(DATA_PROCESSED / "fires_points.csv", index=False)

    last = pts.acq_date.max()
    months = pd.date_range(f"{FIRST_YEAR}-01-01", last, freq="MS")
    out = {"source": "NASA FIRMS MODIS (Terra+Aqua), type=0, confidence>=%d" % MIN_CONF,
           "last_date": f"{last:%Y-%m-%d}", "regions": {}}
    for region, mask in [("amazonas", pts.in_amazonas)] + [(k, pts[f"near_{k}"]) for k in boxes]:
        m = pts[mask].set_index("acq_date").resample("MS").size().reindex(months, fill_value=0)
        if last.day < 28:  # current month incomplete
            m = m.iloc[:-1]
        out["regions"][region] = {"dates": m.index.strftime("%Y-%m").tolist(), "count": m.astype(int).tolist()}
        pd.DataFrame({"date": m.index, "fires": m.values}).to_csv(DATA_PROCESSED / f"fires_{region}.csv", index=False)
        yearly = pts[mask].groupby(pts[mask].acq_date.dt.year).size()
        print(f"  {region}: {mask.sum():,} fires | top years: {yearly.sort_values().tail(4).to_dict()}")
    write_json(WEB_DATA / "fires.json", out)

    for k in boxes:  # points near each AOI, 2015+ (for the map layer)
        p = pts[pts[f"near_{k}"] & (pts.acq_date >= "2015-01-01")]
        write_json(WEB_DATA / f"fires_points_{k}.json", {
            "cols": ["lon", "lat", "date", "frp"],
            "rows": [[round(r.longitude, 4), round(r.latitude, 4), f"{r.acq_date:%Y-%m-%d}", round(float(r.frp), 1)]
                     for r in p.itertuples()]})


if __name__ == "__main__":
    main()
