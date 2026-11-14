"""ENSO-driven forecast: predict a monthly hydro-climate target h months ahead.

Targets (pick with --target):
  precip  -> NASA POWER precipitation anomaly at the AOI (mm/day)   [always available]
  river   -> ANA river stage at the AOI (m)                          [needs fetch_ana.py]
  fire    -> monthly FIRMS active-fire count in a region             [needs fetch_fires.py]
             (--aoi amazonas|manaus|tefe). Modelled as log1p(count); extra drought features:
             rain and river-stage anomalies (lags 0–2 + 3-month mean). Metrics in fires/month.

Features: ONI at lags 1..8 months, the target's own recent values (persistence), month (sin/cos).
Model:    scikit-learn HistGradientBoosting (median) + quantile models (P10/P90) for an uncertainty band.
Validation is strictly temporal: train <= --split, test after (covers the 2023 El Niño drought).

Metrics vs two baselines that any forecast must beat:
  climatology  -> the average for that calendar month (from training data)
  persistence  -> this month's anomaly carries on h months ahead
Reported: MAE, RMSE, NSE (Nash–Sutcliffe) and skill = 1 - MSE_model / MSE_climatology.

Usage: python model/train_forecast.py --aoi manaus --target precip
Output: web/data/forecast_<aoi>_<target>.json
"""
import argparse
import sys
from pathlib import Path

import numpy as np
import pandas as pd
from sklearn.ensemble import HistGradientBoostingRegressor

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "pipeline"))
from common import DATA_PROCESSED, WEB_DATA, write_json  # noqa: E402

HORIZONS = [1, 3, 6]
ONI_LAGS = range(1, 9)  # lag 0 is not yet published when a forecast is issued
SELF_LAGS = [0, 1, 2, 12]
EXOG_LAGS = [0, 1, 2]


def _series(name: str, col: str) -> pd.Series | None:
    p = DATA_PROCESSED / name
    return pd.read_csv(p, parse_dates=["date"]).set_index("date")[col] if p.exists() else None


def load_target(aoi: str, target: str) -> pd.Series:
    if target == "precip":
        return _series(f"precip_{aoi}.csv", "precip_mm_day").rename("y")
    if target == "fire":  # monthly active-fire count (aoi = region: amazonas | manaus | tefe)
        return np.log1p(_series(f"fires_{aoi}.csv", "fires")).rename("y")  # model in log space (skewed counts)
    return _series(f"river_{aoi}.csv", "level_m").rename("y")


def drought_exog() -> dict[str, pd.Series]:
    """Drought indicators for the fire model: rain anomaly (mean of AOIs) and Manaus river stage."""
    ex = {}
    rains = [s for s in (_series("precip_manaus.csv", "precip_mm_day"), _series("precip_tefe.csv", "precip_mm_day")) if s is not None]
    if rains:
        ex["rain"] = pd.concat(rains, axis=1).mean(axis=1)
    river = _series("river_manaus.csv", "level_m")
    if river is not None:
        ex["river"] = river
    return ex


def anomaly(s: pd.Series, idx, split) -> pd.Series:
    """Departure from the monthly climatology computed on TRAINING months only (no test leakage)."""
    s = s.reindex(idx)
    tr = s[s.index <= split]
    return s - pd.Series(idx.month, index=idx).map(tr.groupby(tr.index.month).mean())


