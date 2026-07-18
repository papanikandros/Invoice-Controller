"""Deterministic geo lookup for the F3 Standortbeschreibung — no LLM.

Data classes recovered for the "Standort" paragraph:
  * Bundesland + Regierungsbezirk + coordinates — offline via pgeocode (GeoNames,
    downloaded once then cached locally).
  * Kreis (Landkreis) — OSM Nominatim reverse geocode on the coordinates.
  * Nearby Autobahnen / Bundesstraßen — OSM Overpass around the coordinates,
    ranked by distance.

The OSM calls are best-effort: on no network (or an OSM hiccup) the lookup
degrades to the offline fields and leaves Kreis/roads empty, and the assembler
flags the roads line for the consultant to complete.
"""

from __future__ import annotations

import math
import time
from dataclasses import dataclass, field

import httpx
import pgeocode

_UA = {"User-Agent": "Invoice-Controller/0.1 (BAFA EEW tooling; contact tinonikandros@gmail.com)"}
_NOMINATIM = "https://nominatim.openstreetmap.org/reverse"
_OVERPASS = "https://overpass-api.de/api/interpreter"


@dataclass
class GeoInfo:
    plz: str
    stadt: str
    bundesland: str | None = None
    regierungsbezirk: str | None = None
    kreis: str | None = None
    lat: float | None = None
    lon: float | None = None
    autobahnen: list[str] = field(default_factory=list)     # e.g. ["A 2", "A 30"]
    bundesstrassen: list[str] = field(default_factory=list)  # e.g. ["B 514", "B 611"]
    online_enriched: bool = False  # True if Kreis/roads were fetched from OSM


def _clean(v: object) -> str | None:
    if v is None:
        return None
    s = str(v).strip()
    if not s or s.lower() == "nan":
        return None
    return s


def offline_geo(plz: str, stadt: str) -> GeoInfo:
    """Bundesland, Regierungsbezirk and coordinates from the PLZ — fully offline."""
    rec = pgeocode.Nominatim("de").query_postal_code(str(plz))
    info = GeoInfo(plz=str(plz), stadt=stadt or _clean(rec.place_name) or "")
    info.bundesland = _clean(rec.state_name)
    county = _clean(rec.county_name)
    if county and county.startswith("Reg.-Bez."):
        info.regierungsbezirk = "Regierungsbezirk " + county.split("Reg.-Bez.", 1)[1].strip()
    lat, lon = rec.latitude, rec.longitude
    if lat == lat and lon == lon:  # not NaN
        info.lat, info.lon = float(lat), float(lon)
    return info


def _haversine_km(a_lat: float, a_lon: float, b_lat: float, b_lon: float) -> float:
    r = 6371.0
    p1, p2 = math.radians(a_lat), math.radians(b_lat)
    dp = math.radians(b_lat - a_lat)
    dl = math.radians(b_lon - a_lon)
    h = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2 * r * math.asin(math.sqrt(h))


def _kreis_from_osm(lat: float, lon: float, client: httpx.Client) -> str | None:
    r = client.get(
        _NOMINATIM,
        params={"lat": lat, "lon": lon, "format": "json", "zoom": 10, "addressdetails": 1},
        headers=_UA, timeout=20,
    )
    r.raise_for_status()
    return _clean(r.json().get("address", {}).get("county"))


def _roads_from_osm(
    lat: float, lon: float, client: httpx.Client, *, radius_m: int = 9000, keep: int = 4
) -> tuple[list[str], list[str]]:
    """Nearest Autobahnen and Bundesstraßen, each deduped and distance-ranked."""
    query = (
        f"[out:json][timeout:25];"
        f'(way(around:{radius_m},{lat},{lon})[highway~"motorway|trunk|primary"][ref];);'
        f"out center;"
    )
    r = None
    for _attempt in range(2):  # Overpass is load-limited; one polite retry.
        r = client.post(_OVERPASS, data={"data": query}, headers=_UA, timeout=40)
        if r.status_code == 200 and "json" in r.headers.get("content-type", ""):
            break
        time.sleep(2)
    if r is None or r.status_code != 200:
        return [], []
    nearest: dict[str, float] = {}
    for el in r.json().get("elements", []):
        ref = el.get("tags", {}).get("ref", "")
        c = el.get("center") or {}
        if not ref or "lat" not in c:
            continue
        dist = _haversine_km(lat, lon, c["lat"], c["lon"])
        # Overpass returns "B 61;B 239" for shared segments — split into separate refs.
        for single in (s.strip() for s in ref.split(";")):
            if single and (single not in nearest or dist < nearest[single]):
                nearest[single] = dist
    autobahnen = sorted((r for r in nearest if r.startswith("A ")), key=lambda x: nearest[x])
    bundes = sorted((r for r in nearest if r.startswith("B ")), key=lambda x: nearest[x])
    return autobahnen[:keep], bundes[:keep]


def lookup_geo(plz: str, stadt: str, *, online: bool = True) -> GeoInfo:
    """Full geo lookup: offline base + best-effort OSM enrichment (Kreis, roads)."""
    info = offline_geo(plz, stadt)
    if not online or info.lat is None or info.lon is None:
        return info
    try:
        with httpx.Client() as client:
            info.kreis = _kreis_from_osm(info.lat, info.lon, client)
            info.autobahnen, info.bundesstrassen = _roads_from_osm(info.lat, info.lon, client)
            info.online_enriched = True
    except Exception:
        # Network/OSM failure is non-fatal: keep the offline fields, flag roads later.
        pass
    return info
