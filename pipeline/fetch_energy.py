"""Energy data for the Amazon: hydropower (water -> power) and solar (sun vs smoke).

Hydropower — ONS (Operador Nacional do Sistema Elétrico) open data, no login:
  * dados_hidrologicos_di : daily inflow (vazão afluente, m³/s), turbined/spilled flow, upstream level
  * geracao_usina_2_ho    : hourly generation per plant (MW)
  * capacidade-geracao    : generating units with start/stop dates -> installed capacity month by month
  Plants: Balbina (Uatumã, supplies Manaus), Santo Antônio + Jirau (Madeira), Belo Monte (Xingu), Samuel (Jamari)

Solar — NASA POWER (no login):
  * monthly ALLSKY_SFC_SW_DWN (GHI, kWh/m²/day), CLRSKY_SFC_SW_DWN (cloud-free GHI), AOD_55 (aerosols = smoke)
    at each AOI centre, 2001 → latest
  * annual-mean GHI climatology on a regional grid (solar potential map)

Outputs: data/processed/hydro_monthly.csv, data/processed/solar_<aoi>.csv, web/public/data/solar_grid.json
Usage:   python pipeline/fetch_energy.py
"""
import datetime as dt
import io

import numpy as np
import pandas as pd
import requests

from common import DATA_PROCESSED, DATA_RAW, WEB_DATA, http_get, load_aois, write_json

ONS = "https://ons-aws-prod-opendata.s3.amazonaws.com/dataset/"
FIRST_YEAR = 2013
PLANTS = {  # id_reservatorio (hydro data) -> metadata; coordinates of the powerhouse (approximate)
    # ceg = ANEEL plant code: stable across ONS files (plant NAMES changed format in 2026-05, e.g. "Jirau" -> "UHE JIRAU")
    "AMUHBB": {"name": "Balbina", "river": "Uatumã", "ceg": "UHE.PH.AM.000190-2", "cap": "BALBINA", "lat": -1.917, "lon": -59.473,
               "note": "abastece Manaus; reservatório de acumulação"},
    "AMUSSN": {"name": "Santo Antônio", "river": "Madeira", "ceg": "UHE.PH.RO.029707-0", "cap": "SANTO ANTÔNIO", "lat": -8.802, "lon": -63.950,
               "note": "fio d'água: gera com a água que passa"},
    "AMUHJI": {"name": "Jirau", "river": "Madeira", "ceg": "UHE.PH.RO.029736-4", "cap": "JIRAU", "lat": -9.264, "lon": -64.644,
               "note": "fio d'água: gera com a água que passa"},
    # Belo Monte's own "reservoir" in ONS is the intermediate canal (inflow ~0); the Xingu flow enters at Pimental
    "AMUSBM": {"name": "Belo Monte", "river": "Xingu", "ceg": "UHE.PH.PA.030354-2", "cap": ("BELO MONTE", "PIMENTAL"), "lat": -3.126, "lon": -51.775,
               "inflow_from": "AMUSPM", "note": "maior usina 100% brasileira; fio d'água (vazão medida em Pimental)"},
    "AMSU": {"name": "Samuel", "river": "Jamari", "ceg": "UHE.PH.RO.002687-5", "cap": "SAMUEL", "lat": -8.750, "lon": -63.457,
             "note": "reservatório de acumulação"},
}
EXTRA_RES = {p["inflow_from"] for p in PLANTS.values() if p.get("inflow_from")}
POWER_M = "https://power.larc.nasa.gov/api/temporal/monthly/point"
POWER_CLIM = "https://power.larc.nasa.gov/api/temporal/climatology/regional"
SOLAR_GRID_BBOX = (-67.0, -11.0, -50.0, 2.0)   # same Amazon view as the app's energy tab


def cached(url: str, name: str) -> io.BytesIO:
    p = DATA_RAW / "ons" / name
    p.parent.mkdir(parents=True, exist_ok=True)
    this_year = str(dt.date.today().year)
    if not p.exists() or this_year in name:  # current-year files keep growing: always refresh
        p.write_bytes(http_get(url, timeout=600).content)
    return io.BytesIO(p.read_bytes())


