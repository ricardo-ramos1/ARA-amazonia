"""Smooth "topographic" solar-potential map for each AOI (same extent as the fire views: AOI ±1°).

NASA POWER's solar climatology is a 1° grid (~110 km) — drawn as squares it looks blocky. Here the
annual-mean GHI is interpolated (cubic) onto a fine grid and drawn as filled contour bands with
thin contour lines, like a relief map. The interpolation is VISUAL smoothing only: no new information.

Input : web/public/data/solar_grid.json (from fetch_energy.py)
Output: web/public/data/solar_<aoi>.png  + web/public/data/solar_<aoi>.json (bounds, levels, value grid)
Usage : python pipeline/render_solar.py
"""
import json

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402
import numpy as np  # noqa: E402
from matplotlib.colors import LinearSegmentedColormap  # noqa: E402
from scipy.interpolate import griddata  # noqa: E402

from common import WEB_DATA, load_aois, write_json  # noqa: E402

BUFFER_DEG = 1.0          # same as the fire views
MARGIN_DEG = 2.0          # extra source points around the box so the cubic fit has no edge artefacts
N = 400                   # output raster size (pixels per side)
STEP = 0.02               # contour spacing, kWh/m²/day (the field varies only ~0.1–0.3 inside an area)
RAMP = ["#3b0764", "#86198f", "#be185d", "#f97316", "#facc15", "#fef08a"]


def main():
    cells = np.array(json.loads((WEB_DATA / "solar_grid.json").read_text(encoding="utf-8"))["cells"])
    cmap = LinearSegmentedColormap.from_list("sun", RAMP)
    for key, aoi in load_aois().items():
        w, s, e, n = aoi["bbox"]
        w, s, e, n = w - BUFFER_DEG, s - BUFFER_DEG, e + BUFFER_DEG, n + BUFFER_DEG
        near = cells[(cells[:, 0] >= w - MARGIN_DEG) & (cells[:, 0] <= e + MARGIN_DEG) &
                     (cells[:, 1] >= s - MARGIN_DEG) & (cells[:, 1] <= n + MARGIN_DEG)]
        xs, ys = np.linspace(w, e, N), np.linspace(n, s, N)  # row 0 = north
        gx, gy = np.meshgrid(xs, ys)
        z = griddata(near[:, :2], near[:, 2], (gx, gy), method="cubic")
        z = np.where(np.isfinite(z), z, griddata(near[:, :2], near[:, 2], (gx, gy), method="nearest"))

        lo, hi = np.floor(z.min() / STEP) * STEP, np.ceil(z.max() / STEP) * STEP
        levels = np.round(np.arange(lo, hi + STEP / 2, STEP), 3)
        if len(levels) < 4:  # nearly flat field: still show a gentle gradient
            levels = np.linspace(z.min(), z.max(), 6)

        fig = plt.figure(figsize=(N / 100, N / 100), dpi=100)
        ax = fig.add_axes([0, 0, 1, 1])
        ax.set_axis_off()
        ax.contourf(gx, gy, z, levels=levels, cmap=cmap, alpha=0.9, antialiased=True)
        ax.contour(gx, gy, z, levels=levels, colors="white", linewidths=0.6, alpha=0.45)
        ax.set_xlim(w, e)
        ax.set_ylim(s, n)
        png = WEB_DATA / f"solar_{key}.png"
        fig.savefig(png, transparent=True)
        plt.close(fig)

        sub = z[::10, ::10]  # coarse copy of the values for the map tooltip
        write_json(WEB_DATA / f"solar_{key}.json", {
            "bounds": [round(v, 3) for v in (w, s, e, n)], "png": f"data/{png.name}",
            "min": round(float(z.min()), 3), "max": round(float(z.max()), 3), "step": STEP,
            "colors": RAMP, "grid": [[round(float(v), 3) for v in row] for row in sub],
            "source": "NASA POWER climatology (1° grid); cubic interpolation is visual only"})
        print(f"  {key}: {z.min():.2f}–{z.max():.2f} kWh/m²/dia, {len(levels) - 1} faixas, {len(near)} pontos POWER")


if __name__ == "__main__":
    main()
