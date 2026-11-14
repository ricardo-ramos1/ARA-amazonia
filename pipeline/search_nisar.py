"""Search (and optionally download) NISAR L2 products over each AOI via ASF (asf_search).

Search needs no login. Download needs a free NASA Earthdata account:
  set the env var EARTHDATA_TOKEN (generate at https://urs.earthdata.nasa.gov -> Generate Token)
  or EARTHDATA_USERNAME + EARTHDATA_PASSWORD. Never commit these.

Examples:
  python pipeline/search_nisar.py                       # catalog of GCOV/GUNW for every AOI
  python pipeline/search_nisar.py --aoi manaus --download --n 3
      -> downloads 3 GCOV dates (first, middle, last) from the AOI's preferred track (config/aois.json);
         scenes already in the local dataset (~/spaceapps_data/nisar/<aoi>/manifest.json) are NOT downloaded again
  python pipeline/search_nisar.py --validate
      -> NO download: checks the local dataset against the ASF catalogue (granule still published, same MD5,
         reprocessed since ingest?) and lists newer scenes on the same track

GCOV files are ~2–7 GB each, so pick few dates first.
"""
import argparse
import json
import os

import asf_search as asf

from common import DATA_RAW, NISAR_DATASET, WEB_DATA, bbox_wkt, load_aois, write_json

DATASET = NISAR_DATASET

LEVELS = ["GCOV", "GUNW"]


def search(aoi: dict, level: str):
    res = asf.search(dataset=asf.DATASET.NISAR, processingLevel=level,
                     intersectsWith=bbox_wkt(aoi["bbox"]), maxResults=2000)
    return sorted(res, key=lambda r: r.properties["startTime"])


def h5_size(r) -> int:
    return sum(v["bytes"] for k, v in (r.properties.get("bytes") or {}).items() if k.endswith(".h5"))


def row(r) -> dict:
    p = r.properties
    return {"date": p["startTime"][:10], "start": p["startTime"], "level": p["processingLevel"],
            "path": p["pathNumber"], "frame": p["frameNumber"], "dir": p["flightDirection"],
            "pol": p.get("polarization"), "file": p["fileName"], "url": p["url"],
            "gb": round(h5_size(r) / 1e9, 2), "browse": p.get("browse")}


def catalog_facts(r, file_name: str | None = None) -> dict:
    """Provenance facts from the ASF/CMR record: MD5 of the .h5 (from UMM), production time, track."""
    p = r.properties
    file_name = file_name or p.get("fileName")
    md5 = None
    for item in (r.umm.get("DataGranule", {}).get("ArchiveAndDistributionInformation") or []):
        if item.get("Name") == file_name and item.get("Checksum", {}).get("Algorithm") == "MD5":
            md5 = item["Checksum"]["Value"]
    return {"url": p.get("url"), "md5": md5, "path": p.get("pathNumber"), "frame": p.get("frameNumber"),
            "direction": p.get("flightDirection"),
            "production": r.umm.get("DataGranule", {}).get("ProductionDateTime"),
            "revision": r.meta.get("revision-date")}


def manifest(aoi_key: str) -> dict:
    p = DATASET / aoi_key / "manifest.json"
    return json.loads(p.read_text(encoding="utf-8")) if p.exists() else {"scenes": {}}


def validate(aoi_key: str, aoi: dict, gcov) -> None:
    """Compare the local dataset with the catalogue — no download."""
    man = manifest(aoi_key)["scenes"]
    print(f"  local dataset check ({len(man)} dates in {DATASET / aoi_key}):")
    if not man:
        print("    (empty) — run --plan and process_gcov.py")
    for date, rec in sorted(man.items()):
        grs = rec.get("granules", []) + rec.get("filler", [])
        names = [g["file"].removesuffix(".h5") for g in grs]
        now = {r.properties["fileName"]: catalog_facts(r) for r in asf.granule_search(names)} if names else {}
        gone = [g for g in grs if g["file"] not in now]
        changed = [g for g in grs if g["file"] in now and g.get("md5") and now[g["file"]]["md5"]
                   and g["md5"] != now[g["file"]]["md5"]]
        if gone:
            print(f"    ✗ {date}: {len(gone)} of {len(grs)} granules are no longer published on ASF")
        elif changed:
            print(f"    ⚠ {date}: {len(changed)} granule(s) reprocessed (MD5 changed) — "
                  "run --plan and process_gcov.py --reingest")
        else:
            print(f"    ✓ {date}: {len(grs)} granules match the catalog (MD5)"
                  + (f", filler from {rec['filler_date']}" if rec.get("filler_date") else ""))
    main = aoi["nisar_track"]["path"]
    last = max(man) if man else ""
    newer = sorted({r.properties["startTime"][:10] for r in gcov if r.properties["pathNumber"] == main
                    and r.properties["startTime"][:10] > last})
    print(f"  new passes on track {main} since {last or '—'}: " + (", ".join(newer) or "none")
          + (" — run --plan and process_gcov.py" if newer else ""))