def hydro_daily() -> pd.DataFrame:
    frames = []
    for y in range(FIRST_YEAR, dt.date.today().year + 1):
        print(f"  ONS hidrologia {y}…")
        d = pd.read_csv(cached(f"{ONS}dados_hidrologicos_di/DADOS_HIDROLOGICOS_RES_{y}.csv", f"hidro_{y}.csv"), sep=";",
                        usecols=["id_reservatorio", "din_instante", "val_nivelmontante", "val_vazaoafluente",
                                 "val_vazaoturbinada", "val_vazaovertida"])
        d["id_reservatorio"] = d.id_reservatorio.str.strip()
        frames.append(d[d.id_reservatorio.isin(set(PLANTS) | EXTRA_RES)])
    d = pd.concat(frames)
    d["date"] = pd.to_datetime(d.din_instante).dt.to_period("M").dt.to_timestamp()
    for c in ("val_nivelmontante", "val_vazaoafluente", "val_vazaoturbinada", "val_vazaovertida"):
        d[c] = pd.to_numeric(d[c], errors="coerce")
    m = d.groupby(["id_reservatorio", "date"]).agg(
        inflow=("val_vazaoafluente", "mean"), turbined=("val_vazaoturbinada", "mean"),
        spilled=("val_vazaovertida", "mean"), level=("val_nivelmontante", "mean")).reset_index()
    for rid, p in PLANTS.items():  # take the river inflow from the upstream structure where needed
        if p.get("inflow_from"):
            src = m[m.id_reservatorio == p["inflow_from"]].set_index("date").inflow
            sel = m.id_reservatorio == rid
            m.loc[sel, "inflow"] = m.loc[sel, "date"].map(src).values
    return m[m.id_reservatorio.isin(PLANTS)]


def generation_monthly() -> pd.DataFrame:
    by_ceg = {v["ceg"]: k for k, v in PLANTS.items()}
    frames = []
    today = dt.date.today()
    # yearly files up to 2021, monthly files afterwards (ONS layout)
    files = [(f"GERACAO_USINA-2_{y}.parquet", f"ger_{y}.parquet") for y in range(FIRST_YEAR, 2022)]
    files += [(f"GERACAO_USINA-2_{y}_{m:02d}.parquet", f"ger_{y}_{m:02d}.parquet")
              for y in range(2022, today.year + 1) for m in range(1, 13) if (y, m) <= (today.year, today.month)]
    for remote, local in files:
        try:
            g = pd.read_parquet(cached(f"{ONS}geracao_usina_2_ho/{remote}", local), columns=["din_instante", "ceg", "val_geracao"])
        except Exception as e:  # noqa: BLE001
            print(f"   ! {remote}: {e}")
            continue
        g["id_reservatorio"] = g.ceg.astype(str).str[:18].map(by_ceg)  # "UHE.PH.AM.000190-2.01" -> plant
        g = g.dropna(subset=["id_reservatorio"])
        if g.empty:
            print(f"   ! {remote}: nenhuma usina encontrada")
            continue
        g["val_geracao"] = pd.to_numeric(g.val_geracao, errors="coerce")
        g["date"] = pd.to_datetime(g.din_instante).dt.to_period("M").dt.to_timestamp()
        # hourly MW summed over a plant's units, then averaged over the month = MW médio
        hourly = g.groupby(["id_reservatorio", "din_instante", "date"]).val_geracao.sum().reset_index()
        frames.append(hourly.groupby(["id_reservatorio", "date"]).val_geracao.mean().reset_index())
        print(f"  ONS geração {remote[16:-8]}")
    g = pd.concat(frames)
    return g.groupby(["id_reservatorio", "date"]).val_geracao.mean().rename("gen_mw").reset_index()


