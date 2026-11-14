"""Monthly precipitation for each AOI from NASA POWER (no login needed).

PRECTOTCORR = bias-corrected total precipitation (mm/day), MERRA-2 based, 1981 -> present.
"""
import datetime as dt

import numpy as np
import pandas as pd

from common import DATA_PROCESSED, WEB_DATA, http_get, load_aois, write_json

URL = "https://power.larc.nasa.gov/api/temporal/monthly/point"


def fetch(lon: float, lat: float) -> pd.DataFrame:
    params = dict(parameters="PRECTOTCORR", community="AG", longitude=lon, latitude=lat,
                  start=1981, end=dt.date.today().year, format="JSON")
    j = http_get(URL, params=params, timeout=180).json()
    rec = j["properties"]["parameter"]["PRECTOTCORR"]
    rows = [(k, v) for k, v in rec.items() if not k.endswith("13")]  # "YYYY13" = annual mean
    df = pd.DataFrame(rows, columns=["ym", "precip_mm_day"])
    df["date"] = pd.to_datetime(df["ym"], format="%Y%m")
    df["precip_mm_day"] = df["precip_mm_day"].replace(-999, np.nan)
    df = df.dropna()[["date", "precip_mm_day"]]
    clim = df.groupby(df.date.dt.month)["precip_mm_day"].transform("mean")
    df["precip_anom"] = df["precip_mm_day"] - clim
    return df


def main():
    out = {}
    for key, aoi in load_aois().items():
        print(f"NASA POWER precipitação — {key}…")
        df = fetch(*aoi["center"])
        df.to_csv(DATA_PROCESSED / f"precip_{key}.csv", index=False)
        out[key] = {"dates": df.date.dt.strftime("%Y-%m").tolist(),
                    "precip": df.precip_mm_day.round(2).tolist(),
                    "anom": df.precip_anom.round(2).tolist()}
        print(f"  {len(df)} meses, {df.date.min():%Y-%m} → {df.date.max():%Y-%m}")
    write_json(WEB_DATA / "precip.json", out)


if __name__ == "__main__":
    main()
