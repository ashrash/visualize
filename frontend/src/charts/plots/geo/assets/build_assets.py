"""Regenerate this directory's bundled offline maps from Natural Earth (public domain,
naturalearthdata.com).

Not part of the shipped app: nothing under `frontend/src` imports this file, and it needs
`uv run python` (not the browser) to run. Kept here, next to its own output, so the assets can
be regenerated later (a resolution change, a newer Natural Earth release, more countries'
admin-1 layers) without anyone having to reverse-engineer the simplification from the JSON.

Usage (from the repo root):
    uv run python frontend/src/charts/plots/geo/assets/build_assets.py

Downloads the four source layers (admin-0 countries and admin-1 US states, at 1:110m and
1:50m) to a temporary directory, simplifies their geometry (Douglas-Peucker plus coordinate
rounding) and writes four small GeoJSON files next to this script.
"""

from __future__ import annotations

import json
import tempfile
import urllib.request
from pathlib import Path

OUT = Path(__file__).parent

_BASE = "https://raw.githubusercontent.com/nvkelso/natural-earth-vector/master/geojson"
_SOURCES = {
    "ne_110m_admin_0_countries": f"{_BASE}/ne_110m_admin_0_countries.geojson",
    "ne_50m_admin_0_countries": f"{_BASE}/ne_50m_admin_0_countries.geojson",
    "ne_110m_admin_1_states_provinces": f"{_BASE}/ne_110m_admin_1_states_provinces.geojson",
    "ne_50m_admin_1_states_provinces": f"{_BASE}/ne_50m_admin_1_states_provinces.geojson",
}
# Natural Earth's missing-value sentinel. It shows up as a literal string in ISO_A3, ISO_A2
# and their *_EH ("including exceptions") variants for disputed or unrecognized territories
# (Northern Cyprus, Somaliland, ...). It must never become a join key: a dataset that uses
# "-99" as its own missing-value sentinel for a region column would otherwise match it and
# get silently (and wrongly) colored in.
MISSING = "-99"


def download(name: str, url: str, dest: Path) -> Path:
    path = dest / f"{name}.geojson"
    print(f"fetching {name}")
    urllib.request.urlretrieve(url, path)  # noqa: S310 (a fixed, hardcoded github URL)
    return path


def rdp(points: list[list[float]], epsilon: float) -> list[list[float]]:
    """Douglas-Peucker simplification of a single ring/line (list of [lon, lat])."""
    if len(points) < 3:
        return points
    start, end = points[0], points[-1]
    dx, dy = end[0] - start[0], end[1] - start[1]
    norm = (dx * dx + dy * dy) ** 0.5
    best_idx, best_dist = 0, -1.0
    for i in range(1, len(points) - 1):
        px, py = points[i][0] - start[0], points[i][1] - start[1]
        dist = abs(px * dy - py * dx) / norm if norm > 1e-12 else (px * px + py * py) ** 0.5
        if dist > best_dist:
            best_idx, best_dist = i, dist
    if best_dist > epsilon:
        left = rdp(points[: best_idx + 1], epsilon)
        right = rdp(points[best_idx:], epsilon)
        return left[:-1] + right
    return [start, end]


def round_coords(points: list[list[float]], ndigits: int) -> list[list[float]]:
    return [[round(p[0], ndigits), round(p[1], ndigits)] for p in points]


def simplify_ring(ring: list[list[float]], epsilon: float, ndigits: int) -> list[list[float]]:
    simplified = round_coords(rdp(ring, epsilon), ndigits)
    deduped: list[list[float]] = []
    for p in simplified:
        if not deduped or deduped[-1] != p:
            deduped.append(p)
    if len(deduped) < 4:
        return round_coords(ring, ndigits)  # too small to simplify further; keep as-is (rounded)
    if deduped[0] != deduped[-1]:
        deduped.append(deduped[0])
    return deduped


def simplify_geometry(geom: dict, epsilon: float, ndigits: int) -> dict:
    kind = geom["type"]
    if kind == "Polygon":
        rings = [simplify_ring(r, epsilon, ndigits) for r in geom["coordinates"]]
        return {"type": kind, "coordinates": rings}
    if kind == "MultiPolygon":
        polys = [
            [simplify_ring(r, epsilon, ndigits) for r in poly] for poly in geom["coordinates"]
        ]
        return {"type": kind, "coordinates": polys}
    raise ValueError(f"unexpected geometry type {kind}")


