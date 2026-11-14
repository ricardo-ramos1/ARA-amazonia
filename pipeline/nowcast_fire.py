"""'Fogo agora': active fires of the last 48 h + 72 h weather forecast -> fire danger, spread/smoke direction
and people downwind. A situational nowcast for TODAY, not a validated model.

Inputs (all live, re-run to refresh):
  * NASA FIRMS NRT, last 2 days: VIIRS (NOAA-20, NOAA-21, S-NPP, 375 m) + MODIS (1 km)  [needs FIRMS_MAP_KEY]
    -> for "now" VIIRS is preferred: it sees ~10x more small fires. (The 2000→today series uses MODIS only.)
  * Open-Meteo forecast API (ECMWF/GFS blend, hourly, no key): temperature, humidity, wind, gusts, rain, VPD
  * population_<aoi>_wide.json (WorldPop 2020, ~1 km)

Method
  1. clusters: fire pixels joined on a ~2 km grid (connected components)
  2. weather for every cluster and for a 6x6 grid over the region (one API call)
  3. fire danger per cluster (next 24 h):
       Angström index I = RH/20 + (27 − T)/10 at the driest hour   (<2.0 very favourable … >4.0 unlikely)
       + forecast rain (24 h) and gusts
  4. spread / smoke direction = where the wind blows TO (vector-mean wind of the next 12 h)
     near-field smoke zone = sector of SMOKE_KM length, ±SMOKE_HALF_ANGLE° around that direction
  5. people downwind = population cells inside any smoke zone of an active (moderate+) cluster

Output: web/public/data/nowcast_<aoi>.json
Usage:  python pipeline/nowcast_fire.py --aoi manaus
"""
import argparse
import datetime as dt
import io
import json

import numpy as np
import pandas as pd
import requests
from scipy.ndimage import label

from common import WEB_DATA, http_get, load_aois, write_json
from fetch_fires import user_env

FIRMS = "https://firms.modaps.eosdis.nasa.gov/api/area/csv/{key}/{src}/{bbox}/{days}"
SOURCES = ["VIIRS_NOAA20_NRT", "VIIRS_NOAA21_NRT", "VIIRS_SNPP_NRT", "MODIS_NRT"]
METEO = "https://api.open-meteo.com/v1/forecast"
HOURLY = ["temperature_2m", "relative_humidity_2m", "wind_speed_10m", "wind_direction_10m",
          "wind_gusts_10m", "precipitation", "vapour_pressure_deficit"]
BUFFER_DEG = 1.0
CLUSTER_DEG = 0.02          # ~2 km grid for clustering
GRID_N = 6                  # 6x6 wind arrows
SMOKE_KM = 40
SMOKE_HALF_ANGLE = 25
CALM_MS = 0.5               # below this mean wind there is no meaningful direction
CALM_KM = 10                # ...and smoke is drawn as a circle around the fire
TZ = "America/Manaus"


def fetch_fires(bbox, key) -> pd.DataFrame:
    frames = []
    b = ",".join(f"{v:.3f}" for v in bbox)
    for src in SOURCES:
        txt = http_get(FIRMS.format(key=key, src=src, bbox=b, days=2), timeout=120).text
        if not txt.startswith("latitude"):
            print(f"  ! {src}: {txt[:80]}")
            continue
        df = pd.read_csv(io.StringIO(txt))
        if df.empty:
            continue
        conf = df["confidence"].astype(str)
        keep = conf.isin(["n", "h", "nominal", "high"]) if src.startswith("VIIRS") \
            else pd.to_numeric(conf, errors="coerce") >= 30
        df = df[keep]
        frames.append(pd.DataFrame({"lat": df.latitude, "lon": df.longitude, "frp": df.frp,
                                    "date": df.acq_date, "time": df.acq_time.astype(str).str.zfill(4),
                                    "sensor": src.replace("_NRT", "")}))
        print(f"  {src}: {len(df)} focos")
    return pd.concat(frames, ignore_index=True) if frames else pd.DataFrame(columns=["lat", "lon", "frp", "date", "time", "sensor"])


def cluster(f: pd.DataFrame, bbox) -> list[dict]:
    if f.empty:
        return []
    w, s, e, n = bbox
    nx, ny = int((e - w) / CLUSTER_DEG) + 1, int((n - s) / CLUSTER_DEG) + 1
    ix = ((f.lon - w) / CLUSTER_DEG).astype(int).clip(0, nx - 1)
    iy = ((f.lat - s) / CLUSTER_DEG).astype(int).clip(0, ny - 1)
    grid = np.zeros((ny, nx), bool)
    grid[iy, ix] = True
    lab, _ = label(grid, structure=np.ones((3, 3)))
    f = f.assign(c=lab[iy, ix])
    out = []
    for c, g in f.groupby("c"):
        last = (g.date + " " + g.time.str[:2] + ":" + g.time.str[2:]).max()
        out.append({"id": int(c), "lat": round(float(np.average(g.lat, weights=g.frp + 1)), 4),
                    "lon": round(float(np.average(g.lon, weights=g.frp + 1)), 4), "n": int(len(g)),
                    "frp": round(float(g.frp.sum()), 1), "last_utc": last})
    return sorted(out, key=lambda d: -d["frp"])