def build(y: pd.Series, oni: pd.Series, h: int, split: pd.Timestamp, exog: dict | None = None) -> pd.DataFrame:
    idx = pd.date_range(min(y.index.min(), oni.index.min()), max(y.index.max(), oni.index.max()), freq="MS")
    y, oni = y.reindex(idx), oni.reindex(idx)
    # monthly climatology from TRAINING months only — using the full series would leak test-period info
    anom = anomaly(y, idx, split)
    X = pd.DataFrame(index=idx)
    for L in ONI_LAGS:
        X[f"oni_lag{L}"] = oni.shift(L)
    for L in SELF_LAGS:
        X[f"anom_lag{L}"] = anom.shift(L)
    for name, s in (exog or {}).items():
        a = anomaly(s, idx, split)
        for L in EXOG_LAGS:
            X[f"{name}_anom_lag{L}"] = a.shift(L)
        X[f"{name}_anom_3m"] = a.rolling(3).mean()  # accumulated deficit matters for fuel dryness
    tgt_month = (idx + pd.DateOffset(months=h)).month
    X["m_sin"], X["m_cos"] = np.sin(2 * np.pi * tgt_month / 12), np.cos(2 * np.pi * tgt_month / 12)
    X["target"] = y.shift(-h)              # value h months after the issue date
    X["target_date"] = idx + pd.DateOffset(months=h)
    # "recent level" baseline: same calendar month in the 3 previous years (known at issue time for h <= 12).
    # Captures regime shifts that climate can't explain (e.g. expanding deforestation frontier -> more fire).
    X["base_recent"] = pd.concat([y.shift(12 * k - h) for k in (1, 2, 3)], axis=1).mean(axis=1, skipna=False)
    return X


def metrics(obs, pred, clim) -> dict:
    obs, pred, clim = map(np.asarray, (obs, pred, clim))
    mse = np.mean((obs - pred) ** 2)
    return {"mae": float(np.mean(np.abs(obs - pred))), "rmse": float(np.sqrt(mse)),
            "nse": float(1 - mse / np.var(obs)),
            "skill_vs_clim": float(1 - mse / np.mean((obs - clim) ** 2))}


