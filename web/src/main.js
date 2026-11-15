import maplibregl from 'maplibre-gl';
import 'maplibre-gl/dist/maplibre-gl.css';
import { MapboxOverlay } from '@deck.gl/mapbox';
import { BitmapLayer, ColumnLayer, LineLayer, PathLayer, PolygonLayer, ScatterplotLayer, TextLayer } from '@deck.gl/layers';
import { Chart, registerables } from 'chart.js';
import './style.css';
import { GLOSSARY } from './glossary.js';

Chart.register(...registerables);
Chart.defaults.color = '#8b98a8';
Chart.defaults.borderColor = 'rgba(255,255,255,0.07)';
Chart.defaults.font.size = 11;

const BASEMAP = 'https://basemaps.cartocdn.com/gl/dark-matter-gl-style/style.json';
const FIRE_WINDOW_DAYS = 60;   // Fire mode: fires in the N days before the selected radar date
const FIRE_NEAR_KM = 3;        // a population cell is "near fire" if a fire is within this distance
const RISK = {
  'very high': [220, 38, 38], high: [249, 115, 22], moderate: [250, 204, 21], low: [107, 114, 128],
};
const MODES = ['rios', 'fogo', 'agora', 'energia'];
// internal mode ids -> URL hash shown to users
const HASH = { rios: 'rivers', fogo: 'fire', agora: 'fire-now', energia: 'energy' };
const modeFromHash = (h) => Object.keys(HASH).find((m) => HASH[m] === h || m === h);
const TARGETS = {
  rios: [['river', 'River level'], ['precip', 'Rainfall']],
  fogo: [['fire', 'Fires (Amazonas)']],
};

const $ = (id) => document.getElementById(id);
const fmt = (n, d = 0) => Number(n).toLocaleString('en-US', { maximumFractionDigits: d, minimumFractionDigits: d });
const pct = (x) => `${fmt(x * 100)}%`;
const tip = (html) => ({ html, style: { fontSize: '12px', maxWidth: '280px' } });

async function getJSON(path) {
  try {
    const r = await fetch(path);
    return r.ok ? await r.json() : null;
  } catch {
    return null;
  }
}

const state = {
  mode: 'rios', aoi: null, idx: 0, hour: 0,
  show: { sar: true, opt: false, pop: true, fire: true, scar: true, popf: true, nowFire: true, wind: true, smoke: true, popn: true,
    solar: true, plants: true },
  plant: 'Santo Antônio',
};
const charts = {};

// ---------------------------------------------------------------- scene images
// Radar/optical overlays are ~1–2 MB WebP each. The compressed files are cached (and the other dates preloaded in
// the background); only the current scene is decoded (a decoded 3333×3000 image is ~40 MB). A load that finishes
// after the user has moved to another date is dropped, so an old scene never lands on top of the current one.
const blobCache = new Map();
const getBlob = (url) => {
  if (!blobCache.has(url)) blobCache.set(url, fetch(url).then((r) => (r.ok ? r.blob() : null)).catch(() => null));
  return blobCache.get(url);
};

function loadSceneImages() {
  const sc = currentScene();
  state.img = { scene: sc };
  if (!sc) return;
  const wanted = [['sar', sc.png], ...(state.show.opt && sc.optical_png ? [['opt', sc.optical_png]] : [])];
  for (const [kind, url] of wanted) {
    getBlob(url)
      .then((b) => (b ? createImageBitmap(b) : null))
      .then((bm) => {
        if (!bm || state.img.scene !== currentScene() || state.img.scene !== sc) return; // stale: user moved on
        state.img[kind] = bm;
        render();
      });
  }
}

async function preloadScenes(aoi) {
  for (const sc of state.sar?.scenes ?? []) {
    if (state.aoi !== aoi) return; // switched area: stop
    await getBlob(sc.png);
  }
}

// ---------------------------------------------------------------- map
const map = new maplibregl.Map({
  container: 'map', style: BASEMAP, center: [-60.02, -3.1], zoom: 9, pitch: 55, bearing: -20, antialias: true,
  attributionControl: { compact: true },
});
map.addControl(new maplibregl.NavigationControl({ visualizePitch: true }), 'bottom-left');
// ?capture: hands the map to the demo-video recorder (submission/video) so it can drive the camera
if (new URLSearchParams(location.search).has('capture')) window.__pulsarMap = map;
if (import.meta.env.DEV) window.__map = map; // debugging aid in `npm run dev` only (stripped from the build)
const overlay = new MapboxOverlay({ interleaved: false, layers: [] });
map.addControl(overlay);

// Relief shading from AWS Terrain Tiles (public, no key; SRTM-derived over the Amazon). Drawn by MapLibre under
// the basemap labels, so deck.gl layers above stay aligned. Amazon is flat: exaggeration lifts terraces/valleys.
map.on('load', () => {
  // compact attribution starts expanded and covers the map on small screens: start it collapsed
  document.querySelector('.maplibregl-ctrl-attrib')?.classList.remove('maplibregl-compact-show');
  map.addSource('dem', {
    type: 'raster-dem', encoding: 'terrarium', tileSize: 256, maxzoom: 12,
    tiles: ['https://s3.amazonaws.com/elevation-tiles-prod/terrarium/{z}/{x}/{y}.png'],
    attribution: 'Relief: AWS Terrain Tiles (SRTM/NASA)',
  });
  const firstSymbol = map.getStyle().layers.find((l) => l.type === 'symbol')?.id;
  map.addLayer({
    id: 'hillshade', type: 'hillshade', source: 'dem',
    paint: {
      // dark basemap: shadows have little to darken, so the relief is read from the (cool) highlight side
      'hillshade-exaggeration': 1,
      'hillshade-illumination-direction': 315,
      'hillshade-shadow-color': 'rgba(0,0,0,0.9)',
      'hillshade-highlight-color': 'rgba(150,190,215,0.6)',
      'hillshade-accent-color': 'rgba(60,90,110,0.5)',
    },
  }, firstSymbol);
});

const currentScene = () => state.sar?.scenes?.[state.idx] ?? null;

function firesForScene() {
  const sc = currentScene();
  const rows = state.firePts?.rows;
  if (!sc || !rows) return [];
  const start = new Date(Date.parse(sc.date) - FIRE_WINDOW_DAYS * 864e5).toISOString().slice(0, 10);
  return rows.filter((r) => r[2] > start && r[2] <= sc.date);
}

/** number of fires within FIRE_NEAR_KM of each wide-population cell (spatial hash, ~O(n)) */
function fireProximity(cells, fires) {
  const g = 0.03;
  const hash = new Map();
  for (const f of fires) {
    const k = `${Math.floor(f[0] / g)},${Math.floor(f[1] / g)}`;
    if (!hash.has(k)) hash.set(k, []);
    hash.get(k).push(f);
  }
  return cells.map(([lon, lat]) => {
    const cx = Math.floor(lon / g); const cy = Math.floor(lat / g);
    let n = 0;
    for (let dx = -1; dx <= 1; dx++) {
      for (let dy = -1; dy <= 1; dy++) {
        for (const f of hash.get(`${cx + dx},${cy + dy}`) ?? []) {
          const km = Math.hypot((f[0] - lon) * 111.32 * Math.cos(lat * Math.PI / 180), (f[1] - lat) * 111.32);
          if (km <= FIRE_NEAR_KM) n++;
        }
      }
    }
    return n;
  });
}

const blue = (p) => { const t = Math.min(1, Math.log10(1 + p) / 4); return [60 + 120 * t, 90 + 60 * t, 200 + 40 * t, 190]; };

function popLayer(id, pop, colorFn, trigger) {
  return new ColumnLayer({
    id, data: pop.cells, diskResolution: 4, radius: pop.cell_deg * 111320 * 0.42, angle: 45, extruded: true,
    getPosition: (d) => [d[0], d[1]], getElevation: (d) => d[2], elevationScale: 0.6,
    getFillColor: (d, { index }) => colorFn(d, index), updateTriggers: { getFillColor: trigger }, pickable: true,
  });
}

