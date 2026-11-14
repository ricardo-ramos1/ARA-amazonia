"""Shot list for the 29.5 s PulSAR demo (30 fps). Cameras are MapLibre jumpTo options."""
W, H, FPS = 1920, 1080, 30


def cam(lon, lat, zoom, pitch=55, bearing=-20):
    return {'center': [lon, lat], 'zoom': zoom, 'pitch': pitch, 'bearing': bearing}


N_SCENES = 10  # Manaus radar dates (Jun 23 → Nov 2, 2026)

SHOTS = [
    # 0.0–5.0  intro: cinematic orbit over the Meeting of the Waters, radar on, UI hidden
    {'name': 'intro', 'mode': 'rios', 'clean': True, 'frames': 150, 'setup': {'#date': 0},
     'from': cam(-60.0, -3.12, 8.3, 62, -40), 'to': cam(-59.98, -3.10, 8.9, 58, -10)},
    # 5.0–10.0  the app itself, Rivers view, gentle push in
    {'name': 'app', 'mode': 'rios', 'frames': 150, 'setup': {'#date': 0},
     'from': cam(-59.98, -3.11, 7.72), 'to': cam(-59.98, -3.11, 8.0, 55, -12)},
    # 10.0–15.0  radar time-lapse Jun → Nov: the flooded forest drains as El Niño sets in
    {'name': 'timelapse', 'mode': 'rios', 'frames': 150,
     'sliders': {'#date': lambda t: min(N_SCENES - 1, int(t * 1.15 * N_SCENES))},
     'from': cam(-60.05, -3.12, 8.1, 50, -12), 'to': cam(-60.05, -3.12, 8.25, 52, -4)},
    # 15.0–17.5  Fire: FIRMS fires + radar burn scars
    {'name': 'fire', 'mode': 'fogo', 'frames': 75,
     'from': cam(-59.98, -3.11, 7.8, 55, -20), 'to': cam(-59.98, -3.11, 8.0, 55, -10)},
    # 17.5–20.0  Fire now: risk-coloured fires, wind and smoke over people
    {'name': 'firenow', 'mode': 'agora', 'frames': 75,
     'sliders': {'#hour': lambda t: int(t * 23)},
     'from': cam(-59.96, -3.36, 7.5, 55, -20), 'to': cam(-59.96, -3.36, 7.65, 55, -12)},
    # 20.0–25.0  Energy: hydropower plants and solar potential
    {'name': 'energy', 'mode': 'energia', 'frames': 150,
     'from': cam(-59.96, -3.36, 7.3, 45, -20), 'to': cam(-58.3, -5.2, 5.5, 50, -8)},
    # 25.0–29.5  end card background: wide, slow orbit, UI hidden
    {'name': 'outro', 'mode': 'rios', 'clean': True, 'frames': 135, 'setup': {'#date': N_SCENES - 1},
     'from': cam(-60.0, -3.12, 8.0, 50, 0), 'to': cam(-60.0, -3.12, 7.8, 55, 20)},
]