def r(v, k=3):
    return None if v is None or not np.isfinite(v) else round(float(v), k)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--aoi", default="manaus")
    ap.add_argument("--target", choices=["precip", "river", "fire"], default="precip")
    ap.add_argument("--split", default="2015-12-01", help="último mês (data de emissão) do treino")
    ap.add_argument("--baseline", choices=["clim", "recent"], default="clim",
                    help="o modelo aprende o desvio em relação a: clim = média histórica do mês (treino); "
                         "recent = média do mesmo mês nos 3 anos anteriores (acompanha mudanças de patamar)")
    a = ap.parse_args()

    fire = a.target == "fire"
    y = load_target(a.aoi, a.target)
    exog = drought_exog() if fire else None
    inv = np.expm1 if fire else (lambda v: np.asarray(v, dtype=float))  # back to fires/month
    oni = pd.read_csv(DATA_PROCESSED / "oni.csv", parse_dates=["date"]).set_index("date")["oni"]
    split = pd.Timestamp(a.split)
    unit = {"precip": "mm/dia", "river": "m", "fire": "focos/mês"}[a.target]
    out = {"aoi": a.aoi, "target": a.target, "split": a.split, "unit": unit, "baseline": a.baseline, "horizons": {}}
    importances = {}

    for h in HORIZONS:
        X = build(y, oni, h, split, exog)
        feats = [c for c in X.columns if c not in ("target", "target_date", "base_recent")]
        # train/test must not overlap in *target* time either
        train = X[(X.index <= split) & (X.target_date <= split)].dropna()
        test = X[X.index > split].dropna()
        live = X[X.target.isna() & X[feats + ["base_recent"]].notna().all(axis=1)].tail(1)  # latest issue date

        clim = train.groupby(train.target_date.dt.month)["target"].mean()
        test_clim = test.target_date.dt.month.map(clim).values
        live_clim = live.target_date.dt.month.map(clim).values
        persist = test_clim + test["anom_lag0"].values

        # what the model learns to correct: the long-term monthly climatology or the recent 3-year level
        if a.baseline == "recent":
            tr_base, te_base, lv_base = train.base_recent.values, test.base_recent.values, live.base_recent.values
        else:
            tr_base, te_base, lv_base = train.target_date.dt.month.map(clim).values, test_clim, live_clim

        models = {q: HistGradientBoostingRegressor(loss="quantile", quantile=q, max_iter=300,
                                                   learning_rate=0.05, max_depth=3, random_state=0)
                  for q in (0.1, 0.5, 0.9)}
        for m in models.values():
            m.fit(train[feats], train["target"] - tr_base)
        floor = 0.0 if a.target in ("precip", "fire") else -np.inf  # rain / fire counts cannot be negative
        p = {q: np.maximum(inv(m.predict(test[feats]) + te_base), floor) for q, m in models.items()}
        recent_u = np.maximum(inv(test.base_recent.values), floor)  # extra baseline: "same as recent years"
        obs, persist = inv(test.target), np.maximum(inv(persist), floor)
        # baseline climatology in the REPORTED units (arithmetic monthly mean = best constant under MSE)
        clim_u = pd.Series(inv(train.target), index=train.index).groupby(train.target_date.dt.month.values).mean()
        test_clim_u = test.target_date.dt.month.map(clim_u).values
        live_clim_u = live.target_date.dt.month.map(clim_u).values

        res = {
            "n_train": len(train), "n_test": len(test),
            "model": metrics(obs, p[0.5], test_clim_u),
            "climatology": metrics(obs, test_clim_u, test_clim_u),
            "persistence": metrics(obs, persist, test_clim_u),
            "recent_years": metrics(obs, recent_u, test_clim_u),
            "test": {"dates": test.target_date.dt.strftime("%Y-%m").tolist(),
                     "obs": [r(v) for v in obs], "pred": [r(v) for v in p[0.5]],
                     "p10": [r(v) for v in p[0.1]], "p90": [r(v) for v in p[0.9]],
                     "clim": [r(v) for v in test_clim_u]},
        }
        if len(live):
            res["live"] = {"issued": live.index[0].strftime("%Y-%m"),
                           "target_date": live.target_date.iloc[0].strftime("%Y-%m"),
                           **{k: r(max(float(inv(models[q].predict(live[feats])[0] + lv_base[0])), floor))
                              for k, q in (("p10", 0.1), ("pred", 0.5), ("p90", 0.9))},
                           "clim": r(live_clim_u[0])}
        # which features matter (permutation importance on the test set, P50 model)
        from sklearn.inspection import permutation_importance
        pi = permutation_importance(models[0.5], test[feats], test["target"] - te_base, n_repeats=10,
                                    random_state=0, scoring="neg_mean_absolute_error")
        groups = {}
        for f, v in zip(feats, pi.importances_mean):
            g = ("ENSO (ONI)" if f.startswith("oni") else "memória do alvo" if f.startswith("anom")
                 else "chuva" if f.startswith("rain") else "nível do rio" if f.startswith("river") else "mês")
            groups[g] = groups.get(g, 0) + max(v, 0)
        tot = sum(groups.values()) or 1
        res["importance"] = {g: round(v / tot, 3) for g, v in sorted(groups.items(), key=lambda kv: -kv[1])}
        importances[h] = res["importance"]
        out["horizons"][str(h)] = res
        mm, cc, pp = res["model"], res["climatology"], res["persistence"]
        print(f"h={h}m  modelo MAE={mm['mae']:.3f} NSE={mm['nse']:+.2f} skill={mm['skill_vs_clim']:+.2f} | "
              f"clim MAE={cc['mae']:.3f} | recente MAE={res['recent_years']['mae']:.3f} skill={res['recent_years']['skill_vs_clim']:+.2f} | persist MAE={pp['mae']:.3f} skill={pp['skill_vs_clim']:+.2f}"
              + (f" | previsão {res['live']['target_date']}: {res['live']['pred']} "
                 f"[{res['live']['p10']}, {res['live']['p90']}] (clim {res['live']['clim']})" if "live" in res else ""))

    for h, imp in importances.items():
        print(f"  importância h={h}m: " + ", ".join(f"{g} {v:.0%}" for g, v in imp.items()))
    write_json(WEB_DATA / f"forecast_{a.aoi}_{a.target}.json", out)


if __name__ == "__main__":
    main()
