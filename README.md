# Ara — feel the Amazon's pulse from space

**NASA Space Apps Challenge 2026 · Challenge: _Dancing with the SARs_**

> In Tupi, ***ara*** means time, weather and world. The NISAR radar reads the Amazon's time — its yearly pulse of flood and drought — through clouds and canopy, and shows who is affected: water, fire and energy.

🌐 **Live demo:** https://ricardo-ramos1.github.io/pulsar-amazonia/  
🎬 **Project demo (7 slides):** https://ricardo-ramos1.github.io/pulsar-amazonia/pitch/ · [PDF](https://ricardo-ramos1.github.io/pulsar-amazonia/pitch/Ara-demo.pdf)

Every year the Amazon's rivers rise and fall by more than ten meters. El Niño can turn that rhythm into extreme drought, wildfire and power shortages for millions of people. This project follows that pulse with **NISAR**, the NASA-ISRO L-band radar that sees through clouds and even detects water **beneath the forest canopy**.

The app brings together four layers of evidence:

1. **NISAR L-band radar**: open water, **flooded forest under the canopy** (visible only at L-band) and burn scars. It works in any cloud cover.
2. **El Niño / La Niña (NOAA ONI)**: the climate driver behind Amazon floods and droughts. In ASO 2026 the ONI stands at **+2.46 °C, a very strong El Niño**.
3. **People (WorldPop) in 3D**: who lives by the water, near fires and in the smoke path.
4. **Fire and energy**: NASA FIRMS fires, weather forecasts, hydropower plants (ONS) and solar irradiance (NASA POWER).

Machine-learning models forecast river level, rainfall and fire activity 1, 3 and 6 months ahead. They are evaluated honestly against climatology, persistence and recent-year baselines.

## Highlights

| | |
|---|---|
| 🛰️ **19 NISAR dates** (Jun–Nov 2026) | Manaus and Tefé, each over ~90,000 km², read remotely without downloading full files |
| ✓ **Radar water validated** against Sentinel-2 | F1 **0.89** (Manaus) and **0.84** (Tefé) across the whole area |
| 👥 **People by the water: 194k → 112k** | Manaus region, Jun → Nov 2026: as El Niño set in, the river retreated from people's doors and 97% of the flooded forest drained |
| 🌡️ **~3 m** | the Rio Negro bottoms out at 16.4 m after strong El Niños vs. 19.4 m after strong La Niñas (1951–2026) |
| 🔥 **222 burn scars** detected by radar | **92%** have a FIRMS fire nearby, vs. 12% for random forest points |
| 📈 **River forecast** 3 months ahead | skill **+0.25** over climatology; beats persistence |
| ⚡ **Hydropower in drought** | Belo Monte, Santo Antônio and Jirau generated **54%** of normal in the 2024 drought |

## How to read it

Every view leads with **the people affected** — living by the water, near fires, in the smoke path, or depending on the river for power and fuel. **El Niño** is the second thread, shown only where our results support it and labelled by strength of evidence: *clear in strong events* (river level), *strong* (Balbina inflow) or *indirect* (fire, through drought that reaches the river). Charts are tucked into **evidence cards** that open on click; clicking a population column on the map shows who lives in that km². **▶ Tell me the story** runs a five-step guided tour — the pulse, the drought, the fire, the smoke today, the power — that doubles as the pitch script. **📖 Glossary** explains every technical term in plain language (37 terms, searchable); click any dotted-underlined word for a quick definition balloon, with a link to the full entry. The glossary is also one click away inside the story.

## The four views

| View | Headline (people) | Map | El Niño card | Evidence cards (click to open) |
|---|---|---|---|---|
| 💧 **Rivers** | people living by the water on the selected radar date | NISAR radar (open water, flooded forest, urban; gaps hatched), Sentinel-2 optical, 3D population | *clear in strong events* | ONI × Rio Negro level; river and rainfall forecasts |
| 🔥 **Fire** | people within 3 km of a fire in the 60 days before the date | fires, radar burn scars (ΔHV), 3D population near fire | *indirect* | fire forecast; fires per year in Amazonas |
| 📡 **Fire now** | people in the smoke path now | cards for the largest active clusters (click to fly there), fires colored by risk, wind arrows (+0 to +69 h), smoke sectors | — | 72 h Angström index, wind and rain |
| ⚡ **Energy** | people in the region who depend on the river for power and fuel | cards for 5 hydropower plants (click for charts), solar relief map, plant columns (height = capacity, color = inflow vs. normal) | *strong* | water × power per plant; dry-season output year by year; sun × smoke |