MAX_GAP_DAYS = 12  # a filler track may be at most this far in time from the main pass


def _pick_frames(rs) -> list:
    """One granule per frame: prefer frequencyB with HH (light remote read; some A-only modes are mostly fill),
    then dual-pol (has HV), then the latest processing."""
    best = {}
    for r in rs:
        p = r.properties
        pol = p["fileName"].split("_")[9]
        key = (pol[2:] in ("DH", "SH"), "DH" in pol, p["fileName"])
        if p["frameNumber"] not in best or key > best[p["frameNumber"]][0]:
            best[p["frameNumber"]] = (key, r)
    return [v[1] for _, v in sorted(best.items())]


def _granule(r) -> dict:
    p = r.properties
    return {"file": p["fileName"], "frame": p["frameNumber"], "start": p["startTime"],
            "pol": p["fileName"].split("_")[9], **catalog_facts(r)}


def wide_plan(key: str, aoi: dict) -> dict:
    """Every date of the AOI's main track over the wide (±1°) radar grid, each completed by the
    other track that adds most coverage within MAX_GAP_DAYS. Nothing is downloaded."""
    import datetime as dt

    from shapely.geometry import box, shape
    from shapely.ops import unary_union

    from common import sar_grid
    wide = box(*sar_grid(aoi)[0])
    res = asf.search(dataset=asf.DATASET.NISAR, processingLevel="GCOV", intersectsWith=wide.wkt, maxResults=4000)
    # water/flooded-forest thresholds are HH-based: drop V-only acquisitions (DV/SV codes)
    # Standard science modes only: frequencyA at 20 or 40 MHz (bandwidth code "20xx"/"40xx"). Narrow 5 MHz
    # modes ("05xx", "00xx") showed noise-floor stripes between sub-swaths that the water mask read as rivers.
    has_hh = lambda pol: pol[:2] in ("DH", "SH") or pol[2:] in ("DH", "SH")  # noqa: E731
    science = lambda bw: bw[:2] in ("20", "40")  # noqa: E731
    res = [r for r in res if has_hh(r.properties["fileName"].split("_")[9])
           and science(r.properties["fileName"].split("_")[8])]
    groups = {}
    for r in res:
        p = r.properties
        groups.setdefault((p["pathNumber"], p["startTime"][:10]), []).append(r)
    cover = lambda rs: unary_union([shape(r.geometry) for r in rs]).intersection(wide)  # noqa: E731
    main = aoi["nisar_track"]["path"]
    day = dt.date.fromisoformat
    plan = {"bbox": list(wide.bounds), "main_path": main, "max_gap_days": MAX_GAP_DAYS, "dates": {}}
    for (path, date), rs in sorted(groups.items()):
        if path != main:
            continue
        prim = _pick_frames(rs)
        u = cover(prim)
        best = None
        for (p2, d2), rs2 in groups.items():
            gap = abs((day(d2) - day(date)).days)
            if p2 == main or gap > MAX_GAP_DAYS:
                continue
            uu = u.union(cover(rs2)).intersection(wide)
            score = (round(uu.area / wide.area, 2), -gap)
            if best is None or score > best[0]:
                best = (score, p2, d2, rs2)
        rec = {"primary": [_granule(r) for r in prim], "coverage_primary": round(u.area / wide.area, 3)}
        if best and best[0][0] > rec["coverage_primary"] + 0.02:
            rec.update(secondary=[_granule(r) for r in _pick_frames(best[3])], secondary_path=best[1],
                       secondary_date=best[2], coverage=best[0][0])
        else:
            rec["coverage"] = rec["coverage_primary"]
        plan["dates"][date] = rec
        print(f"  {date}: track {main} frames {[g['frame'] for g in rec['primary']]} {rec['coverage_primary']:.0%}"
              + (f" + track {rec['secondary_path']} {rec['secondary_date']} -> {rec['coverage']:.0%}"
                 if "secondary" in rec else ""))
    write_json(DATASET / key / "plan.json", plan)
    return plan


