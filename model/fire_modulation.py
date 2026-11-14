"""How much do climate conditions push a fire season above/below its RECENT level?

Fire counts in Amazonas jumped after ~2019 (expanding deforestation frontier), so comparing a season
with the long-term mean mostly measures that human-driven shift. Instead each Aug–Oct season is
compared with the mean of the 3 previous seasons (ratio), and the ratio is averaged (geometric mean)
by ENSO phase (ONI Jul–Sep) and by Manaus river minimum (Sep–Nov, lowest third = "low river").

Output: web/public/data/fire_modulation.json
Usage:  python model/fire_modulation.py
"""
import sys
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "pipeline"))
from common import DATA_PROCESSED, WEB_DATA, write_json  # noqa: E402

SEASON = ("08", "10")


def main():
    f = pd.read_csv(DATA_PROCESSED / "fires_amazonas.csv", parse_dates=["date"]).set_index("date").fires
    oni = pd.read_csv(DATA_PROCESSED / "oni.csv", parse_dates=["date"]).set_index("date").oni
    riv = pd.read_csv(DATA_PROCESSED / "river_manaus.csv", parse_dates=["date"]).set_index("date")
    last = f.index.max()
    last_full = last.year if last.month >= int(SEASON[1]) else last.year - 1

    def season(y):
        return f[f"{y}-{SEASON[0]}":f"{y}-{SEASON[1]}"].sum()

    rows = []
    for y in range(f.index.min().year + 3, last_full + 1):
        prev = np.mean([season(y - k) for k in (1, 2, 3)])
        rows.append(dict(year=y, season=int(season(y)), ratio=season(y) / prev,
                         oni=oni[f"{y}-07":f"{y}-09"].mean(),
                         rmin=riv.level_min_m[f"{y}-09":f"{y}-11"].min()))
    d = pd.DataFrame(rows)
    lr = np.log(d.ratio)
    nino, nina = d.oni >= 0.5, d.oni <= -0.5
    thr = d.rmin.quantile(1 / 3)
    low = d.rmin < thr
    g = lambda m: round(float(np.exp(lr[m].mean())), 2)  # noqa: E731  geometric mean of ratios
    out = {"season": "ago–out", "years": d.year.tolist(), "season_fires": d.season.tolist(),
           "ratio_vs_prev3": d.ratio.round(2).tolist(), "oni_jas": d.oni.round(2).tolist(),
           "river_min_m": d.rmin.round(2).tolist(),
           "el_nino": g(nino), "neutral": g(~nino & ~nina), "la_nina": g(nina),
           "low_river": g(low), "other_river": g(~low), "n": len(d), "n_el_nino": int(nino.sum()),
           "n_la_nina": int(nina.sum()), "low_river_threshold_m": round(float(thr), 2),
           "spearman_ratio_vs_river_min": round(float(lr.corr(d.rmin, method="spearman")), 2)}
    write_json(WEB_DATA / "fire_modulation.json", out)
    print(f"{len(d)} temporadas ({d.year.min()}–{d.year.max()}): El Niño x{out['el_nino']} (n={out['n_el_nino']}), "
          f"La Niña x{out['la_nina']} (n={out['n_la_nina']}), neutro x{out['neutral']} | rio baixo (<{thr:.2f} m) "
          f"x{out['low_river']}, demais x{out['other_river']}")
    print(d.tail(4).round(2).to_string(index=False))


if __name__ == "__main__":
    main()
