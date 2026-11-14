"""Water -> energy in the Amazon: how much hydropower depends on river flow, how ENSO / drought hits it,
how smoke cuts solar, and the risk that low rivers cut diesel supply to isolated communities.

Inputs  (data/processed): hydro_monthly.csv, oni.csv, river_manaus.csv, solar_<aoi>.csv, fires_amazonas.csv
         (web/public/data): forecast_manaus_river.json
Output  web/public/data/energy.json
Usage   python model/energy_analysis.py
"""
import json
import math
import sys
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "pipeline"))
from common import DATA_PROCESSED, WEB_DATA, load_aois, write_json  # noqa: E402

DRY = [9, 10, 11]          # Amazon low-water / low-generation months
BASE_YEARS = (2016, 2022)  # "normal" reference period (all plants fully online from 2016–2019)


def r(v, k=2):
    return None if v is None or (isinstance(v, float) and not math.isfinite(v)) else round(float(v), k)


def main():
    h = pd.read_csv(DATA_PROCESSED / "hydro_monthly.csv", parse_dates=["date"])
    plants_meta = json.loads((DATA_PROCESSED / "hydro_plants.json").read_text(encoding="utf-8"))
    oni = pd.read_csv(DATA_PROCESSED / "oni.csv", parse_dates=["date"]).set_index("date").oni
    riv = pd.read_csv(DATA_PROCESSED / "river_manaus.csv", parse_dates=["date"]).set_index("date")

    plants, series = [], {}
    for rid, meta in plants_meta.items():
        p = h[h.id_reservatorio == rid].set_index("date").sort_index()
        p = p[p.index >= "2013-01-01"]
        if p.empty:
            continue
        mon = p.index.month
        base = p[(p.index.year >= BASE_YEARS[0]) & (p.index.year <= BASE_YEARS[1])]
        clim_in = base.groupby(base.index.month).inflow.mean()
        clim_cf = base.groupby(base.index.month).cf.mean()
        p["inflow_clim"] = mon.map(clim_in)
        p["cf_clim"] = mon.map(clim_cf)

        # water -> power: monthly correlation and "elasticity" in the dry season
        ok = p.dropna(subset=["inflow", "gen_mw"])
        corr = ok.inflow.corr(ok.gen_mw)
        dry = ok[ok.index.month.isin(DRY) & (ok.index.year >= BASE_YEARS[0]) & (ok.inflow > 0) & (ok.gen_mw > 0)]
        el = np.polyfit(np.log(dry.inflow), np.log(dry.gen_mw.clip(lower=1)), 1)[0] if len(dry) > 6 else np.nan

        # dry-season capacity factor by year
        dy = p[p.index.month.isin(DRY)].groupby(p[p.index.month.isin(DRY)].index.year)
        dry_cf = dy.cf.mean()
        dry_in = (dy.inflow.mean() / dy.inflow_clim.mean())
        last = ok.iloc[-1]
        plants.append({
            "id": rid, **{k: meta[k] for k in ("name", "river", "lat", "lon", "note")},
            "cap_mw": r(p.cap_mw.dropna().iloc[-1], 0),
            "corr_inflow_gen": r(corr), "elasticity_dry": r(el),
            "dry_cf_by_year": {int(y): r(v, 3) for y, v in dry_cf.items() if y >= 2014},
            "dry_inflow_pct_by_year": {int(y): r(v, 3) for y, v in dry_in.items() if y >= 2014},
            "last": {"date": f"{last.name:%Y-%m}", "inflow": r(last.inflow, 0), "inflow_pct": r(last.inflow / last.inflow_clim, 3),
                     "gen_mw": r(last.gen_mw, 0), "cf": r(last.cf, 3), "cf_clim": r(last.cf_clim, 3)},
        })
        s = p[p.index >= "2016-01-01"]
        series[meta["name"]] = {"dates": s.index.strftime("%Y-%m").tolist(), "inflow": [r(v, 0) for v in s.inflow],
                                "inflow_clim": [r(v, 0) for v in s.inflow_clim], "gen_mw": [r(v, 0) for v in s.gen_mw],
                                "cap_mw": [r(v, 0) for v in s.cap_mw]}

    # ENSO / Manaus river vs. dry-season inflow of the big Madeira + Xingu plants
    years = range(2014, int(h.date.dt.year.max()) + 1)
    rows = []
    for y in years:
        o = oni[f"{y}-07":f"{y}-09"].mean()
        rm = riv.level_min_m[f"{y}-09":f"{y}-11"].min() if len(riv[f"{y}-09":f"{y}-11"]) else np.nan
        row = {"year": y, "oni_jas": o, "manaus_min": rm}
        for pl in plants:
            row[pl["name"]] = pl["dry_inflow_pct_by_year"].get(y)
        rows.append(row)
    yd = pd.DataFrame(rows)
    links = {}
    for pl in plants:
        col = pl["name"]
        if yd[col].notna().sum() >= 6:
            links[col] = {"corr_oni": r(yd[col].corr(yd.oni_jas, method="spearman")),
                          "corr_manaus_min": r(yd[col].corr(yd.manaus_min, method="spearman"))}

    # headline: big run-of-river plants in drought years vs normal
    big = [p for p in plants if p["name"] in ("Santo Antônio", "Jirau", "Belo Monte")]
    def dry_gen(years_sel):
        tot = 0
        for p in big:
            s = series[p["name"]]
            d = pd.Series(s["gen_mw"], index=pd.to_datetime(s["dates"]))
            tot += d[d.index.month.isin(DRY) & d.index.year.isin(years_sel)].mean()
        return tot
    normal = dry_gen(range(2019, 2023))
    headline = {str(y): r(dry_gen([y]) / normal, 3) for y in (2023, 2024, 2025, 2026)}

    # ---- solar vs smoke (NASA POWER) ----
    fires = pd.read_csv(DATA_PROCESSED / "fires_amazonas.csv", parse_dates=["date"]).set_index("date").fires
    solar = {}
    for key in load_aois():
        f = DATA_PROCESSED / f"solar_{key}.csv"
        if not f.exists():
            continue
        s = pd.read_csv(f, parse_dates=["date"]).set_index("date")
        # POWER's "clear-sky" GHI does not respond to the real smoke (corr ~0), so use ACTUAL GHI.
        # Anomalies vs each month's climatology remove the seasonal cycle; Aug–Oct = fire season.
        s["ghi_anom"] = s.ghi - s.groupby(s.index.month).ghi.transform("mean")
        s["aod_anom"] = s.aod - s.groupby(s.index.month).aod.transform("mean")
        fire_season = s[s.index.month.isin([8, 9, 10])].dropna(subset=["ghi_anom", "aod_anom"])
        mean_ghi = fire_season.ghi.mean()
        slope = np.polyfit(fire_season.aod_anom, fire_season.ghi_anom, 1)[0] if len(fire_season) > 10 else np.nan
        q1, q3 = fire_season.aod_anom.quantile([0.25, 0.75])
        clean = fire_season.ghi_anom[fire_season.aod_anom <= q1].mean()
        smoky = fire_season.ghi_anom[fire_season.aod_anom >= q3].mean()
        aod_fire = fire_season.aod.corr(fires.reindex(fire_season.index), method="spearman")
        corr_ghi_aod = fire_season.ghi_anom.corr(fire_season.aod_anom)
        clim = s.groupby(s.index.month)[["ghi", "aod"]].mean()
        solar[key] = {
            "dates": s.index.strftime("%Y-%m").tolist(), "ghi": [r(v) for v in s.ghi], "ghi_clear": [r(v) for v in s.ghi_clear],
            "aod": [r(v, 3) for v in s.aod], "ghi_annual": r(s.ghi.mean()),
            "ghi_month_clim": [r(v) for v in clim.ghi], "aod_month_clim": [r(v, 3) for v in clim.aod],
            "smoke_loss_pct": r(100 * (clean - smoky) / mean_ghi, 1),   # smoky vs clean quartile, % of mean GHI
            "loss_per_0_1_aod_pct": r(-100 * slope * 0.1 / mean_ghi, 1),
            "corr_ghi_aod": r(corr_ghi_aod), "n_months": int(len(fire_season)),
            "corr_aod_fires": r(aod_fire),
        }

    # ---- diesel / isolated systems: risk of a severe low river at Manaus ----
    ann_min = riv.level_min_m.groupby(riv.index.year).min()
    ann_min = ann_min[ann_min.index < 2023]
    thr = float(ann_min.quantile(0.10))
    fc = json.loads((WEB_DATA / "forecast_manaus_river.json").read_text(encoding="utf-8"))
    risk = []
    for hz, H in fc["horizons"].items():
        lv = H.get("live")
        if not lv:
            continue
        sigma = max((lv["p90"] - lv["p10"]) / 2.563, 0.05)
        prob = 0.5 * (1 + math.erf((thr - lv["pred"]) / (sigma * math.sqrt(2))))
        risk.append({"h": int(hz), "target_date": lv["target_date"], "p50": lv["pred"], "p10": lv["p10"], "p90": lv["p90"],
                     "prob_severe": r(prob, 3)})
    years_below = {int(y): r(v) for y, v in riv.level_min_m.groupby(riv.index.year).min().items() if y >= 2000}

    out = {"plants": plants, "series": series, "links": links, "dry_months": DRY, "base_years": BASE_YEARS,
           "drought_dry_gen_vs_2019_2022": headline, "solar": solar,
           "diesel": {"threshold_m": r(thr), "definition": "annual minimum among the lowest 10% (1902–2022), Rio Negro at Manaus",
                      "annual_min_m": years_below, "forecast": risk}}
    write_json(WEB_DATA / "energy.json", out)

    print("Plants (dry-season Sep–Nov capacity factor):")
    for p in plants:
        cf = p["dry_cf_by_year"]
        print(f"  {p['name']:14s} {p['cap_mw']:>6} MW | corr inflow×generation {p['corr_inflow_gen']} | dry elasticity {p['elasticity_dry']} | "
              f"2019 {cf.get(2019)} 2022 {cf.get(2022)} 2023 {cf.get(2023)} 2024 {cf.get(2024)} 2025 {cf.get(2025)} 2026 {cf.get(2026)} | "
              f"last {p['last']['date']}: inflow {p['last']['inflow_pct']} of normal")
    print("Climate links (Spearman, dry-season inflow × ONI / × Manaus minimum):", links)
    print("Dry-season generation Madeira+Xingu vs 2019–2022:", headline)
    for k, v in solar.items():
        print(f"Solar {k}: {v['ghi_annual']} kWh/m²/day; smoke cuts {v['smoke_loss_pct']}% (smokiest vs cleanest quartile, corr {v['corr_ghi_aod']}, n={v['n_months']}), "
              f"{v['loss_per_0_1_aod_pct']}% per +0.1 AOD; corr AOD×fires {v['corr_aod_fires']}")
    print(f"Diesel: severe drought < {thr:.2f} m; forecast: {risk}")


if __name__ == "__main__":
    main()
