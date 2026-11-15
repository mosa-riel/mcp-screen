"""Places, routes and weather from public services (online, no API keys, no accounts).

- Places in the Netherlands: PDOK Locatieserver (Kadaster), https://api.pdok.nl/bzk/locatieserver
- Places elsewhere: Open-Meteo geocoding, https://geocoding-api.open-meteo.com
- Routes: OpenStreetMap routing by FOSSGIS (OSRM; car, bike, foot), https://routing.openstreetmap.de
  (public service with a fair-use policy: occasional requests only)
- Weather: Open-Meteo forecast, https://api.open-meteo.com
- "thuis": Home Assistant's zone.home (through the Supervisor proxy, or HA_URL + HA_TOKEN).

Copy of mcp-geo/mcp_geo/geo.py (github.com/mosa-riel/mcp-geo) for map blocks with a "route";
keep the two in step.
"""

from __future__ import annotations

import math
import os
import re

import httpx

UA = {"User-Agent": "mcp-screen (reTerminal e-paper; https://github.com/mosa-riel/mcp-screen)"}
# Words for the home address (Home Assistant's zone.home); an empty place means home too.
HOME_WORDS = {"thuis", "huis", "home", "hier", "mijn huis", "bij mij thuis", "bij mij", "bij ons", "vanaf hier",
              "vanaf thuis", "van thuis", "mijn adres", "ons huis", "huidige locatie", "mijn locatie", "hier in huis"}
PROFILES = {"fiets": "routed-bike", "auto": "routed-car", "lopen": "routed-foot", "wandelen": "routed-foot"}
WIND = ["N", "NNO", "NO", "ONO", "O", "OZO", "ZO", "ZZO", "Z", "ZZW", "ZW", "WZW", "W", "WNW", "NW", "NNW"]
WIND_NAME = {"N": "noord", "O": "oost", "Z": "zuid", "W": "west"}
WMO = {0: "helder", 1: "vrijwel helder", 2: "half bewolkt", 3: "bewolkt", 45: "mist", 48: "rijpmist",
       51: "lichte motregen", 53: "motregen", 55: "dichte motregen", 56: "ijzel", 57: "ijzel",
       61: "lichte regen", 63: "regen", 65: "zware regen", 66: "ijzelregen", 67: "ijzelregen",
       71: "lichte sneeuw", 73: "sneeuw", 75: "zware sneeuw", 77: "korrelsneeuw",
       80: "lichte buien", 81: "buien", 82: "zware buien", 85: "sneeuwbuien", 86: "zware sneeuwbuien",
       95: "onweer", 96: "onweer met hagel", 99: "zwaar onweer met hagel"}


class GeoError(Exception):
    pass


def compass(deg: float) -> str:
    return WIND[round((deg % 360) / 22.5) % 16]


def compass_long(deg: float) -> str:
    """'ZZW' → 'zuidzuidwest' style, for speech."""
    return "".join(WIND_NAME[c] for c in compass(deg))


