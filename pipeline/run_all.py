"""Run the whole no-login pipeline: ONI, NASA POWER, WorldPop, NISAR catalog and the forecast model.

  python pipeline/run_all.py

Steps that need extra input are run separately:
  python pipeline/fetch_ana.py --aoi manaus            (ANA service is often down; see --csv fallback)
  python pipeline/search_nisar.py --plan                (dates + filler tracks; no download)
  python pipeline/process_gcov.py --aoi manaus         (remote partial read; needs EARTHDATA_TOKEN)
"""
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
STEPS = [
    ["pipeline/fetch_oni.py"],
    ["pipeline/fetch_power.py"],
    ["pipeline/fetch_population.py"],
    ["pipeline/fetch_landcover.py"],
    ["pipeline/search_nisar.py"],
    ["model/train_forecast.py", "--aoi", "manaus", "--target", "precip"],
    ["model/train_forecast.py", "--aoi", "tefe", "--target", "precip"],
    ["pipeline/fetch_fires.py"],          # current year only with FIRMS_MAP_KEY; archives need no key
    ["pipeline/fetch_energy.py"],         # ONS hydropower + NASA POWER solar
    ["pipeline/render_solar.py"],         # smooth relief solar map per AOI
]
# River stage needs the HidroWeb zip in data/ana/ (see README); add it when present.
_ana = sorted((ROOT / "data" / "ana").glob("Estacao_14990000_*.zip"))
if _ana:
    STEPS += [["pipeline/fetch_ana.py", "--aoi", "manaus", "--csv", str(_ana[-1])],
              ["model/train_forecast.py", "--aoi", "manaus", "--target", "river", "--split", "2010-12-01"],
              ["model/fire_modulation.py"],
              ["model/train_forecast.py", "--aoi", "amazonas", "--target", "fire", "--split", "2015-12-01"],
              ["model/energy_analysis.py"]]

shutil.copy(ROOT / "config" / "aois.json", ROOT / "web" / "public" / "data" / "aois.json")

for step in STEPS:
    print(f"\n=== {' '.join(step)} ===")
    subprocess.run([sys.executable, *step], cwd=ROOT, check=True)