function arrowSegments(arrows, k) {
  const segs = [];
  for (const a of arrows) {
    const s = a.speed[k]; const to = a.to[k];
    if (s == null || to == null) continue;
    const L = Math.min(0.35, 0.05 + 0.06 * s); // degrees
    const r = (to * Math.PI) / 180; const c = Math.cos((a.lat * Math.PI) / 180);
    const tip0 = [a.lon + (L * Math.sin(r)) / c, a.lat + L * Math.cos(r)];
    const head = (off) => {
      const rr = r + Math.PI + off;
      return [tip0[0] + (0.3 * L * Math.sin(rr)) / c, tip0[1] + 0.3 * L * Math.cos(rr)];
    };
    const color = s < 2 ? [148, 163, 184] : s < 4 ? [125, 211, 252] : [56, 189, 248];
    segs.push({ from: [a.lon, a.lat], to: tip0, color, s }, { from: tip0, to: head(0.45), color, s },
      { from: tip0, to: head(-0.45), color, s });
  }
  return segs;
}

function render() {
  const L = [];
  const sc = currentScene();
  const m = state.mode;
  const sh = state.show;

  if (m === 'rios') {
    const img = state.img?.scene === sc ? state.img : {}; // only images of the current scene
    if (sh.opt && img.opt) L.push(new BitmapLayer({ id: 'opt', image: img.opt, bounds: state.sar.bounds }));
    if (sh.sar && img.sar) L.push(new BitmapLayer({ id: 'sar', image: img.sar, bounds: state.sar.bounds, opacity: sh.opt ? 0.6 : 0.95 }));
    if (sh.pop && state.popWide) {
      const cells = sceneCells(sc);
      L.push(popLayer('pop', state.popWide, (d, i) => {
        const c = cells.get(i);
        return c && (c[0] > 0.05 || c[1] > 0.05) ? [245, 158, 11, 230] : blue(d[2]);
      }, [state.idx]));
    }
  }

  if (m === 'fogo') {
    if (sh.scar && state.scars) {
      L.push(new BitmapLayer({ id: 'scar', image: state.scars.png, bounds: state.scars.bounds }));
      L.push(new ScatterplotLayer({
        id: 'scar-pts', data: state.scars.patches, getPosition: (d) => [d.lon, d.lat],
        getRadius: (d) => 300 + Math.sqrt(d.km2) * 600, stroked: true, filled: false, lineWidthMinPixels: 2,
        getLineColor: (d) => (d.fires ? [255, 59, 31, 255] : [225, 29, 72, 200]), pickable: true,
      }));
    }
    if (sh.popf && state.popWide) {
      L.push(popLayer('popf', state.popWide, (d, i) => {
        const n = state.nearFire?.[i] ?? 0;
        if (!n) return blue(d[2]);
        const t = Math.min(1, n / 10);
        return [249, 115 - 60 * t, 22, 235];
      }, [state.idx]));
    }
    if (sh.fire) {
      L.push(new ScatterplotLayer({
        id: 'fires', data: firesForScene(), getPosition: (d) => [d[0], d[1]],
        getRadius: (d) => 250 + Math.sqrt(d[3]) * 120, radiusMinPixels: 2,
        getFillColor: [255, 59, 31, 210], getLineColor: [255, 210, 120, 255], stroked: true, lineWidthMinPixels: 0.5,
        updateTriggers: { data: [state.idx] }, pickable: true,
      }));
    }
  }

  if (m === 'agora' && state.now) {
    const nw = state.now;
    if (sh.smoke) {
      L.push(new PolygonLayer({
        id: 'smoke', data: nw.clusters.filter((c) => c.smoke), getPolygon: (c) => c.smoke,
        getFillColor: (c) => [...RISK[c.danger], 45], getLineColor: (c) => [...RISK[c.danger], 160],
        lineWidthMinPixels: 1, pickable: true,
      }));
    }
    if (sh.popn && state.popWide) {
      const down = new Set(nw.downwind_cells);
      L.push(popLayer('popn', state.popWide, (d, i) => (down.has(i) ? [192, 132, 252, 235] : blue(d[2])), [nw.generated_local]));
    }
    if (sh.wind) {
      L.push(new LineLayer({
        id: 'wind', data: arrowSegments(nw.arrows, state.hour), getSourcePosition: (d) => d.from,
        getTargetPosition: (d) => d.to, getColor: (d) => d.color, getWidth: 2.5, widthUnits: 'pixels',
        updateTriggers: { data: [state.hour] },
      }));
    }
    if (sh.nowFire) {
      L.push(new ScatterplotLayer({
        id: 'now-fires', data: nw.fires, getPosition: (d) => [d[0], d[1]], getRadius: 180, radiusMinPixels: 2,
        getFillColor: [255, 90, 40, 200], pickable: true,
      }));
      L.push(new ScatterplotLayer({
        id: 'clusters', data: nw.clusters, getPosition: (c) => [c.lon, c.lat],
        getRadius: (c) => 900 + Math.sqrt(c.frp) * 90, stroked: true, filled: false, lineWidthMinPixels: 3,
        getLineColor: (c) => [...RISK[c.danger ?? 'low'], 255], pickable: true,
      }));
    }
  }

  if (m === 'energia') {
    // smooth contour ("relief") map pre-rendered by pipeline/render_solar.py, same extent as the fire views
    if (sh.solar && state.solarMap) {
      L.push(new BitmapLayer({ id: 'solar', image: state.solarMap.png, bounds: state.solarMap.bounds, opacity: 0.8, pickable: true }));
    }
    if (sh.plants && state.energy) {
      const pl = state.energy.plants;
      const color = (p) => { const x = p.last.inflow_pct ?? 1; return x < 0.6 ? [220, 38, 38] : x < 0.85 ? [245, 158, 11] : [34, 197, 94]; };
      L.push(new ColumnLayer({
        id: 'plants', data: pl, diskResolution: 24, radius: 18000, extruded: true, pickable: true,
        getPosition: (p) => [p.lon, p.lat], getElevation: (p) => Math.sqrt(p.cap_mw), elevationScale: 1500, // sqrt: Belo Monte (11 GW) would dwarf the rest
        getFillColor: (p) => [...color(p), p.name === state.plant ? 255 : 200],
        getLineColor: [255, 255, 255], updateTriggers: { getFillColor: [state.plant] },
      }));
      L.push(new TextLayer({
        id: 'plant-names', data: pl, getPosition: (p) => [p.lon, p.lat], getText: (p) => p.name, getSize: 13,
        getColor: [230, 237, 243], getPixelOffset: [0, -18], fontWeight: 600,
        outlineWidth: 3, outlineColor: [5, 8, 13, 255], fontSettings: { sdf: true },
      }));
    }
  }

  overlay.setProps({ layers: L, getTooltip, onClick: showPick });
}