**Fire now** is a same-day snapshot: re-run it to refresh. It is not a validated model or an official warning.
- **Fires:** FIRMS near-real-time, VIIRS (375 m, catches more small fires) and MODIS.
- **Weather:** hourly [Open-Meteo](https://open-meteo.com/) forecast (ECMWF/GFS, no key).
- **Risk:** Angström index (RH/20 + (27 − T)/10) at the driest hour of the next 24 h, adjusted for forecast rain and gusts.
- **Smoke:** a 40 km sector downwind of the mean 12 h wind, or a 10 km circle when the wind is calm.

## Project structure

```
config/aois.json            study areas (Manaus, Tefé/Mamirauá) and preferred NISAR track
pipeline/
  fetch_oni.py              ONI from NOAA CPC (1950 → today)
  fetch_power.py            monthly rainfall from NASA POWER (1981 → today, no login)
  fetch_ana.py              river stage from ANA: HidroWeb CSV (history) + telemetry (recent)
  fetch_population.py       WorldPop 2020 ~1 km → study-area grid + wide grid (±1°)
  fetch_landcover.py        ESA WorldCover 10 m → urban/water/wetland/tree fractions (read straight from AWS)
  fetch_fires.py            FIRMS/MODIS fires 2000 → today (current year via API, needs FIRMS_MAP_KEY)
  search_nisar.py           NISAR GCOV/GUNW catalog via ASF; --plan (dates + tracks), --validate
  remote_h5.py              reads a remote HDF5 over HTTP Range requests (only the blocks needed)
  process_gcov.py           GCOV HDF5 → 100 m mosaic, dB, classes (water/flooded forest/urban), WebP overlays, exposure
  validate_optical.py       validates radar water with Sentinel-2 (MNDWI) and flags hazy/smoky references
  detect_burn_scars.py      burn scars (HV drop) cross-checked with fires and a random control
  nowcast_fire.py           "Fire now": 48 h fires + 72 h weather forecast → risk, wind and smoke
  fetch_energy.py           hydropower (ONS: inflow, level, generation, capacity) + sun and aerosols (NASA POWER)
  render_solar.py           smooth solar relief map (contours) over the fire-view extent
  run_all.py                runs every step that needs no login
model/
  train_forecast.py         river, rainfall and fire forecasts; metrics, baselines, P10–P90 and feature importance
  fire_modulation.py        how much El Niño and low rivers push fire above its recent level
  energy_analysis.py        water → power at each plant, sun × smoke, and severe-drought risk for diesel supply
web/                        Vite + deck.gl + MapLibre + Chart.js app
  index.html                layout: people headline, El Niño cards, evidence cards, story card, glossary
  src/main.js               map layers, panels, story mode, definition balloons
  src/glossary.js           the 37 glossary entries (plain-language definitions)
  src/style.css             styles (desktop and phone)
  public/data/              JSON and WebP produced by the pipeline (served as data/)
  public/pitch/             the 7-slide project demo (HTML; ←/→ to navigate) and its PDF
  Dockerfile, nginx.conf    site image (Node build → nginx)
.github/workflows/pages.yml builds web/ and publishes it to GitHub Pages on every push that changes web/
docker-compose.yml          publishes the site; --profile https adds Caddy for the domain's HTTPS
deploy/Caddyfile            HTTPS reverse proxy (domain from the DOMAIN variable)
```

## How to run

### 1. Python (data and models)

```bash
python -m venv .venv
.venv/Scripts/python -m pip install -r requirements.txt
.venv/Scripts/python pipeline/run_all.py
```

### 2. NISAR radar (needs a free NASA Earthdata account)

Generate a token at https://urs.earthdata.nasa.gov (*Generate Token*) and store it in the `EARTHDATA_TOKEN` environment variable. **Never paste it into the code.** Then run:

```bash
.venv/Scripts/python pipeline/search_nisar.py --plan                      # picks dates and tracks (no download)
.venv/Scripts/python pipeline/process_gcov.py --aoi manaus --workers 4    # reads only what it needs, straight from ASF
.venv/Scripts/python pipeline/validate_optical.py --aoi manaus
.venv/Scripts/python pipeline/detect_burn_scars.py --aoi manaus
```

**Area and dates.** The radar covers the same area as the fire and energy views (study area ± 1°) on **every pass of the main track**: 10 dates for Manaus and 9 for Tefé, June to November 2026. A single pass covers 55–80% of that area. `--plan` fills the edges with passes from **other tracks no more than 12 days away** (up to 2 planned, plus reserve passes that `process_gcov.py` reads only while the *measured* coverage is below 98.5%, since footprint polygons overstate real coverage). Coverage reaches 95–97% for every Manaus date and 97% for Tefé from August on. Only HH acquisitions in the standard 20/40 MHz science modes are used: VV-only passes are skipped, and so are narrow 5 MHz modes, whose noise-floor stripes between sub-swaths would read as fake rivers. Pixels no pass covered are drawn hatched ("No radar coverage"). Each track gets its own water threshold, and band 3 of each GeoTIFF records which pass every pixel came from.

**Remote partial reads instead of downloads.** Each GCOV file is 2–13 GB. `process_gcov.py` opens the HDF5 over the network (`pipeline/remote_h5.py`, parallel HTTP *Range* requests) and reads only the HH and HV blocks inside the area. It uses **frequencyB (80 m)**, about 1/20 of the bytes of the 20 m band. We aggregate to 100 m anyway: in a Tefé test, water classified from frequencyB agreed with frequencyA at **F1 0.97**. When a scene has no frequencyB, the script reads A and averages 4×4. Result: **150–220 MB and about 2 minutes per date**, instead of a 5–13 GB download, and nothing raw stays on disk.

**Local dataset.** Each date becomes `~/spaceapps_data/nisar/<aoi>/<date>_100m.tif`, about 17 MB, kept outside the repository (int16 in dB×10: band 1 = HH, 2 = HV, 3 = source pass). The `manifest.json` next to it records the granules used, the catalog MD5, the frequency read, MB read and the GeoTIFF's sha256. From then on, the pipeline and app work without contacting ASF. To **only check** the dataset against the catalog, without downloading anything (verifies each granule's MD5 and lists new passes):

