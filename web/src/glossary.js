// Plain-language glossary of the technical terms used in the app, grouped by theme.
// Each entry: [term, short definition, aliases that should open it when clicked].
export const GLOSSARY = [
  ['Radar & satellites', [
    ['NISAR', 'NASA-ISRO Synthetic Aperture Radar, launched in 2025. It images the whole planet about every 12 days, day and night, through clouds and smoke.'],
    ['SAR (synthetic aperture radar)', 'A radar that sends its own microwave pulses and measures the echo. It does not need sunlight and sees through clouds — essential in the Amazon, which is cloudy most of the year.', ['SAR', 'radar']],
    ['L-band', 'Radar wavelength of about 24 cm. Long enough to pass through the forest canopy and reach the water or soil below — which is how NISAR sees flooded forest.'],
    ['Backscatter (dB)', 'How much of the radar pulse bounces back to the satellite, in decibels. Calm water acts like a mirror and returns little (very dark, below about −13 dB); forest returns more.', ['backscatter', 'dB']],
    ['HH / HV polarization', 'Orientation of the radar wave sent and received. HH (sent and received horizontal) separates water from land; HV (sent horizontal, received vertical) comes from leaves and branches, so it drops when forest burns.', ['HH', 'HV', 'polarization']],
    ['Open water', 'Rivers and lakes seen by the radar: pixels darker than a threshold computed for each satellite pass.'],
    ['Flooded forest', 'Forest standing in water (várzea / igapó). The radar wave bounces between water surface and tree trunks ("double bounce") and comes back very bright — only L-band radar sees this under the canopy.', ['flooded forest', 'double bounce']],
    ['Pass / track', 'One overflight of the satellite along a fixed orbit path. The same track repeats every 12 days; nearby tracks fill the edges of the map on dates close in time.', ['pass', 'track', 'passes']],
    ['No radar coverage', 'Areas no NISAR pass covered within 12 days of the date. They are shown hatched — never guessed.'],
    ['Sentinel-2 & MNDWI', 'Sentinel-2 is a European optical satellite (a "camera"). MNDWI is a water index from its green and short-wave infrared bands; we use it as an independent check of the radar water map.', ['Sentinel-2', 'MNDWI', 'optical']],
    ['F1 score', 'Agreement between the radar water map and the optical reference, from 0 (none) to 1 (perfect). 0.89 means the two maps mostly agree on where the water is.', ['F1']],
  ]],
  ['Climate', [
    ['El Niño / La Niña (ENSO)', 'Opposite phases of a natural cycle of the tropical Pacific Ocean (ENSO = El Niño–Southern Oscillation). El Niño warms the Pacific and usually brings less rain to the Amazon; La Niña usually brings more.', ['El Niño', 'La Niña', 'ENSO']],
    ['ONI (Oceanic Niño Index)', 'NOAA\'s official El Niño thermometer: Pacific sea-surface temperature anomaly over three months. Above +0.5 °C = El Niño, below −0.5 °C = La Niña; beyond ±1.0 °C is a strong event.', ['ONI']],
    ['Evidence label', 'How well our own data support an El Niño link: "strong" (clear year-to-year relation), "clear in strong events" (shows up mainly in big El Niño / La Niña years), "indirect" (acts through something else, such as drought reaching the river).', ['evidence']],
    ['Spearman ρ (rho)', 'Correlation between two variables from −1 to +1, based on ranks. ρ = −0.68 means that when one goes up the other tends to go down, fairly consistently.', ['ρ', 'Spearman', 'correlation']],
  ]],
  ['Rivers & forecasts', [
    ['River gauge (Rio Negro at Manaus)', 'Daily water level measured at the Manaus port since 1902 (ANA). Its yearly minimum is the drought indicator used here.', ['gauge', 'river level']],
    ['Climatology', 'The normal for each month: the historical average. The simplest forecast — "this month will look like an average year".', ['climatology', 'normal']],
    ['Persistence', 'A forecast that assumes things stay as they are now ("next month = this month").', ['persistence']],
    ['Skill', 'How much better a forecast is than climatology: 0 = no better than the average year, 1 = perfect, negative = worse. +0.25 means 25% less squared error than climatology.', ['skill']],
    ['Mean absolute error (MAE)', 'Average size of the forecast errors, in the units of the variable (meters of river, fires per month).', ['MAE', 'mean abs. error']],
    ['P10–P90', 'The forecast range: 80% of outcomes are expected between these two values.', ['P10', 'P90']],
    ['Out-of-sample test', 'The models are scored only on years they never saw while training, so the numbers reflect real forecasting skill.', ['out-of-sample']],
  ]],
  ['Fire & smoke', [
    ['FIRMS', 'NASA\'s Fire Information for Resource Management System: active fires detected from space by the MODIS and VIIRS sensors, within hours.', ['FIRMS', 'MODIS', 'VIIRS']],
    ['Fire radiative power (MW)', 'How much heat a fire emits, measured from space — a proxy for its intensity.', ['radiative power', 'FRP']],
    ['Burn scar (ΔHV)', 'Forest whose HV radar signal dropped more than 3 dB between two dates: leaves and branches were lost. 92% of these patches had a FIRMS fire nearby.', ['burn scar', 'burn scars', 'ΔHV']],
    ['Fire cluster', 'Fires detected less than about 2 km apart, grouped as one event.', ['cluster', 'clusters']],
    ['Angström index', 'A fire-weather index from air humidity and temperature: RH/20 + (27 − T)/10. Below 2.0 the air is very favorable to fire; above 4, fire is unlikely.', ['Angström']],
    ['Smoke path', 'A 40 km sector downwind of each fire cluster, using the mean wind forecast for the next 12 h (a 10 km circle when the wind is calm).', ['smoke path', 'downwind']],
    ['Nowcast', 'A same-day snapshot combining the last 48 h of fires with the next 72 h of weather forecast. Indicative only, not an official warning.', ['nowcast', 'Fire now']],
  ]],
  ['Energy', [
    ['Inflow', 'Water reaching a hydropower plant, in cubic meters per second (m³/s).', ['inflow']],
    ['Run-of-river plant', 'A hydropower plant with little or no reservoir: it generates with the water flowing past, so output falls quickly when the river is low (Belo Monte, Santo Antônio, Jirau).', ['run-of-river']],
    ['Storage reservoir', 'A dam that holds water for months (Balbina, Samuel), buffering a dry season — until inflow stays low for too long.', ['reservoir']],
    ['Capacity factor', 'Generation divided by installed capacity: 50% means the plant produced half of what it could at full power.', ['capacity']],
    ['GHI & AOD', 'GHI = sunlight reaching the ground (kWh/m² per day). AOD = aerosol optical depth, how much smoke and haze block the sunlight. Both from NASA POWER.', ['GHI', 'AOD']],
    ['Severe drought (diesel risk)', 'A yearly river minimum among the lowest 10% on record (below 15.4 m at Manaus). Then river transport is restricted and the diesel that powers isolated communities cannot get through.', ['severe drought', 'diesel']],
  ]],
  ['People & data', [
    ['WorldPop', 'Open population map (2020, 1 km cells) used to count people. "People by the water" = people in cells where at least 5% of the area is open water on that radar date.', ['WorldPop', 'people by the water']],
    ['1 km cell', 'Each 3D column is one square kilometer; its height is the number of people living there.', ['3D population', 'cell']],
  ]],
];