function getTooltip(info) {
  const { object, index, layer } = info;
  if (!object) return null;
  switch (layer?.id) {
    case 'fires': return tip(`🔥 MODIS fire on <b>${object[2]}</b><br/>radiative power ${fmt(object[3], 1)} MW`);
    case 'now-fires': return tip(`🔥 ${object[4].replace('_', ' ')} · ${object[3]} UTC<br/>radiative power ${fmt(object[2], 1)} MW`);
    case 'scar-pts': return tip(`<b>${object.label}</b><br/>${fmt(object.km2, 2)} km² · HV −${fmt(-object.dhv_db, 1)} dB
      ${object.fires ? `<br/>${object.fires} fire${object.fires > 1 ? 's' : ''} nearby` : ''}`);
    case 'clusters':
    case 'smoke': return tip(clusterHtml(object));
    case 'pop': {
      const c = sceneCells(currentScene()).get(index);
      return tip(`<b>${fmt(object[2])}</b> people/km²${c ? `<br/>open water ${pct(c[0])} · flooded forest ${pct(c[1])}` : ''}`);
    }
    case 'popf': return tip(`<b>${fmt(object[2])}</b> people/km²<br/>${fmt(state.nearFire?.[index] ?? 0)} fires within ${FIRE_NEAR_KM} km`);
    case 'popn': return tip(`<b>${fmt(object[2])}</b> people/km²${new Set(state.now.downwind_cells).has(index) ? '<br/>in the smoke path' : ''}`);
    case 'solar': {
      const g = state.solarMap?.grid;
      const uv = info.bitmap?.uv;
      if (!g || !uv) return null;
      const v = g[Math.min(g.length - 1, Math.floor(uv[1] * g.length))]?.[Math.min(g[0].length - 1, Math.floor(uv[0] * g[0].length))];
      return v == null ? null : tip(`☀️ <b>${fmt(v, 2)}</b> kWh/m²/day (annual mean, NASA POWER)`);
    }
    case 'plants': {
      const l = object.last;
      return tip(`<b>${object.name}</b> · ${object.river} River · ${fmt(object.cap_mw)} MW<br/>${object.note}<br/>
        ${l.date}: inflow at ${pct(l.inflow_pct)} of normal · generating ${pct(l.cf)} of capacity`);
    }
    default: return null;
  }
}

function clusterHtml(c) {
  if (c.danger == null) return `🔥 ${c.n} fires · ${fmt(c.frp)} MW`;
  const dir = DIRS[Math.round(c.wind_to / 45) % 8];
  return `<span class="risk" style="background:rgb(${RISK[c.danger]})">${c.danger}</span>
    <b>${c.n} fires</b> · ${fmt(c.frp)} MW<br/>
    min RH ${c.rh_min}% · max ${fmt(c.t_max, 0)} °C · Angström ${fmt(c.angstrom_min, 1)}<br/>
    wind ${c.calm ? 'calm' : `${fmt(c.wind_ms, 1)} m/s → ${dir}`} · 24 h rain ${fmt(c.rain24, 1)} mm
    ${c.people_downwind ? `<br/>${fmt(c.people_downwind)} people in the smoke path` : ''}`;
}

// ---------------------------------------------------------------- panels
function fillerText(dates) {
  if (!dates?.length) return '';
  const d = [...dates].sort().map((x) => x.slice(5));
  const span = d.length > 1 ? `${d[0]} → ${d.at(-1)}` : d[0];
  return ` · edges from ${d.length > 1 ? `${d.length} other passes` : 'another pass'} (${span})`;
}

function updateTimeline() {
  const scenes = state.sar?.scenes ?? [];
  const slider = $('date');
  slider.max = Math.max(0, scenes.length - 1);
  slider.disabled = !scenes.length;
  slider.value = state.idx;
  const sc = currentScene();
  $('date-label').textContent = sc ? `${sc.date} · scene ${state.idx + 1} of ${scenes.length}` : 'No processed NISAR scenes';
  if (!sc) { $('river-stats').innerHTML = ''; $('fire-stats').innerHTML = ''; return; }

  // rivers
  $('river-stats').innerHTML = `
    <div><b>${fmt(sc.water_km2)} km²</b>open water</div>
    <div><b>${fmt(sc.flooded_forest_km2)} km²</b>flooded forest</div>
    ${sc.coverage != null ? `<div class="wide-note" style="grid-column:span 2">radar covers <b style="display:inline;font-size:13px">${pct(sc.coverage)}</b> of the area${
      fillerText(sc.filler_dates)}</div>` : ''}`;
  const v = state.validation?.scenes.find((x) => x.sar_date === sc.date);
  $('validation').innerHTML = !v ? '' : v.ref_reliable === false
    ? `<span class="warn">⚠ Optical view blocked by haze/smoke</span> — the radar sees through it, Sentinel-2 does not.
       <span class="info" title="Sentinel-2 (${v.s2_images[0]?.slice(0, 10)}) recognizes only ${pct(v.ref_perm_water_detected)} of the permanent river as water, so it cannot be used as a reference on this date.">ⓘ</span>`
    : `✓ Validated against <span class="term" data-term="Sentinel-2">Sentinel-2</span>: <b class="term" data-term="F1">F1 ${fmt(v.f1, 2)}</b>
       <span class="info" title="Radar water vs. Sentinel-2 MNDWI (${v.s2_images[0]?.slice(0, 10)}). Precision ${pct(v.precision)}, recall ${pct(v.recall)}, cloud-free area ${pct(v.coverage)}.">ⓘ</span>`;

  // fire
  const f = firesForScene();
  $('fire-stats').innerHTML = `
    <div><b>${fmt(f.length)}</b>fires in the 60 days before</div>
    <div><b>${fmt(f.reduce((s, r) => s + r[3], 0) / 1000, 1)} GW</b>total radiative power</div>`;
  const s = state.scars;
  $('scar-line').innerHTML = s?.n_patches
    ? `🛰️ <b>${s.n_patches}</b> burn scars seen by radar (${s.from.slice(5)} → ${s.to.slice(5)}); <b>${pct(s.confirm_rate)}</b> have a
       FIRMS fire nearby, vs. ${pct(s.control_rate_random_forest)} for random forest points.
       <span class="info" title="Forest HV backscatter dropping more than 3 dB beyond the scene's normal change. Scars without a fire may be deforestation or understory fire.">ⓘ</span>`
    : s ? '🛰️ No burn scars detected by radar.' : '';
  updateHero();
}

function updateNowPanel() {
  const nw = state.now;
  if (!nw) { $('now-head').textContent = 'No nowcast yet — run pipeline/nowcast_fire.py'; $('now-stats').innerHTML = ''; return; }
  const counts = Object.fromEntries(Object.keys(RISK).map((k) => [k, nw.clusters.filter((c) => c.danger === k).length]));
  $('now-head').textContent = `Snapshot of ${nw.generated_local} (Manaus time)`;
  $('now-stats').innerHTML = `
    <div><b>${fmt(nw.n_fires)}</b>fires in 48 h</div>
    <div><b>${fmt(counts['very high'] + counts.high)}</b>clusters at high risk</div>`;
  updateHourLabel();
  updateHero();
  renderDock();
}

// ---------------------------------------------------------------- hero: the people affected, per view
const k = (n) => (n >= 1e6 ? `${fmt(n / 1e6, 1)} million` : n >= 1e4 ? `${fmt(n / 1e3, 0)} thousand` : fmt(n));

function updateHero() {
  const box = $('hero');
  if (!box) return;
  const sc = currentScene();
  let big = '', label = '', story = '';
  if (state.mode === 'rios' && sc) {
    big = k(sc.pop_near_water); label = 'people live by the water';
    const first = state.sar.scenes[0];
    story = sc === first
      ? `On ${sc.date} the radar sees ${fmt(sc.water_km2)} km² of open water and ${fmt(sc.flooded_forest_km2)} km² of flooded forest around them. Drag the date to watch the river fall.`
      : `On ${sc.date}: ${fmt(sc.flooded_forest_km2)} km² of flooded forest left — ${pct(1 - sc.flooded_forest_km2 / first.flooded_forest_km2)} less than in ${first.date.slice(0, 7)}.`;
  } else if (state.mode === 'fogo' && sc) {
    const near = peopleNearFire();
    big = k(near); label = `people within ${FIRE_NEAR_KM} km of a fire`;
    const s = state.scars;
    story = s?.n_patches
      ? `In the 60 days before ${sc.date}. The radar found ${s.n_patches} burn scars — ${pct(s.confirm_rate)} confirmed by fire satellites.`
      : `In the 60 days before ${sc.date}.`;
  } else if (state.mode === 'agora' && state.now) {
    const nw = state.now;
    big = k(nw.people_downwind); label = 'people in the smoke path now';
    const hi = nw.clusters.filter((c) => c.danger === 'very high' || c.danger === 'high').length;
    story = `${fmt(nw.n_fires)} fires in the last 48 h; ${hi} clusters face high fire weather in the next 24 h.`;
  } else if (state.mode === 'energia' && state.popWide) {
    big = k(state.popWide.total); label = 'people in this region depend on the river for power and fuel';
    const b = state.energy?.plants.find((p) => p.name === 'Balbina');
    story = b ? `Balbina, the reservoir that serves Manaus, is receiving ${pct(b.last.inflow_pct)} of its normal water (${b.last.date}).` : '';
  }
  box.innerHTML = big ? `<div class="hero-big">${big}</div><div class="hero-label">${label}</div><p class="hero-story">${story}</p>` : '';
}