def weather(points: list[tuple[float, float]]) -> list[dict]:
    r = requests.post(METEO, data={"latitude": ",".join(f"{p[0]:.4f}" for p in points),
                                   "longitude": ",".join(f"{p[1]:.4f}" for p in points),
                                   "hourly": ",".join(HOURLY), "forecast_days": 4, "timezone": TZ,
                                   "wind_speed_unit": "ms"}, timeout=120)
    r.raise_for_status()
    j = r.json()
    return j if isinstance(j, list) else [j]


def frame(w: dict, start: pd.Timestamp, hours=72) -> pd.DataFrame:
    h = pd.DataFrame(w["hourly"])
    h["time"] = pd.to_datetime(h["time"])
    return h[(h.time >= start) & (h.time < start + pd.Timedelta(hours=hours))].reset_index(drop=True)


def wind_vector_mean(speed, direction_from):
    """Mean wind as (speed, direction TO which it blows)."""
    rad = np.radians(direction_from)
    u, v = -(speed * np.sin(rad)).mean(), -(speed * np.cos(rad)).mean()  # components the air moves TO
    return float(np.hypot(u, v)), float((np.degrees(np.arctan2(u, v)) + 360) % 360)


def angstrom(t, rh):
    return rh / 20 + (27 - t) / 10


def danger(a_min, rain24, gust_max) -> str:
    if a_min < 2.0 and rain24 < 2:
        return "muito alto" if gust_max >= 8 else "alto"
    if a_min < 2.5 and rain24 < 5:
        return "moderado"
    return "baixo"


def in_sector(lon, lat, lon0, lat0, bearing_to, length_km, half_angle):
    dx = (lon - lon0) * 111.32 * np.cos(np.radians(lat0))
    dy = (lat - lat0) * 111.32
    dist = np.hypot(dx, dy)
    ang = (np.degrees(np.arctan2(dx, dy)) + 360) % 360
    diff = np.abs((ang - bearing_to + 180) % 360 - 180)
    return (dist <= length_km) & (diff <= half_angle)