def bearing(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    p1, p2, dl = math.radians(lat1), math.radians(lat2), math.radians(lon2 - lon1)
    x = math.sin(dl) * math.cos(p2)
    y = math.cos(p1) * math.sin(p2) - math.sin(p1) * math.cos(p2) * math.cos(dl)
    return (math.degrees(math.atan2(x, y)) + 360) % 360


def _ha_endpoint() -> tuple[str, str] | None:
    if os.getenv("SUPERVISOR_TOKEN"):
        return "http://supervisor/core/api", os.environ["SUPERVISOR_TOKEN"]
    if os.getenv("HA_URL") and os.getenv("HA_TOKEN"):
        return os.environ["HA_URL"].rstrip("/") + "/api", os.environ["HA_TOKEN"]
    return None


async def home(cli: httpx.AsyncClient) -> tuple[str, float, float]:
    ep = _ha_endpoint()
    if ep is None:
        raise GeoError("waar 'thuis' is, is onbekend (geen Home Assistant)")
    base, token = ep
    r = await cli.get(f"{base}/states/zone.home", headers={"Authorization": f"Bearer {token}"})
    r.raise_for_status()
    a = r.json()["attributes"]
    return "thuis", float(a["latitude"]), float(a["longitude"])


async def geocode(cli: httpx.AsyncClient, name: str) -> tuple[str, float, float]:
    """→ (label, lat, lon). 'thuis' is zone.home; Dutch places via PDOK, else Open-Meteo."""
    q = (name or "").strip()
    if not q or q.lower().strip(" .!?") in HOME_WORDS:
        return await home(cli)
    m = re.fullmatch(r"\s*(-?\d+(?:\.\d+)?)\s*,\s*(-?\d+(?:\.\d+)?)\s*", q)
    if m:
        return q, float(m.group(1)), float(m.group(2))
    has_digits = any(c.isdigit() for c in q)
    # Addresses and postcodes: PDOK, all types. Places: Dutch towns first (woonplaats, then
    # gemeente), else anywhere in the world (Open-Meteo), else Dutch streets etc.
    for fq in ((None,) if has_digits else ("type:woonplaats", "type:gemeente")):
        hit = await _pdok(cli, q, fq)
        if hit:
            return hit
    world = await _open_meteo(cli, q)
    if world:
        return world
    hit = None if has_digits else await _pdok(cli, q, None)
    if hit:
        return hit
    raise GeoError(f"plaats '{name}' niet gevonden")


async def _pdok(cli: httpx.AsyncClient, q: str, fq: str | None):
    params = {"q": q, "rows": 1, "fl": "weergavenaam,centroide_ll,type,score"}
    if fq:
        params["fq"] = fq
    try:
        r = await cli.get("https://api.pdok.nl/bzk/locatieserver/search/v3_1/free", params=params)
        r.raise_for_status()
        docs = r.json().get("response", {}).get("docs", [])
    except (httpx.HTTPError, ValueError):
        return None
    if not docs or docs[0].get("score", 0) < 5:
        return None
    lon, lat = map(float, re.findall(r"-?\d+\.\d+", docs[0]["centroide_ll"])[:2])
    return docs[0]["weergavenaam"], lat, lon


async def _open_meteo(cli: httpx.AsyncClient, q: str):
    try:
        r = await cli.get("https://geocoding-api.open-meteo.com/v1/search",
                          params={"name": q, "count": 1, "language": "nl"})
        r.raise_for_status()
        res = r.json().get("results") or []
    except (httpx.HTTPError, ValueError):
        return None
    if not res:
        return None
    p = res[0]
    label = ", ".join(x for x in (p.get("name"), p.get("admin1"), p.get("country")) if x)
    return label, float(p["latitude"]), float(p["longitude"])


async def route(cli: httpx.AsyncClient, a: tuple[float, float], b: tuple[float, float], vervoer: str) -> dict:
    """OSRM route → {distance_m, duration_s, path: [[lat, lon], ...] (simplified)}."""
    prof = PROFILES.get(vervoer, "routed-bike")
    url = (f"https://routing.openstreetmap.de/{prof}/route/v1/driving/"
           f"{a[1]:.6f},{a[0]:.6f};{b[1]:.6f},{b[0]:.6f}")
    r = await cli.get(url, params={"overview": "simplified", "geometries": "geojson"})
    r.raise_for_status()
    data = r.json()
    if data.get("code") != "Ok" or not data.get("routes"):
        raise GeoError(f"geen route gevonden ({data.get('code')})")
    rt = data["routes"][0]
    coords = rt["geometry"]["coordinates"]
    step = max(1, len(coords) // 60)  # at most ~60 points
    path = [[round(c[1], 5), round(c[0], 5)] for c in coords[::step]]
    if path[-1] != [round(coords[-1][1], 5), round(coords[-1][0], 5)]:
        path.append([round(coords[-1][1], 5), round(coords[-1][0], 5)])
    return {"distance_m": rt["distance"], "duration_s": rt["duration"], "path": path}


async def forecast(cli: httpx.AsyncClient, lat: float, lon: float, days: int) -> dict:
    r = await cli.get("https://api.open-meteo.com/v1/forecast", params={
        "latitude": lat, "longitude": lon, "timezone": "Europe/Amsterdam", "forecast_days": max(1, min(days, 16)),
        "wind_speed_unit": "kmh",
        "hourly": "temperature_2m,precipitation,precipitation_probability,wind_speed_10m,wind_direction_10m,"
                  "wind_gusts_10m,weather_code",
        "daily": "temperature_2m_max,temperature_2m_min,precipitation_sum,precipitation_probability_max,"
                 "wind_speed_10m_max,wind_direction_10m_dominant,weather_code,sunrise,sunset",
    })
    r.raise_for_status()
    return r.json()