// ---------------------------------------------------------------- click card: who lives here
function showPick(info) {
  const box = $('pick');
  const { object, index, layer } = info ?? {};
  if (!object || !['pop', 'popf', 'popn'].includes(layer?.id)) { box.hidden = true; return; }
  const dens = object[2];
  let line = '';
  if (layer.id === 'pop') {
    const c = sceneCells(currentScene()).get(index);
    line = c ? `${pct(c[0])} open water · ${pct(c[1])} flooded forest in this km² on ${currentScene().date}` : 'no water detected in this km² on this date';
  } else if (layer.id === 'popf') {
    const n = state.nearFire?.[index] ?? 0;
    line = n ? `${n} fire${n > 1 ? 's' : ''} within ${FIRE_NEAR_KM} km in the 60 days before ${currentScene().date}` : `no fire within ${FIRE_NEAR_KM} km`;
  } else {
    line = new Set(state.now.downwind_cells).has(index) ? 'in the smoke path of an active fire' : 'outside the forecast smoke path';
  }
  box.innerHTML = `<button class="pick-x" aria-label="Close">×</button>
    <div class="pick-big">~${fmt(dens)} <span>people in this km²</span></div>
    <div class="pick-line">${line}</div>
    <div class="muted">${object[1].toFixed(3)}, ${object[0].toFixed(3)} · WorldPop 2020</div>`;
  box.hidden = false;
  box.querySelector('.pick-x').onclick = () => { box.hidden = true; };
}

// ---------------------------------------------------------------- feature dock (cards over the map)
const DIRS = ['N', 'NE', 'E', 'SE', 'S', 'SW', 'W', 'NW'];

function renderDock() {
  const box = $('dock-cards');
  if (state.mode === 'agora') {
    const nw = state.now;
    const list = (nw?.clusters ?? []).slice(0, 12);
    $('dock-title').innerHTML = `Active fires now <span class="info" title="The ${list.length} largest fire clusters of the last 48 h, most intense first. Click one to fly to it.">ⓘ</span>`;
    $('dock-hint').textContent = list.length ? 'click a card to fly to the fire' : 'no fires in the last 48 h';
    box.innerHTML = list.map((c, i) => {
      const risk = c.danger ?? 'low';
      const wind = c.calm ? 'calm wind' : `wind ${fmt(c.wind_ms, 1)} m/s → ${DIRS[Math.round(c.wind_to / 45) % 8]}`;
      return `<button class="card ${state.selCluster === i ? 'sel' : ''}" data-i="${i}">
        <div class="top"><span class="risk" style="background:rgb(${RISK[risk]})">${risk}</span><span class="muted">${fmt(c.frp)} MW</span></div>
        <div class="big">${c.n} fires</div>
        <div class="meta">${c.people_downwind ? `👥 <b>${fmt(c.people_downwind)}</b> in the smoke<br/>` : ''}
          💨 ${wind}<br/>💧 min RH ${c.rh_min ?? '—'}% · rain ${fmt(c.rain24 ?? 0, 1)} mm</div>
      </button>`;
    }).join('');
    box.querySelectorAll('.card').forEach((el) => {
      el.onclick = () => {
        state.selCluster = +el.dataset.i;
        const c = list[state.selCluster];
        map.flyTo({ center: [c.lon, c.lat], zoom: 10.5, pitch: 50, duration: 1200 });
        renderDock();
      };
    });
  } else if (state.mode === 'energia') {
    const pl = state.energy?.plants ?? [];
    $('dock-title').innerHTML = `Amazon hydropower <span class="info" title="Inflow reaching each plant last month (the current month is partial) vs. the 2016–2022 normal for that month, and how much of its capacity it is generating. Click a card for the charts.">ⓘ</span>`;
    $('dock-hint').textContent = 'click a plant to see water × power';
    box.innerHTML = pl.map((p) => {
      const x = p.last.inflow_pct ?? 1;
      const col = x < 0.6 ? '#dc2626' : x < 0.85 ? '#f59e0b' : '#22c55e';
      return `<button class="card ${p.name === state.plant ? 'sel' : ''}" data-name="${p.name}">
        <div class="top"><span class="muted">${p.river} River</span><span class="muted">${fmt(p.cap_mw)} MW</span></div>
        <div class="name">${p.name}</div>
        <div class="big" style="color:${col}">${pct(x)}</div>
        <div class="meta">of normal inflow · generating ${pct(p.last.cf ?? 0)}</div>
        <div class="bar"><i style="width:${Math.min(100, x * 100)}%;background:${col}"></i></div>
      </button>`;
    }).join('');
    box.querySelectorAll('.card').forEach((el) => { el.onclick = () => selectPlant(el.dataset.name, true); });
  }
}

function updateHourLabel() {
  const t = state.now?.steps?.[state.hour];
  if (!t) return;
  const d = new Date(`${t}:00`);
  $('hour-label').textContent = `${d.toLocaleDateString('en-US', { weekday: 'short', day: '2-digit' })} ${t.slice(11, 16)} (+${state.hour * 3} h)`;
}

// ---------------------------------------------------------------- charts
function ensoBadge(oni) {
  const v = oni.oni.at(-1);
  const cls = v >= 0.5 ? 'nino' : v <= -0.5 ? 'nina' : 'neutral';
  const name = v >= 0.5 ? 'El Niño' : v <= -0.5 ? 'La Niña' : 'ENSO-neutral';
  const strength = Math.abs(v) >= 2 ? 'Very strong ' : Math.abs(v) >= 1.5 ? 'Strong ' : Math.abs(v) >= 1 ? 'Moderate ' : '';
  $('enso-now').className = `enso-chip ${cls}`;
  $('enso-now').innerHTML = `<b class="term" data-term="ENSO">${strength}${name}</b> now · <span class="term" data-term="ONI">ONI</span> ${v > 0 ? '+' : ''}${fmt(v, 2)} °C
    <span class="info" title="Oceanic Niño Index (NOAA), 3-month season centered on ${oni.dates.at(-1)}. Above +0.5 °C = El Niño, below −0.5 °C = La Niña.">ⓘ</span>`;
}

function ensoChart() {
  const { oni, river } = state;
  const i0 = oni.dates.findIndex((d) => d >= '2010-01');
  const labels = oni.dates.slice(i0);
  const last = river?.dates.at(-1);
  while (last && labels.at(-1) < last) {
    const [y, m] = labels.at(-1).split('-').map(Number);
    labels.push(m === 12 ? `${y + 1}-01` : `${y}-${String(m + 1).padStart(2, '0')}`);
  }
  const oniVals = labels.map((_, i) => oni.oni[i0 + i] ?? null);
  const rmap = river ? Object.fromEntries(river.dates.map((d, i) => [d, river.level[i]])) : {};
  charts.enso?.destroy();
  charts.enso = new Chart($('chart-enso'), {
    data: {
      labels,
      datasets: [
        { type: 'bar', label: 'ONI', data: oniVals, yAxisID: 'y',
          backgroundColor: oniVals.map((v) => (v >= 0.5 ? '#ef4444' : v <= -0.5 ? '#3b82f6' : '#4b5563')) },
        ...(river ? [{ type: 'line', label: 'Rio Negro (m)', data: labels.map((d) => rmap[d] ?? null), yAxisID: 'y2',
          borderColor: '#1ad9bf', borderWidth: 1.5, pointRadius: 0, tension: 0.3, spanGaps: true }] : []),
      ],
    },
    options: {
      animation: false, interaction: { mode: 'index', intersect: false },
      scales: { x: { ticks: { maxTicksLimit: 8 } }, y: { title: { display: true, text: 'ONI °C' } },
        y2: { display: !!river, position: 'right', grid: { display: false }, title: { display: true, text: 'm' } } },
      plugins: { legend: { labels: { boxWidth: 10 } } },
    },
  });
}

