"""Download the Oceanic Niño Index (ONI) from NOAA CPC.

ONI = 3-month running mean of SST anomaly in the Niño 3.4 region.
>= +0.5 °C -> El Niño, <= -0.5 °C -> La Niña.
Each 3-month season (e.g. "DJF") is assigned to its centre month (Jan).
"""
import io

import pandas as pd

from common import DATA_PROCESSED, WEB_DATA, http_get, write_json

URL = "https://www.cpc.ncep.noaa.gov/data/indices/oni.ascii.txt"
SEASON_CENTER = {"DJF": 1, "JFM": 2, "FMA": 3, "MAM": 4, "AMJ": 5, "MJJ": 6,
                 "JJA": 7, "JAS": 8, "ASO": 9, "SON": 10, "OND": 11, "NDJ": 12}


def phase(v: float) -> str:
    return "El Niño" if v >= 0.5 else "La Niña" if v <= -0.5 else "Neutro"


def main() -> pd.DataFrame:
    print("ONI (NOAA CPC)…")
    txt = http_get(URL).text
    df = pd.read_csv(io.StringIO(txt), sep=r"\s+")
    df["month"] = df["SEAS"].map(SEASON_CENTER)
    df["date"] = pd.to_datetime(dict(year=df["YR"], month=df["month"], day=1))
    df = df.rename(columns={"ANOM": "oni"})[["date", "SEAS", "oni"]]
    df["phase"] = df["oni"].map(phase)
    df.to_csv(DATA_PROCESSED / "oni.csv", index=False)
    write_json(WEB_DATA / "oni.json", {
        "source": URL,
        "dates": df["date"].dt.strftime("%Y-%m").tolist(),
        "oni": df["oni"].round(2).tolist(),
    })
    last = df.iloc[-1]
    print(f"  último valor: {last.SEAS} {last.date:%Y} = {last.oni:+.2f} °C ({last.phase})")
    return df


if __name__ == "__main__":
    main()