def capacity_monthly(dates) -> pd.DataFrame:
    c = pd.read_csv(io.BytesIO(http_get(f"{ONS}capacidade-geracao/CAPACIDADE_GERACAO.csv").content), sep=";")
    rows = []
    for rid, p in PLANTS.items():
        names = p["cap"] if isinstance(p["cap"], tuple) else (p["cap"],)  # complex = all powerhouses
        u = c[c.nom_usina.str.upper().isin(names)].copy()
        start = pd.to_datetime(u.dat_entradaoperacao, errors="coerce")
        stop = pd.to_datetime(u.dat_desativacao, errors="coerce")
        pot = pd.to_numeric(u.val_potenciaefetiva, errors="coerce")
        for d in dates:
            end = d + pd.offsets.MonthEnd(0)
            on = (start.isna() | (start <= end)) & (stop.isna() | (stop > d))
            rows.append((rid, d, float(pot[on].sum())))
    return pd.DataFrame(rows, columns=["id_reservatorio", "date", "cap_mw"])


def solar_point(lon, lat) -> pd.DataFrame:
    j = http_get(POWER_M, params=dict(parameters="ALLSKY_SFC_SW_DWN,CLRSKY_SFC_SW_DWN,AOD_55", community="RE",
                                      longitude=lon, latitude=lat, start=2001, end=dt.date.today().year,
                                      format="JSON"), timeout=180).json()["properties"]["parameter"]
    df = pd.DataFrame(j)
    df = df[~df.index.str.endswith("13")].replace(-999, np.nan)
    df.index = pd.to_datetime(df.index, format="%Y%m")
    return df.rename(columns={"ALLSKY_SFC_SW_DWN": "ghi", "CLRSKY_SFC_SW_DWN": "ghi_clear", "AOD_55": "aod"}) \
        .rename_axis("date").reset_index().dropna(how="all", subset=["ghi", "aod"])


def solar_grid():
    """POWER regional climatology allows at most 10° per side -> query tiles and merge (1° grid)."""
    w, s, e, n = SOLAR_GRID_BBOX
    cells = {}
    for x0 in np.arange(w, e, 9):
        for y0 in np.arange(s, n, 9):
            box = {"longitude-min": x0, "longitude-max": min(x0 + 9, e), "latitude-min": y0, "latitude-max": min(y0 + 9, n)}
            r = requests.get(POWER_CLIM, params={"parameters": "ALLSKY_SFC_SW_DWN", "community": "RE", "format": "JSON", **box},
                             timeout=300)
            if r.status_code != 200:
                print(f"  ! grade solar POWER {box}: HTTP {r.status_code} {r.text[:120]}")
                continue
            for f in r.json().get("features", []):
                lon, lat = f["geometry"]["coordinates"][:2]
                v = f["properties"]["parameter"]["ALLSKY_SFC_SW_DWN"].get("ANN")
                if v is not None and v > 0:
                    cells[(round(lon, 2), round(lat, 2))] = round(v, 2)
    write_json(WEB_DATA / "solar_grid.json", {"param": "GHI média anual (kWh/m²/dia)", "cell_deg": 1.0,
                                              "cells": [[x, y, v] for (x, y), v in sorted(cells.items())]})
    print(f"  grade solar: {len(cells)} células")


def main():
    print("Hidrelétricas (ONS)…")
    h = hydro_daily()
    g = generation_monthly()
    m = h.merge(g, on=["id_reservatorio", "date"], how="outer")
    m = m.merge(capacity_monthly(sorted(m.date.unique())), on=["id_reservatorio", "date"], how="left")
    m["cf"] = m.gen_mw / m.cap_mw.replace(0, np.nan)
    m["plant"] = m.id_reservatorio.map(lambda k: PLANTS[k]["name"])
    m.sort_values(["plant", "date"]).to_csv(DATA_PROCESSED / "hydro_monthly.csv", index=False)
    print(m.groupby("plant").agg(meses=("date", "count"), ultimo=("date", "max"), cap=("cap_mw", "last")).to_string())
    write_json(DATA_PROCESSED / "hydro_plants.json", PLANTS)

    print("Solar (NASA POWER)…")
    for key, aoi in load_aois().items():
        df = solar_point(*aoi["center"])
        df.to_csv(DATA_PROCESSED / f"solar_{key}.csv", index=False)
        print(f"  {key}: {len(df)} meses, até {df.date.max():%Y-%m}")
    solar_grid()


if __name__ == "__main__":
    main()