function forecastChart() {
  const fc = state.forecasts?.[$('target').value];
  $('fc-title').textContent = `${$('target').selectedOptions[0]?.textContent ?? 'ML'} forecast`;
  charts.fc?.destroy();
  if (!fc) { $('metrics').innerHTML = ''; $('fc-line').textContent = 'No model for this target.'; return; }
  const H = fc.horizons[$('horizon').value];
  const t = H.test;
  const n = 72;
  const sl = (a) => a.slice(-n);
  const live = H.live;
  const stale = live && (Date.now() - Date.parse(`${live.issued}-01`)) / 864e5 > 75;
  const showLive = live && !stale;
  const labels = sl(t.dates);
  if (showLive) labels.push(live.target_date);
  const pad = (a, v = null) => (showLive ? [...sl(a), v] : sl(a));
  charts.fc = new Chart($('chart-forecast'), {
    type: 'line',
    data: {
      labels,
      datasets: [
        { label: 'P90', data: pad(t.p90, live?.p90), borderWidth: 0, pointRadius: 0, fill: '+1', backgroundColor: 'rgba(26,217,191,0.15)' },
        { label: 'P10', data: pad(t.p10, live?.p10), borderWidth: 0, pointRadius: 0, fill: false },
        { label: 'Observed', data: pad(t.obs), borderColor: '#e6edf3', borderWidth: 1.5, pointRadius: 0 },
        { label: 'Model', data: pad(t.pred, live?.pred), borderColor: '#1ad9bf', borderWidth: 2,
          pointRadius: (c) => (showLive && c.dataIndex === labels.length - 1 ? 5 : 0) },
        { label: 'Climatology', data: pad(t.clim, live?.clim), borderColor: '#8b98a8', borderDash: [4, 4], borderWidth: 1, pointRadius: 0 },
      ],
    },
    options: {
      animation: false, interaction: { mode: 'index', intersect: false },
      scales: { x: { ticks: { maxTicksLimit: 7 } }, y: { title: { display: true, text: fc.unit } } },
      plugins: { legend: { labels: { boxWidth: 10, filter: (i) => !i.text.startsWith('P') } } },
    },
  });
  const rows = [['ML model', H.model], ['Climatology', H.climatology], ['Persistence', H.persistence]];
  if (fc.target === 'fire' && H.recent_years) rows.push(['Last 3 years', H.recent_years]);
  const best = Math.max(...rows.map((r) => r[1].skill_vs_clim));
  const dec = fc.target === 'fire' ? 0 : 2;
  $('metrics').innerHTML = `<tr><th></th><th class="term" data-term="MAE">Mean abs. error</th><th class="term" data-term="Skill">Skill</th></tr>
    ${rows.map(([name, m]) => `<tr><td>${name}</td><td>${fmt(m.mae, dec)} ${fc.unit}</td>
      <td class="${m.skill_vs_clim === best ? 'good' : ''}">${fmt(m.skill_vs_clim, 2)}</td></tr>`).join('')}`;
  const imp = H.importance ? Object.entries(H.importance).slice(0, 2).map(([g, v]) => `${g} ${pct(v)}`).join(', ') : '';
  $('fc-line').innerHTML = [
    showLive ? `Forecast for <b>${live.target_date}</b>: ${fmt(live.pred, dec || 1)} ${fc.unit} (normal ${fmt(live.clim, dec || 1)})` : '',
    imp ? `drivers: ${imp}` : '',
    fc.target === 'fire' ? '<span class="info" title="The fire baseline has risen since 2019 (deforestation). “Same as the last 3 years” is the hardest reference to beat; climate modulates that human baseline.">ⓘ human baseline</span>' : '',
  ].filter(Boolean).join(' · ');
}

function fireChart() {
  const reg = state.fires?.regions?.amazonas;
  charts.fire?.destroy();
  if (!reg) return;
  const years = {};
  reg.dates.forEach((d, i) => { const y = d.slice(0, 4); years[y] = (years[y] ?? 0) + reg.count[i]; });
  const labels = Object.keys(years).filter((y) => y >= '2003');
  const oniJAS = labels.map((y) => {
    const v = ['07', '08', '09'].map((m) => state.oni.oni[state.oni.dates.indexOf(`${y}-${m}`)]).filter((x) => x != null);
    return v.length ? v.reduce((a, b) => a + b, 0) / v.length : null;
  });
  charts.fire = new Chart($('chart-fire'), {
    type: 'bar',
    data: { labels, datasets: [{ label: 'Fires/year', data: labels.map((y) => years[y]),
      backgroundColor: oniJAS.map((v) => (v >= 0.5 ? '#ef4444' : v <= -0.5 ? '#3b82f6' : '#6b7280')) }] },
    options: { animation: false, plugins: { legend: { display: false } },
      scales: { x: { ticks: { maxTicksLimit: 12 } }, y: { title: { display: true, text: 'fires/year' } } } },
  });
  const m = state.fireMod;
  const p = (r) => `${r >= 1 ? '+' : '−'}${fmt(Math.abs(r - 1) * 100)}%`;
  $('fire-mod').innerHTML = m ? `<b>Low-river years: ${p(m.low_river)}</b> fire · El Niño years: ${p(m.el_nino)}
    <span class="info" title="Aug–Oct fire season vs. the mean of the previous 3 years (${m.n} seasons). Low river = Sep–Nov minimum below ${fmt(m.low_river_threshold_m, 1)} m at Manaus. Other years: ${p(m.other_river)}; La Niña: ${p(m.la_nina)}. A Pacific index alone does not mean fire — drought that reaches the river does.">ⓘ</span>` : '';
}

function nowChart() {
  const r = state.now?.region;
  charts.now?.destroy();
  if (!r) { $('now-line').textContent = ''; return; }
  const labels = r.time.map((t) => `${['Sun', 'Mon', 'Tue', 'Wed', 'Thu', 'Fri', 'Sat'][new Date(`${t}:00`).getDay()]} ${t.slice(11, 13)}h`);
  const flat = (v) => r.time.map(() => v);
  charts.now = new Chart($('chart-now'), {
    data: {
      labels,
      datasets: [
        { type: 'line', label: 'Angström', data: r.angstrom, yAxisID: 'y', borderColor: '#f97316', borderWidth: 2, pointRadius: 0, tension: 0.3 },
        { type: 'line', label: 'threshold 2.0', data: flat(2), yAxisID: 'y', borderColor: '#dc2626', borderDash: [4, 4], borderWidth: 1, pointRadius: 0 },
        { type: 'bar', label: 'Wind (m/s)', data: r.wind, yAxisID: 'y2', backgroundColor: 'rgba(125,211,252,.45)' },
        { type: 'bar', label: 'Rain (mm)', data: r.rain, yAxisID: 'y2', backgroundColor: 'rgba(59,130,246,.9)' },
      ],
    },
    options: {
      animation: false, interaction: { mode: 'index', intersect: false },
      scales: { x: { ticks: { maxTicksLimit: 7 } }, y: { title: { display: true, text: 'Angström' }, suggestedMin: 0, suggestedMax: 5 },
        y2: { position: 'right', grid: { display: false }, title: { display: true, text: 'm/s · mm' } } },
      plugins: { legend: { labels: { boxWidth: 10, filter: (i) => i.text !== 'threshold 2.0' } } },
    },
  });
  const dry = r.angstrom.filter((v) => v < 2).length;
  const rain = r.rain.reduce((a, b) => a + b, 0);
  $('now-line').innerHTML = `<b>${dry} h</b> very favorable to fire (<span class="term" data-term="Angström">Angström</span> &lt; 2) in the next 72 h · ${fmt(rain, 1)} mm of rain expected`;
}