```bash
.venv/Scripts/python pipeline/search_nisar.py --validate
.venv/Scripts/python pipeline/process_gcov.py --aoi manaus --reingest   # only if the check reports reprocessing
```

**Classification:**
- **Open water:** the threshold is set per pass at the valley of the HH dB histogram. A filler pass with no clear water peak uses the main pass's threshold. Otsu is the last resort.
- **Flooded forest:** HH above `FLOODED_FOREST_DB` (−3 dB), from double bounce off water and trunks.
- **Urban:** bright pixels in WorldCover built-up areas are classed as "urban", because buildings double-bounce too.
- **Validation:** uses MNDWI (green − SWIR) rather than NDWI, because the sediment-laden Solimões reflects in the near-infrared.

### 3. River level (optional; the ANA service is often down)

Download the zip for station **14990000** from [HidroWeb](https://www.snirh.gov.br/hidroweb/serieshistoricas) (it has a reCAPTCHA, so this step is manual) and place it in `data/ana/`. The script fills recent years with ANA telemetry.

```bash
.venv/Scripts/python pipeline/fetch_ana.py --aoi manaus --csv data/ana/<station zip>
.venv/Scripts/python model/train_forecast.py --aoi manaus --target river --split 2010-12-01
```

The Tefé station (12900000) only has data up to 1994 and no telemetry, so it is not used.

### 4. Web app (development)

```bash
cd web
npm install
npm run dev
```

### 5. Publish

**GitHub Pages (the live demo).** `.github/workflows/pages.yml` builds `web/` and publishes it to https://ricardo-ramos1.github.io/pulsar-amazonia/ whenever a push to `main` changes `web/`. One-time setup: repository *Settings → Pages → Source: GitHub Actions*. To refresh the data online, re-run the pipeline locally and push.

**Your own domain (Docker).**

The site is static: the pipeline output in `web/public/data` is baked into the image. `docker-compose.yml` builds the app (Node) and serves it with nginx. The server needs no database, no keys and no Python.

```bash
docker compose up -d --build          # http://<server>:8080  (port: PORT in .env)
```

For a domain with **automatic HTTPS**, point the domain's DNS (A or AAAA record) to the server, open ports 80 and 443, and run:

```bash
cp .env.example .env                  # set DOMAIN=your.domain
docker compose --profile https up -d --build
```

Caddy issues and renews the certificate automatically and stores certificates in a persistent volume.

**To refresh the data** (for example after re-running "Fire now" or adding a new NISAR pass), run the pipeline locally and then `docker compose up -d --build`. Alternatively, uncomment the `./web/public/data` volume in `docker-compose.yml` so the server picks up new files without a rebuild.

The build also works without Docker on any static host (Netlify, Cloudflare Pages, GitHub Pages…). Paths are relative, so it even works from a sub-folder:

```bash
cd web && npm run build               # publish the web/dist folder
```

The live data is a snapshot of the last pipeline run. "Fire now" in particular only updates when it is re-run.

## Data sources

| Data | Source |
|---|---|
| NISAR L2 GCOV / GUNW | NASA-ISRO via [ASF](https://search.asf.alaska.edu/) · [guide](https://nisar-docs.asf.alaska.edu/) |
| ONI | [NOAA CPC](https://www.cpc.ncep.noaa.gov/data/indices/oni.ascii.txt) |
| Rainfall | [NASA POWER](https://power.larc.nasa.gov/) (PRECTOTCORR) |
| River stage | [ANA HidroWeb](https://www.snirh.gov.br/hidroweb/) |
| Population | [WorldPop](https://www.worldpop.org/) 2020, 1 km, UN-adjusted |
| Land cover | [ESA WorldCover](https://esa-worldcover.org/) 2021, 10 m |
| Optical (validation) | Sentinel-2 L2A via [Earth Search](https://earth-search.aws.element84.com/v1) |
| Fires | [NASA FIRMS](https://firms.modaps.eosdis.nasa.gov/) (MODIS for the long series; VIIRS + MODIS NRT for "Fire now") |
| Amazonas boundary | [IBGE malhas](https://servicodados.ibge.gov.br/api/docs/malhas) |
| Weather forecast | [Open-Meteo](https://open-meteo.com/) (ECMWF/GFS, hourly, 72 h) |
| Hydropower | [ONS open data](https://dados.ons.org.br/) (inflow, level and generation per plant) |
| Sun and aerosols | [NASA POWER](https://power.larc.nasa.gov/) (GHI and AOD at 550 nm, MERRA-2/CERES) |
| Terrain relief | [AWS Terrain Tiles](https://registry.opendata.aws/terrain-tiles/) (SRTM-derived) |

## Results: rivers

- **Radar (19 dates, study area ± 1°):** from June to November 2026, in the Manaus region, open water fell 26% (7,259 → 5,396 km²), flooded forest fell 97% (6,041 → 158 km²), and the people living by the water fell from 194,000 to 112,000. In Tefé, from August to October (same coverage), flooded forest fell 98% (4,044 → 82 km²).
- **Sentinel-2 validation (whole area):** F1 0.89 in Manaus (8 of 10 dates reliable) and 0.84 in Tefé (8 of 9). Within the original study area, F1 0.91 and 0.94. A date only counts if the optical image sees at least 70% of the permanent river (WorldCover) as water. Haze or smoke covered Manaus on Oct 21 and Nov 2, and Tefé on Oct 23.
- **Tefé caveat (Jun–Jul):** on those dates, frame 175 of the main track only came as frequencyA in `DHNA` mode, and its content is fill (~−54 dB). Those pixels are discarded (below −40 dB), so coverage drops to 60–72% even with four filler passes.
- **El Niño × La Niña:** comparing the Sep–Nov minimum at Manaus with the preceding Dec–Feb ONI (1951–2026, 76 years), the river bottoms out at **16.4 m after strong El Niños** (ONI ≥ 1.0, 10 years), 17.5 m in neutral years (26) and **19.4 m after strong La Niñas** (≤ −1.0, 9 years): about 3 m apart. Year by year the correlation is modest (Spearman −0.24), because weak events and the tropical Atlantic (as in 2023) also matter. Hence the label *clear in strong events*.
- **River model:** 3 months ahead, skill +0.25 over climatology, also beating persistence; ENSO is its second driver after the river's own memory. Forecast for Nov 2026: 17.2 m (normal: 18.7 m).

## Results: wildfire

- **Data:** MODIS fires (NASA FIRMS), 2000 to Nov 2026, within Amazonas state (IBGE boundary). Vegetation fires only, confidence ≥ 30. The standard product is used up to Aug 2026, then near-real-time data (API, with `FIRMS_MAP_KEY`). Only MODIS is used, so the series stays consistent (VIIRS starts in 2012); the Terra/Aqua split is unchanged in 2026.
- **A human baseline:** the Aug–Oct season went from about 10,000 fires (2000–2015) to 20,000–34,000 (2021–2024; the record was 2024). That makes "same as the previous 3 years" the hardest baseline to beat: skill +0.25, vs. +0.16 for the ML model.
- **Climate modulation** relative to the recent level (24 seasons): **low river at Manaus (minimum < 16.3 m) +39%**, other years −10%. The El Niño effect on its own is nearly zero (+1%). 2026 has a very strong El Niño (Jul–Sep ONI 2.14), yet the river only fell to 16.45 m and state-wide fire stayed at 0.55× the recent level. **What matters is drought that reaches the river, not the Pacific index alone.**
- **2026 around Manaus:** 3,500 fires, the second-highest year on record (after 2023).
- **Radar × fire:** across the wide Manaus area, the forest HV drop between Jun 23 and Nov 2, 2026 reveals **222 patches (34 km²)**. Only pixels seen by the main track on both dates are compared. **204 of them (92%) have a FIRMS fire within about 1.7 km, vs. 12% of randomly sampled forest points.** NISAR is seeing burn scars. Patches without a fire may be deforestation or understory fire. No patches appeared around Tefé (66 fires in the period).

```bash
.venv/Scripts/python pipeline/fetch_fires.py
.venv/Scripts/python model/fire_modulation.py
.venv/Scripts/python model/train_forecast.py --aoi amazonas --target fire --split 2015-12-01
.venv/Scripts/python pipeline/detect_burn_scars.py --aoi manaus
```

## Results: energy

- **Water → power:** at run-of-river plants, generation follows the river. At **Belo Monte** the inflow × generation correlation is 0.86, and in the dry season −10% water ≈ −12% power. In the 2024 drought, Belo Monte, Santo Antônio and Jirau generated **54% of normal** (2023: 66%; 2026: 90%).
- **Climate → water:** inflow to **Balbina**, which supplies Manaus, falls with El Niño (Spearman −0.68) and follows the Rio Negro minimum (+0.63). In Nov 2026 it is at **30% of normal**, though the reservoir still holds 62% of capacity. The Madeira, fed by the Andes, is normal.
- **Sun × smoke (NASA POWER):** about 4.7 kWh/m²/day. In the smokiest Aug–Oct months, irradiance is about 8% lower (correlation −0.58). This is an association, since clouds also vary.
- **River-borne diesel:** "severe drought" = an annual minimum among the lowest 10% (< 15.4 m at Manaus). Based on the river forecast, the chance in Nov 2026 is 2%.

```bash
.venv/Scripts/python pipeline/fetch_energy.py
.venv/Scripts/python pipeline/render_solar.py
.venv/Scripts/python model/energy_analysis.py
```

## Use of AI

In line with the NASA Space Apps rules on Super Intelligence (SI), we disclose that this project was built with an AI coding assistant (**Claude, by Anthropic**). The team chose the challenge, the focus (the Amazon, El Niño/La Niña and the people affected), the questions and the design direction, and reviewed every step. The assistant wrote most of the Python pipeline and the web application under our direction, helped debug the data (for example, NISAR frames delivered without valid measurements), ran and summarized validation and statistics, and drafted the English texts, documentation and demo slides. All numbers come from the open datasets listed above and were checked against the data. **No images, video or audio were generated by AI**: maps and charts are rendered from the data. Commits co-written with the assistant carry a `Co-Authored-By: Claude` line in their metadata.

## Roadmap

- [x] NISAR on 19 dates (Manaus 10, Tefé 9) over the wide area, with remote partial reads, urban mask and optical validation
- [x] Manaus river gauge 1902 → today (HidroWeb + telemetry) and a leak-free river-level model
- [x] Wildfire: FIRMS fires 2000 → Nov 2026, fire model, ENSO/river modulation and radar burn scars confirmed by fires
- [x] Docker deployment (nginx + automatic HTTPS with Caddy)
- [ ] More cities with the same setup (Parintins, Coari, Tabatinga), about 30 min of processing each
- [ ] Long Sentinel-1 series (2015 →) to cover the 2015 and 2023 El Niños
- [ ] Interferometry (GUNW) for ground deformation
- [x] A five-step "story" mode that follows the presentation script
