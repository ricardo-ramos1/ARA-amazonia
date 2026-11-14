"""River stage (cota, cm) from ANA (Agência Nacional de Águas).

Two ways in:
  1. ANA web service (HidroSerieHistorica) — frequently times out, so we request one year at a time with retries.
  2. Fallback: download the station CSV manually at https://www.snirh.gov.br/hidroweb/serieshistoricas
     (station 14990000 = Rio Negro em Manaus), save it anywhere and run:
        python pipeline/fetch_ana.py --csv <arquivo>.csv --aoi manaus

Both sources use the same "wide" layout: one row per month with columns Cota01..Cota31.
Output: data/processed/river_<aoi>.csv (monthly mean, min and max stage in metres).
"""
import argparse
import datetime as dt
import xml.etree.ElementTree as ET

import pandas as pd

from common import DATA_PROCESSED, WEB_DATA, http_get, load_aois, write_json

SERVICE = "http://telemetriaws1.ana.gov.br/ServiceANA.asmx/HidroSerieHistorica"


def wide_to_daily(df: pd.DataFrame, date_col: str, consist_col: str | None) -> pd.DataFrame:
    df = df.copy()
    df["month"] = pd.to_datetime(df[date_col], dayfirst=True, errors="coerce").dt.to_period("M")
    if consist_col and consist_col in df:  # prefer consisted data (2) over raw (1) when both exist
        df = df.sort_values(consist_col).drop_duplicates("month", keep="last")
    cota_cols = [c for c in df.columns if c.startswith("Cota") and c[4:].isdigit()]
    long = df.melt(id_vars="month", value_vars=cota_cols, var_name="d", value_name="cota_cm")
    long["cota_cm"] = pd.to_numeric(long["cota_cm"].astype(str).str.replace(",", "."), errors="coerce")
    long["day"] = long["d"].str[4:].astype(int)
    long = long.dropna(subset=["cota_cm", "month"])
    long["date"] = pd.to_datetime(dict(year=long.month.dt.year, month=long.month.dt.month, day=long.day), errors="coerce")
    return long.dropna(subset=["date"])[["date", "cota_cm"]].sort_values("date")


def from_service(station: str, y0: int, y1: int) -> pd.DataFrame:
    frames = []
    for y in range(y0, y1 + 1):
        print(f"  ANA {station} {y}…")
        try:
            r = http_get(SERVICE, params=dict(codEstacao=station, dataInicio=f"01/01/{y}", dataFim=f"31/12/{y}",
                                              tipoDados="1", nivelConsistencia=""), retries=3, timeout=90)
        except RuntimeError as e:
            print(f"   ! {e}")
            continue
        root = ET.fromstring(r.content)
        rows = [{c.tag: c.text for c in el} for el in root.iter("SerieHistorica")]
        if rows:
            frames.append(pd.DataFrame(rows))
    if not frames:
        raise RuntimeError("ANA não retornou dados (serviço fora do ar?). Use o fallback --csv.")
    return wide_to_daily(pd.concat(frames), "DataHora", "NivelConsistencia")


def read_cotas_text(path: str) -> str:
    """Return the stage-series text from a .csv, or from a HidroWeb .zip (possibly with nested zips)."""
    import io
    import zipfile

    def from_zip(zf: zipfile.ZipFile) -> str | None:
        for name in zf.namelist():
            low = name.lower()
            if low.endswith(".zip"):
                found = from_zip(zipfile.ZipFile(io.BytesIO(zf.read(name))))
                if found:
                    return found
            elif "cota" in low and low.endswith((".csv", ".txt")):
                print(f"  usando {name}")
                return zf.read(name).decode("latin-1")
        return None

    if str(path).lower().endswith(".zip"):
        text = from_zip(zipfile.ZipFile(path))
        if text is None:
            raise SystemExit(f"Nenhum arquivo de cotas dentro de {path}")
        return text
    with open(path, encoding="latin-1") as f:
        return f.read()


def from_csv(path: str) -> pd.DataFrame:
    # HidroWeb CSVs have a metadata header block; find the line with the column names.
    import io

    text = read_cotas_text(path)
    lines = text.splitlines()
    hdr = next(i for i, l in enumerate(lines) if "Data;" in l or "Data," in l)
    sep = ";" if ";" in lines[hdr] else ","
    df = pd.read_csv(io.StringIO("\n".join(lines[hdr:])), sep=sep, dtype=str)
    return wide_to_daily(df, "Data", "NivelConsistencia")