// ---------------------------------------------------------------- energy
function energyPanel() {
  const e = state.energy;
  if (!e) { $('energy-stats').innerHTML = '<div style="grid-column:span 2">Run fetch_energy.py and energy_analysis.py</div>'; return; }
  const capGW = e.plants.reduce((s, p) => s + (p.cap_mw ?? 0), 0) / 1000;
  const hd = e.drought_dry_gen_vs_2019_2022 ?? {};
  const worst = Object.entries(hd).filter(([, v]) => v != null).sort((a, b) => a[1] - b[1])[0];
  const sol = state.energy.solar?.[state.aoi];
  const risk = e.diesel?.forecast?.find((f) => f.h === 1) ?? e.diesel?.forecast?.[0]; // next month is the actionable one
  $('energy-stats').innerHTML = `
    <div><b>${fmt(capGW, 1)} GW</b>of hydropower on the map</div>
    ${worst ? `<div><b>${pct(worst[1])}</b>of normal output in the ${worst[0]} drought</div>` : ''}`;
  void sol; // sun × smoke figures live in their own evidence card
  renderDock();
  updateHero();
  $('diesel-line').innerHTML = risk
    ? `⛽ Chance of a <b>severe drought</b> on the Rio Negro in ${risk.target_date}: <b>${pct(risk.prob_severe)}</b>
       <span class="info" title="Severe drought = ${e.diesel.definition}: below ${fmt(e.diesel.threshold_m, 1)} m. In such droughts river transport is restricted and the diesel that powers isolated communities cannot get through (2023, 2024). Probability from the river model's P10–P90 forecast.">ⓘ</span>`
    : '';
}

function selectPlant(name, fly = false) {
  state.plant = name;
  $('plant-sel').value = name;
  $('plant-name').textContent = name;
  renderDock();
  const p = state.energy?.plants.find((x) => x.name === name);
  if (fly && p) map.flyTo({ center: [p.lon, p.lat], zoom: 7.5, pitch: 50, bearing: -20, duration: 1200 });
  hydroChart();
  dryChart();
  render();
}

function hydroChart() {
  const s = state.energy?.series?.[state.plant];
  const p = state.energy?.plants.find((x) => x.name === state.plant);
  charts.hydro?.destroy();
  if (!s) return;
  charts.hydro = new Chart($('chart-hydro'), {
    data: {
      labels: s.dates,
      datasets: [
        { type: 'bar', label: 'Generation (MW)', data: s.gen_mw, yAxisID: 'y', backgroundColor: 'rgba(250,204,21,.75)' },
        { type: 'line', label: 'Inflow (m³/s)', data: s.inflow, yAxisID: 'y2', borderColor: '#38bdf8', borderWidth: 1.5, pointRadius: 0, tension: 0.3 },
        { type: 'line', label: 'Normal inflow', data: s.inflow_clim, yAxisID: 'y2', borderColor: '#64748b', borderDash: [4, 4], borderWidth: 1, pointRadius: 0 },
      ],
    },
    options: {
      animation: false, interaction: { mode: 'index', intersect: false },
      scales: { x: { ticks: { maxTicksLimit: 8 } }, y: { title: { display: true, text: 'MW' } },
        y2: { position: 'right', grid: { display: false }, title: { display: true, text: 'm³/s' } } },
      plugins: { legend: { labels: { boxWidth: 10, filter: (i) => i.text !== 'Normal inflow' } } },
    },
  });
  const link = state.energy.links?.[state.plant];
  $('hydro-line').innerHTML = p ? `Inflow × generation: <b>correlation ${fmt(p.corr_inflow_gen, 2)}</b>
    ${p.elasticity_dry != null ? ` · in the dry season, −10% water ≈ <b>−${fmt(Math.abs(p.elasticity_dry) * 10, 0)}%</b> power` : ''}
    ${link ? `<span class="info" title="Across years, Sep–Nov inflow follows the Rio Negro minimum at Manaus (Spearman ${fmt(link.corr_manaus_min, 2)}) and El Niño (Spearman ${fmt(link.corr_oni, 2)}; negative = El Niño reduces the water).">ⓘ</span>` : ''}` : '';
}

function dryChart() {
  const p = state.energy?.plants.find((x) => x.name === state.plant);
  charts.dry?.destroy();
  if (!p) return;
  const years = Object.keys(p.dry_cf_by_year);
  const oniY = years.map((y) => {
    const v = ['07', '08', '09'].map((m) => state.oni.oni[state.oni.dates.indexOf(`${y}-${m}`)]).filter((x) => x != null);
    return v.length ? v.reduce((a, b) => a + b, 0) / v.length : null;
  });
  charts.dry = new Chart($('chart-dry'), {
    data: {
      labels: years,
      datasets: [
        { type: 'bar', label: '% of capacity', data: years.map((y) => (p.dry_cf_by_year[y] ?? 0) * 100), yAxisID: 'y',
          backgroundColor: oniY.map((v) => (v >= 0.5 ? '#ef4444' : v <= -0.5 ? '#3b82f6' : '#6b7280')) },
        { type: 'line', label: 'water (% of normal)', data: years.map((y) => (p.dry_inflow_pct_by_year[y] ?? null) * 100), yAxisID: 'y',
          borderColor: '#38bdf8', pointRadius: 2, borderWidth: 1.5 },
      ],
    },
    options: { animation: false, interaction: { mode: 'index', intersect: false },
      scales: { y: { title: { display: true, text: '%' }, beginAtZero: true } },
      plugins: { legend: { labels: { boxWidth: 10 } } } },
  });
}

function solarChart() {
  const s = state.energy?.solar?.[state.aoi];
  charts.solar?.destroy();
  if (!s) { $('solar-line').textContent = ''; return; }
  const months = ['Jan', 'Feb', 'Mar', 'Apr', 'May', 'Jun', 'Jul', 'Aug', 'Sep', 'Oct', 'Nov', 'Dec'];
  charts.solar = new Chart($('chart-solar'), {
    data: {
      labels: months,
      datasets: [
        { type: 'bar', label: 'Sun (kWh/m²/day)', data: s.ghi_month_clim, yAxisID: 'y', backgroundColor: 'rgba(250,204,21,.75)' },
        { type: 'line', label: 'Smoke (AOD)', data: s.aod_month_clim, yAxisID: 'y2', borderColor: '#a8a29e', borderWidth: 2, pointRadius: 2 },
      ],
    },
    options: { animation: false, interaction: { mode: 'index', intersect: false },
      scales: { y: { title: { display: true, text: 'kWh/m²/day' }, beginAtZero: true },
        y2: { position: 'right', grid: { display: false }, title: { display: true, text: 'AOD' }, beginAtZero: true } },
      plugins: { legend: { labels: { boxWidth: 10 } } } },
  });
  $('solar-line').innerHTML = `The dry season brings more sun but also more smoke: in the smokiest Aug–Oct months
    irradiance drops <b>${fmt(s.smoke_loss_pct, 0)}%</b>
    <span class="info" title="Smokiest Aug–Oct months (top quartile of AOD anomaly) vs. the cleanest, over ${s.n_months} months: ${fmt(s.loss_per_0_1_aod_pct, 1)}% less sun per +0.1 AOD (correlation ${fmt(s.corr_ghi_aod, 2)}). Clouds also vary with smoke, so this is an association, not an isolated measurement of the effect.">ⓘ</span>`;
}