def slugify(name: str) -> str:
    return "".join(c.upper() if c.isalnum() else "_" for c in name).strip("_")


def _clean(value: str | None) -> str:
    """A property value, or "" for Natural Earth's "-99" missing-value sentinel."""
    if not value or value == MISSING:
        return ""
    return value


def build_countries(src_path: Path, epsilon: float, ndigits: int) -> dict:
    raw = json.loads(src_path.read_text(encoding="utf-8"))
    features = []
    seen_codes: set[str] = set()
    for f in raw["features"]:
        props = f["properties"]
        # ISO_A3 is "-99" for several sovereign states with disputed status elsewhere (France,
        # Norway, ...); ADM0_A3 and ISO_A3_EH ("including exceptions") carry their real code.
        # A few genuinely contested territories (Northern Cyprus, Somaliland) have no real ISO
        # code anywhere and fall back to Natural Earth's own admin-0 code.
        iso3 = _clean(props.get("ISO_A3")) or _clean(props.get("ADM0_A3"))
        iso3 = iso3 or _clean(props.get("ISO_A3_EH")) or slugify(props.get("NAME") or "")
        code = iso3.upper()
        if not code:
            continue
        if code in seen_codes:
            code = f"{code}_{len(seen_codes)}"
        seen_codes.add(code)
        names = {
            props.get("NAME"),
            props.get("NAME_LONG"),
            props.get("ADMIN"),
            props.get("BRK_NAME"),
            props.get("FORMAL_EN"),
            props.get("ABBREV"),
        }
        alt = sorted({n for n in names if n and n != MISSING})
        # ISO_A2_EH (not plain ISO_A2): Taiwan's ISO_A2 is "CN-TW", not a usable 2-letter code,
        # and ISO_A2_EH gives Kosovo "XK" instead of "-99".
        iso2 = _clean(props.get("ISO_A2_EH")).upper()
        geometry = simplify_geometry(f["geometry"], epsilon, ndigits)
        features.append(
            {
                "type": "Feature",
                "properties": {"name": code, "alt": alt, "iso2": iso2 or None},
                "geometry": geometry,
            }
        )
    return {"type": "FeatureCollection", "features": features}


def build_us_states(src_path: Path, epsilon: float, ndigits: int) -> dict:
    raw = json.loads(src_path.read_text(encoding="utf-8"))
    features = []
    for f in raw["features"]:
        props = f["properties"]
        if props.get("admin") != "United States of America" and props.get("adm0_a3") != "USA":
            continue
        postal = _clean(props.get("postal")).upper()
        if not postal:
            continue
        names = {props.get("name"), props.get("name_alt"), props.get("woe_name")}
        alt = sorted({n for n in names if n and n != MISSING})
        iso2 = _clean(props.get("iso_3166_2"))
        geometry = simplify_geometry(f["geometry"], epsilon, ndigits)
        features.append(
            {
                "type": "Feature",
                "properties": {"name": postal, "alt": alt, "iso2": iso2 or None},
                "geometry": geometry,
            }
        )
    return {"type": "FeatureCollection", "features": features}


def write(name: str, data: dict) -> None:
    path = OUT / name
    path.write_text(json.dumps(data, separators=(",", ":")), encoding="utf-8")
    print(name, path.stat().st_size, "bytes", len(data["features"]), "features")


def main() -> None:
    with tempfile.TemporaryDirectory(prefix="viz-geo-assets-") as tmp:
        raw_dir = Path(tmp)
        paths = {name: download(name, url, raw_dir) for name, url in _SOURCES.items()}
        write(
            "countries-110m.json",
            build_countries(paths["ne_110m_admin_0_countries"], 0.04, 3),
        )
        write(
            "countries-50m.json",
            build_countries(paths["ne_50m_admin_0_countries"], 0.035, 3),
        )
        write(
            "us-states-110m.json",
            build_us_states(paths["ne_110m_admin_1_states_provinces"], 0.02, 3),
        )
        write(
            "us-states-50m.json",
            build_us_states(paths["ne_50m_admin_1_states_provinces"], 0.02, 3),
        )


if __name__ == "__main__":
    main()