TELEMETRY = "http://telemetriaws1.ana.gov.br/ServiceANA.asmx/DadosHidrometeorologicos"


def telemetry_month(station: str, first: dt.date) -> pd.DataFrame:
    last = (pd.Timestamp(first) + pd.offsets.MonthEnd(0)).date()
    try:
        r = http_get(TELEMETRY, params=dict(codEstacao=station, dataInicio=f"{first:%d/%m/%Y}",
                                            dataFim=f"{last:%d/%m/%Y}"), retries=3, timeout=120)
    except RuntimeError as e:
        print(f"   ! {first:%Y-%m}: {e}")
        return pd.DataFrame(columns=["date", "cota_cm"])
    root = ET.fromstring(r.content)
    rows = [(el.findtext("DataHora"), el.findtext("Nivel")) for el in root.iter("DadosHidrometereologicos")]
    df = pd.DataFrame(rows, columns=["t", "cota_cm"])
    df["date"] = pd.to_datetime(df["t"].str.strip(), errors="coerce").dt.normalize()
    df["cota_cm"] = pd.to_numeric(df["cota_cm"], errors="coerce")
    return df.dropna().groupby("date", as_index=False)["cota_cm"].mean()  # 15-min -> daily mean


def from_telemetry(station: str, start: dt.date) -> pd.DataFrame:
    """Recent data (the consolidated HidroWeb series lags years behind): one request per month."""
    from concurrent.futures import ThreadPoolExecutor

    months = pd.date_range(start, dt.date.today(), freq="MS").date
    print(f"  telemetria {station}: {len(months)} meses a partir de {start:%Y-%m}…")
    with ThreadPoolExecutor(4) as ex:
        parts = list(ex.map(lambda m: telemetry_month(station, m), months))
    return pd.concat(parts, ignore_index=True).sort_values("date")


def clean(daily: pd.DataFrame) -> pd.DataFrame:
    """Drop gross errors (typos, sensor glitches): values far from the station's robust range."""
    v = daily["cota_cm"]
    med, mad = v.median(), (v - v.median()).abs().median()
    ok = (v - med).abs() <= 8 * 1.4826 * mad
    # day-to-day jumps > 1.5 m are not physical for large Amazon rivers
    jump = daily.sort_values("date")["cota_cm"].diff().abs()
    ok &= ~((jump > 150) & (jump.shift(-1) > 150))
    if (~ok).sum():
        print(f"  removidos {(~ok).sum()} valores espúrios")
    return daily[ok]


def monthly(daily: pd.DataFrame) -> pd.DataFrame:
    m = daily.set_index("date")["cota_cm"].resample("MS").agg(["mean", "min", "max", "count"])
    m = m[m["count"] >= 15] / [100, 100, 100, 1]  # cm -> m, require >= 15 days
    m.columns = ["level_m", "level_min_m", "level_max_m", "n_days"]
    return m.reset_index()


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--aoi", default="manaus")
    ap.add_argument("--csv", help="CSV baixado manualmente do HidroWeb")
    ap.add_argument("--start", type=int, default=1990)
    ap.add_argument("--end", type=int, default=dt.date.today().year)
    ap.add_argument("--no-telemetry", action="store_true", help="não completar com dados telemétricos recentes")
    a = ap.parse_args()
    aoi = load_aois()[a.aoi]
    daily = from_csv(a.csv) if a.csv else from_service(aoi["ana_station"], a.start, a.end)
    if not a.no_telemetry:
        # telemetry fills everything after the historical series (historical wins where both exist)
        tel = from_telemetry(aoi["ana_station"], (daily["date"].max() + pd.Timedelta(days=1)).date().replace(day=1))
        tel = tel[tel["date"] > daily["date"].max()]
        print(f"  telemetria: {len(tel)} dias novos")
        daily = pd.concat([daily, tel], ignore_index=True)
    daily = clean(daily)
    m = monthly(daily)
    m.to_csv(DATA_PROCESSED / f"river_{a.aoi}.csv", index=False)
    write_json(WEB_DATA / f"river_{a.aoi}.json", {"dates": m.date.dt.strftime("%Y-%m").tolist(),
                                                  "level": m.level_m.round(2).tolist()})
    print(f"  {len(m)} meses, {m.date.min():%Y-%m} → {m.date.max():%Y-%m}")


if __name__ == "__main__":
    main()