// ---------------------------------------------------------------- glossary
const norm = (t) => t.toLowerCase().normalize('NFD').replace(/[̀-ͯ]/g, '');
const gEntries = GLOSSARY.flatMap(([group, items]) => items.map(([term, def, aliases = []]) => ({
  group, term, def, id: `g-${norm(term).replace(/[^a-z0-9]+/g, '-')}`, keys: [term, ...aliases].map(norm),
})));

function renderGlossary(q = '') {
  const nq = norm(q.trim());
  const hit = (e) => !nq || e.keys.some((k) => k.includes(nq)) || norm(e.def).includes(nq);
  const groups = GLOSSARY.map(([g]) => [g, gEntries.filter((e) => e.group === g && hit(e))]).filter(([, es]) => es.length);
  $('g-body').innerHTML = groups.length ? groups.map(([g, es]) => `<h4>${g}</h4><dl>${es.map((e) =>
    `<div class="g-item" id="${e.id}"><dt>${e.term}</dt><dd>${e.def}</dd></div>`).join('')}</dl>`).join('')
    : '<p class="muted">No term found.</p>';
}

function openGlossary(term) {
  const dlg = $('glossary');
  $('g-search').value = '';
  renderGlossary();
  if (!dlg.open) dlg.showModal();
  const nt = term ? norm(term) : '';
  const e = nt && (gEntries.find((x) => x.keys.includes(nt)) ?? gEntries.find((x) => x.keys.some((k) => k.startsWith(nt))));
  if (e) {
    const el = document.getElementById(e.id);
    el.classList.add('flash');
    el.scrollIntoView({ block: 'center' });
    setTimeout(() => el.classList.remove('flash'), 1600);
  } else {
    $('g-body').scrollTop = 0;
    $('g-search').focus();
  }
}

// ---------------------------------------------------------------- story mode: a guided tour, people first
function peopleNearFire() {
  return state.nearFire && state.popWide
    ? state.popWide.cells.reduce((s, c, i) => s + (state.nearFire[i] ? c[2] : 0), 0) : 0;
}

function setScene(i) {
  state.idx = i; state.idxTouched = true;
  recomputeNearFire(); updateTimeline(); loadSceneImages();
}

const STORY = [
  {
    title: 'The pulse',
    go: () => { setScene(0); setMode('rios'); },
    text: () => {
      const s0 = state.sar.scenes[0];
      return `${s0.date.slice(0, 7)}: the Rio Negro is near its yearly peak. NISAR's L-band radar sees through clouds and
        under the canopy: <b>${fmt(s0.flooded_forest_km2)} km²</b> of forest standing in water around Manaus.
        <b>${k(s0.pop_near_water)} people</b> live right by that water.`;
    },
  },
  {
    title: 'The drought',
    go: () => { setScene(state.sar.scenes.length - 1); setMode('rios'); },
    text: () => {
      const s0 = state.sar.scenes[0]; const sL = state.sar.scenes.at(-1); const v = state.oni.oni.at(-1);
      return `${sL.date.slice(0, 7)}, under a very strong El Niño (ONI +${fmt(v, 2)} °C): the flooded forest shrank
        <b>${pct(1 - sL.flooded_forest_km2 / s0.flooded_forest_km2)}</b>. People living by the water fell from
        ${k(s0.pop_near_water)} to <b>${k(sL.pop_near_water)}</b> — the river moved away from their doors.
        Over 76 years, the river bottoms out ~3 m lower after strong El Niños than after strong La Niñas.`;
    },
  },
  {
    title: 'The fire',
    go: () => {
      setScene(state.sar.scenes.length - 1); setMode('fogo');
      const top = [...(state.scars?.patches ?? [])].sort((a, b) => b.km2 - a.km2).slice(0, 20);
      if (top.length) {
        const lon = top.reduce((a, p) => a + p.lon, 0) / top.length; const lat = top.reduce((a, p) => a + p.lat, 0) / top.length;
        map.flyTo({ center: [lon, lat], zoom: 8.6, pitch: 50, bearing: -15, duration: 1600 });
      }
    },
    text: () => {
      const sc = state.scars;
      return `Dry forest burns. In the 60 days before ${currentScene().date}, <b>${k(peopleNearFire())} people</b> lived
        within ${FIRE_NEAR_KM} km of a fire. The radar found <b>${sc?.n_patches ?? 0} burn scars</b> —
        ${pct(sc?.confirm_rate ?? 0)} confirmed by fire satellites (only ${pct(sc?.control_rate_random_forest ?? 0)} by chance).
        El Niño alone doesn't explain fire; drought reaching the river does (+39%).`;
    },
  },
  {
    title: 'The smoke, today',
    go: () => {
      setMode('agora');
      const c = state.now?.clusters?.[0];
      if (c) map.flyTo({ center: [c.lon, c.lat], zoom: 9.2, pitch: 50, bearing: -15, duration: 1600 });
    },
    text: () => {
      const nw = state.now;
      return nw ? `Right now (${nw.generated_local}): <b>${k(nw.people_downwind)} people</b> are in the smoke path of
        ${fmt(nw.n_fires)} fires. Slide the forecast hour to see where the wind takes it over the next 72 h.` : 'No nowcast available.';
    },
  },
  {
    title: 'The power',
    go: () => { setMode('energia'); selectPlant('Balbina', true); },
    text: () => {
      const b = state.energy?.plants.find((p) => p.name === 'Balbina');
      return `<b>${k(state.popWide?.total ?? 0)} people</b> in this region depend on the river for power and fuel.
        Balbina, the reservoir that serves Manaus, gets <b>${pct(b?.last.inflow_pct ?? 0)}</b> of its normal water —
        its inflow falls with El Niño (ρ = −0.68). When the river is low, diesel barges can't reach isolated towns.`;
    },
  },
];

async function storyGo(i) {
  const box = $('story-card');
  if (i < 0 || i >= STORY.length) { box.hidden = true; state.story = null; return; }
  if (state.aoi !== 'manaus') { $('aoi').value = 'manaus'; await selectAoi('manaus'); }
  state.story = i;
  $('pick').hidden = true;
  if (innerWidth < 900) { document.body.scrollTo?.({ top: 0, behavior: 'smooth' }); window.scrollTo({ top: 0, behavior: 'smooth' }); }
  STORY[i].go();
  const last = i === STORY.length - 1;
  box.innerHTML = `<button class="pick-x" aria-label="Close story">×</button>
    <div class="story-step">Step ${i + 1} of ${STORY.length}</div>
    <h3>${STORY[i].title}</h3>
    <p>${STORY[i].text()}</p>
    <div class="story-nav">
      <button class="ghost" ${i === 0 ? 'disabled' : ''} data-go="${i - 1}">◀ Back</button>
      <span class="dots">${STORY.map((_, j) => `<i class="${j === i ? 'on' : ''}"></i>`).join('')}</span>
      <button class="primary" data-go="${last ? -1 : i + 1}">${last ? 'Explore on your own' : 'Next ▶'}</button>
    </div>`;
  box.hidden = false;
  box.querySelector('.pick-x').onclick = () => storyGo(-1);
  box.querySelectorAll('[data-go]').forEach((b) => { b.onclick = () => storyGo(+b.dataset.go); });
}