def sector_polygon(lon0, lat0, bearing_to, length_km, half_angle, steps=12):
    pts = [[lon0, lat0]]
    for a in np.linspace(bearing_to - half_angle, bearing_to + half_angle, steps):
        r = np.radians(a)
        pts.append([round(lon0 + length_km * np.sin(r) / (111.32 * np.cos(np.radians(lat0))), 4),
                    round(lat0 + length_km * np.cos(r) / 111.32, 4)])
    return pts + [[lon0, lat0]]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--aoi", default="manaus")
    a = ap.parse_args()
    aoi = load_aois()[a.aoi]
    w, s, e, n = aoi["bbox"]
    bbox = [w - BUFFER_DEG, s - BUFFER_DEG, e + BUFFER_DEG, n + BUFFER_DEG]
    key = user_env("FIRMS_MAP_KEY")
    if not key:
        raise SystemExit("Defina FIRMS_MAP_KEY.")

    print(f"Fogo agora — {a.aoi}")
    fires = fetch_fires(bbox, key.strip())
    clusters = cluster(fires, bbox)
    top = clusters[:20]

    gx = np.linspace(bbox[0] + 0.2, bbox[2] - 0.2, GRID_N)
    gy = np.linspace(bbox[1] + 0.2, bbox[3] - 0.2, GRID_N)
    grid_pts = [(float(y), float(x)) for y in gy for x in gx]
    pts = grid_pts + [(c["lat"], c["lon"]) for c in top] + [tuple(aoi["center"][::-1])]
    wx = weather(pts)
    now = pd.Timestamp.now(tz=TZ).tz_localize(None).floor("h")

    # wind arrows on the grid, every 3 h for 72 h (time slider in the app)
    steps = [now + pd.Timedelta(hours=3 * k) for k in range(24)]
    arrows = []
    for (lat, lon), wj in zip(grid_pts, wx[:len(grid_pts)]):
        h = frame(wj, now).set_index("time")
        sel = h.reindex(steps)
        arrows.append({"lat": round(lat, 3), "lon": round(lon, 3),
                       "speed": sel.wind_speed_10m.round(1).tolist(),
                       "to": ((sel.wind_direction_10m + 180) % 360).round(0).tolist()})

    pop = json.loads((WEB_DATA / f"population_{a.aoi}_wide.json").read_text(encoding="utf-8"))["cells"]
    plon, plat, pval = (np.array([c[i] for c in pop]) for i in range(3))
    downwind = np.zeros(len(pop), bool)

    for c, wj in zip(top, wx[len(grid_pts):len(grid_pts) + len(top)]):
        h = frame(wj, now)
        h24, h12 = h.iloc[:24], h.iloc[:12]
        ai = angstrom(h24.temperature_2m, h24.relative_humidity_2m)
        spd, to = wind_vector_mean(h12.wind_speed_10m.to_numpy(), h12.wind_direction_10m.to_numpy())
        c.update({"angstrom_min": round(float(ai.min()), 2), "t_max": round(float(h24.temperature_2m.max()), 1),
                  "rh_min": int(h24.relative_humidity_2m.min()), "wind_ms": round(spd, 1), "wind_to": round(to),
                  "gust_max": round(float(h24.wind_gusts_10m.max()), 1),
                  "rain24": round(float(h24.precipitation.sum()), 1), "rain72": round(float(h.precipitation.sum()), 1),
                  "vpd_max_kpa": round(float(h24.vapour_pressure_deficit.max()), 2)})
        c["danger"] = danger(c["angstrom_min"], c["rain24"], c["gust_max"])
        if c["danger"] != "baixo":
            if spd < CALM_MS:  # calm / variable wind: no direction -> smoke pools around the fire
                length, half = CALM_KM, 180
                c["calm"] = True
            else:
                length, half = SMOKE_KM, SMOKE_HALF_ANGLE
            c["smoke"] = sector_polygon(c["lon"], c["lat"], to, length, half, steps=12 if half < 180 else 36)
            inside = in_sector(plon, plat, c["lon"], c["lat"], to, length, half)
            c["people_downwind"] = int(pval[inside].sum())
            downwind |= inside

    # regional 72 h series at the AOI centre (chart)
    hc = frame(wx[-1], now)
    region = {"time": hc.time.dt.strftime("%Y-%m-%dT%H:%M").tolist(),
              "temp": hc.temperature_2m.round(1).tolist(), "rh": hc.relative_humidity_2m.round(0).tolist(),
              "wind": hc.wind_speed_10m.round(1).tolist(), "gust": hc.wind_gusts_10m.round(1).tolist(),
              "rain": hc.precipitation.round(1).tolist(),
              "angstrom": angstrom(hc.temperature_2m, hc.relative_humidity_2m).round(2).tolist()}

    active = [c for c in top if c.get("danger") not in (None, "baixo")]
    out = {"aoi": a.aoi, "generated_local": f"{pd.Timestamp.now(tz=TZ):%Y-%m-%d %H:%M}", "tz": TZ,
           "bbox": [round(v, 3) for v in bbox], "steps": [t.strftime("%Y-%m-%dT%H:%M") for t in steps],
           "n_fires": int(len(fires)), "n_clusters": len(clusters),
           "by_sensor": fires.sensor.value_counts().to_dict(),
           "fires": [[round(r.lon, 4), round(r.lat, 4), round(float(r.frp), 1), f"{r.date} {r.time[:2]}:{r.time[2:]}", r.sensor]
                     for r in fires.itertuples()],
           "clusters": top, "n_active": len(active), "people_downwind": int(pval[downwind].sum()),
           "downwind_cells": np.nonzero(downwind)[0].tolist(), "arrows": arrows, "region": region,
           "smoke_km": SMOKE_KM, "sources": ["NASA FIRMS NRT (VIIRS NOAA-20/21, S-NPP; MODIS)",
                                             "Open-Meteo forecast (ECMWF/GFS)", "WorldPop 2020"]}
    write_json(WEB_DATA / f"nowcast_{a.aoi}.json", out)
    dist = {d: sum(1 for c in top if c.get("danger") == d) for d in ("muito alto", "alto", "moderado", "baixo")}
    print(f"  {len(fires)} focos em {len(clusters)} aglomerados | risco nos 20 maiores: {dist} | "
          f"pessoas a favor do vento (≤{SMOKE_KM} km): {out['people_downwind']:,}")
    for c in top[:5]:
        print(f"   {c['lat']:.3f},{c['lon']:.3f} n={c['n']} FRP={c['frp']} Angström {c['angstrom_min']} "
              f"UR mín {c['rh_min']}% vento {c['wind_ms']} m/s → {c['wind_to']}° chuva24 {c['rain24']} mm "
              f"=> {c['danger']} | pessoas a favor do vento {c.get('people_downwind', 0):,}")


if __name__ == "__main__":
    main()