def user_env(name: str) -> str | None:
    """Env var from the process, or (Windows) the persistent user variables, so no restart is needed."""
    if os.getenv(name):
        return os.environ[name]
    try:
        import winreg
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, "Environment") as k:
            return winreg.QueryValueEx(k, name)[0]
    except (ImportError, OSError):
        return None


def session() -> asf.ASFSession:
    s = asf.ASFSession()
    if token := user_env("EARTHDATA_TOKEN"):
        # asf_search's auth_with_token() pre-check rejects valid EDL tokens; set the header directly.
        s.headers.update({"Authorization": f"Bearer {token.strip()}"})
        return s
    if os.getenv("EARTHDATA_USERNAME"):
        return s.auth_with_creds(os.environ["EARTHDATA_USERNAME"], os.environ["EARTHDATA_PASSWORD"])
    raise SystemExit("Set EARTHDATA_TOKEN (or EARTHDATA_USERNAME/EARTHDATA_PASSWORD) to read NISAR data.")


def pick(items: list, n: int) -> list:
    if len(items) <= n:
        return items
    idx = sorted({round(i * (len(items) - 1) / (n - 1)) for i in range(n)})
    return [items[i] for i in idx]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--aoi", help="one AOI (default: all)")
    ap.add_argument("--download", action="store_true")
    ap.add_argument("--n", type=int, default=3, help="number of GCOV dates to download (legacy full-file mode)")
    ap.add_argument("--validate", action="store_true", help="only check the local dataset against the catalog (no download)")
    ap.add_argument("--plan", action="store_true",
                    help="plan the wide mosaic (every date of the main track + a filler track)")
    a = ap.parse_args()

    aois = load_aois()
    if a.plan:
        for key, aoi in aois.items():
            if not a.aoi or key == a.aoi:
                print(f"plano NISAR amplo @ {key}")
                wide_plan(key, aoi)
        return
    catalog = {}
    for key, aoi in aois.items():
        if a.aoi and key != a.aoi:
            continue
        print(f"NISAR @ {key}")
        rows, gcov = [], []
        for level in LEVELS:
            res = search(aoi, level)
            print(f"  {level}: {len(res)} produtos")
            rows += [row(r) for r in res]
            if level == "GCOV":
                gcov = res
        catalog[key] = rows

        if a.validate:
            validate(key, aoi, gcov)

        if a.download:
            t = aoi["nisar_track"]
            same_track = [r for r in gcov if r.properties["pathNumber"] == t["path"]
                          and r.properties["frameNumber"] == t["frame"]]
            chosen = pick(same_track, a.n)
            out = DATA_RAW / "nisar" / key
            out.mkdir(parents=True, exist_ok=True)
            in_dataset = {g["file"] for rec in manifest(key)["scenes"].values()
                          for g in rec.get("granules", []) + rec.get("filler", [])}
            todo = [r for r in chosen if not (out / r.properties["fileName"]).exists()
                    and r.properties["fileName"] not in in_dataset]  # already processed: no need to re-download
            if len(todo) < len(chosen):
                print(f"  {len(chosen) - len(todo)} scene(s) already in the local dataset or downloaded — skipped")
            gb = sum(h5_size(r) for r in todo) / 1e9
            print(f"  downloading {len(todo)} GCOV (track {t['path']}/{t['frame']}, ~{gb:.1f} GB) to {out}")
            if todo:
                asf.ASFSearchResults(todo).download(path=str(out), session=session(), processes=2)

    write_json(WEB_DATA / "nisar_catalog.json", catalog)


if __name__ == "__main__":
    main()