// ---------------------------------------------------------------- modes & AOI
function setMode(mode) {
  if (!MODES.includes(mode)) mode = 'rios';
  state.mode = mode;
  document.body.dataset.mode = mode;
  if (location.hash.slice(1) !== HASH[mode]) history.replaceState(null, '', `#${HASH[mode]}`);
  // fire season is Aug–Nov: unless the user picked a date, the fire view opens on the latest radar date
  const nScenes = state.sar?.scenes?.length ?? 0;
  if (mode === 'fogo' && !state.idxTouched && nScenes && state.idx !== nScenes - 1) {
    state.idx = nScenes - 1;
    recomputeNearFire();
    updateTimeline();
    loadSceneImages();
  }
  document.querySelectorAll('.tabs button').forEach((b) => b.classList.toggle('active', b.dataset.tab === mode));
  const sel = $('target');
  if (TARGETS[mode]) {
    const keep = sel.value;
    sel.innerHTML = TARGETS[mode].map(([v, l]) => `<option value="${v}" ${state.forecasts?.[v] ? '' : 'disabled'}>${l}</option>`).join('');
    const ok = TARGETS[mode].map(([v]) => v).filter((v) => state.forecasts?.[v]);
    sel.value = ok.includes(keep) ? keep : ok[0] ?? TARGETS[mode][0][0];
    forecastChart();
  }
  renderDock();
  updateHero();
  $('pick').hidden = true;
  fitView();
  render();
}

function fitView() {
  const aoi = state.aois[state.aoi];
  let [w, s, e, n] = aoi.bbox;
  [w, s, e, n] = [w - 1, s - 1, e + 1, n + 1]; // every tab shares the AOI ±1° view
  // on desktop the side panels float over the map: keep the area of interest in the visible middle
  const wide = innerWidth > 1100;
  const dockOn = state.mode === 'agora' || state.mode === 'energia'; // leave room for the card dock
  const padding = wide ? { left: 420, right: 500, top: 90, bottom: dockOn ? 210 : 60 } : 16;
  // portrait phones: a strong tilt squeezes the area into the middle third of the map
  const pitch = !wide && innerWidth < 900 ? 35 : state.mode === 'energia' ? 45 : 55;
  // fitBounds is imprecise with a tilted camera: frame the area flat, then tilt around the same centre
  const cam = map.cameraForBounds([[w, s], [e, n]], { padding, bearing: 0, pitch: 0 });
  const bearing = !wide && innerWidth < 900 ? 0 : -20; // a rotated rectangle overflows a narrow screen
  if (cam) map.flyTo({ center: cam.center, zoom: cam.zoom - 0.15, pitch, bearing, duration: 1000 });
}

// radar water / flooded-forest fractions per wide 1 km population cell (sparse [index, water, flooded] -> Map)
function sceneCells(sc) {
  if (!sc) return new Map();
  sc.cellMap ??= new Map((sc.cells ?? []).map(([i, w, f]) => [i, [w, f]]));
  return sc.cellMap;
}

function recomputeNearFire() {
  state.nearFire = state.popWide ? fireProximity(state.popWide.cells, firesForScene()) : null;
}

async function selectAoi(key) {
  state.aoi = key;
  state.idx = 0;
  state.idxTouched = false;
  $('aoi').title = state.aois[key].story;
  const g = (p) => getJSON(`data/${p}`);
  const [pop, popWide, sar, river, fcPrecip, fcRiver, validation, firePts, scars, now] = await Promise.all([
    g(`population_${key}.json`), g(`population_${key}_wide.json`), g(`sar_${key}.json`), g(`river_${key}.json`),
    g(`forecast_${key}_precip.json`), g(`forecast_${key}_river.json`), g(`validation_${key}.json`),
    g(`fires_points_${key}.json`), g(`scars_${key}.json`), g(`nowcast_${key}.json`),
  ]);
  state.solarMap = await g(`solar_${key}.json`);
  if (state.solarMap && $('solar-legend')) {
    $('solar-legend').innerHTML = `<i style="background:linear-gradient(90deg,${state.solarMap.colors.join(',')})"></i>
      Sun ${fmt(state.solarMap.min, 2)}–${fmt(state.solarMap.max, 2)} kWh/m²/day`;
  }
  Object.assign(state, { pop, popWide, sar, river, validation, firePts, scars, now,
    forecasts: { precip: fcPrecip, river: fcRiver, fire: state.fcFire } });
  state.hour = 0;
  state.selCluster = null;
  $('hour').max = Math.max(0, (now?.steps?.length ?? 1) - 1);
  $('hour').value = 0;
  recomputeNearFire();
  loadSceneImages();
  preloadScenes(key);
  ensoChart();
  updateTimeline();
  updateNowPanel();
  nowChart();
  energyPanel();
  solarChart();
  if (state.energy) { $("plant-sel").value = state.plant; hydroChart(); dryChart(); }
  setMode(state.mode);
}

async function init() {
  const g = (p) => getJSON(`data/${p}`);
  const [aois, oni, fires, fcFire, fireMod, energy, solarGrid] = await Promise.all([
    g('aois.json'), g('oni.json'), g('fires.json'), g('forecast_amazonas_fire.json'), g('fire_modulation.json'),
    g('energy.json'), g('solar_grid.json'),
  ]);
  Object.assign(state, { aois, oni, fires, fcFire, fireMod, energy, solarGrid });
  if (energy) {
    $('plant-sel').innerHTML = energy.plants.map((p) => `<option>${p.name}</option>`).join('');
    $('plant-sel').onchange = (e) => selectPlant(e.target.value, true);
    $('plant-name').textContent = state.plant;
  }
  ensoBadge(oni);
  fireChart();
  $('aoi').innerHTML = Object.entries(aois).map(([k, a]) => `<option value="${k}">${a.name}</option>`).join('');

  $('aoi').onchange = (e) => selectAoi(e.target.value);
  document.querySelectorAll('.tabs button').forEach((b) => { b.onclick = () => setMode(b.dataset.tab); });
  $('date').oninput = (e) => {
    state.idx = +e.target.value; state.idxTouched = true;
    recomputeNearFire(); updateTimeline(); loadSceneImages(); render();
  };
  $('hour').oninput = (e) => { state.hour = +e.target.value; updateHourLabel(); render(); };
  $('horizon').onchange = forecastChart;
  $('target').onchange = forecastChart;
  const toggles = { 't-sar': 'sar', 't-opt': 'opt', 't-pop': 'pop', 't-fire': 'fire', 't-scar': 'scar', 't-popf': 'popf',
    't-now-fire': 'nowFire', 't-wind': 'wind', 't-smoke': 'smoke', 't-popn': 'popn', 't-solar': 'solar', 't-plants': 'plants' };
  for (const [id, k] of Object.entries(toggles)) {
    $(id).onchange = (e) => {
      state.show[k] = e.target.checked;
      if (k === 'opt' && e.target.checked) loadSceneImages(); // optical is only fetched when asked for
      render();
    };
  }

  // evidence cards: a chart drawn while its card was closed has no size, so resize it when the card opens
  document.querySelectorAll('details.chart-card').forEach((d) => {
    d.addEventListener('toggle', () => { if (d.open) Object.values(charts).forEach((c) => c?.resize()); });
  });
  window.addEventListener('hashchange', () => setMode(modeFromHash(location.hash.slice(1))));
  $('story-btn').onclick = () => storyGo(0);
  $('glossary-btn').onclick = () => openGlossary();
  $('g-close').onclick = () => $('glossary').close();
  $('g-search').oninput = (e) => renderGlossary(e.target.value);
  $('glossary').addEventListener('click', (e) => { if (e.target === $('glossary')) $('glossary').close(); }); // backdrop
  // any underlined term (static or generated text) opens its glossary entry
  document.addEventListener('click', (e) => {
    const t = e.target.closest('[data-term]');
    if (!t || t.closest('#glossary')) return;
    e.preventDefault();
    openGlossary(t.dataset.term);
  });
  document.addEventListener('keydown', (e) => {
    if (state.story == null) return;
    if (e.key === 'ArrowRight') storyGo(state.story + 1 < STORY.length ? state.story + 1 : state.story);
    if (e.key === 'ArrowLeft') storyGo(Math.max(0, state.story - 1));
    if (e.key === 'Escape') storyGo(-1);
  });
  state.mode = modeFromHash(location.hash.slice(1)) ?? 'rios';
  await selectAoi(Object.keys(aois)[0]);
}

init();
