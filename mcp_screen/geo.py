"""Places, routes and weather from public services (online, no API keys, no accounts).

- Places (addresses, towns, points of interest, worldwide): OpenStreetMap Nominatim,
  https://nominatim.openstreetmap.org (usage policy: max 1 request/s, identifying User-Agent;
  results cached); Open-Meteo geocoding as fallback, https://geocoding-api.open-meteo.com
- Routes: OpenStreetMap routing by FOSSGIS (OSRM; car, bike, foot), https://routing.openstreetmap.de
  (public service with a fair-use policy: occasional requests only)
- Weather: Open-Meteo forecast, https://api.open-meteo.com
- "thuis": Home Assistant's zone.home (through the Supervisor proxy, or HA_URL + HA_TOKEN).

Copy of mcp-geo/mcp_geo/geo.py (github.com/mosa-riel/mcp-geo) for map blocks with a "route";
keep the two in step.
"""

from __future__ import annotations

import asyncio
import math
import os
import re
import time

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


_NOMINATIM_GAP = 1.1  # usage policy: at most one request per second
_nominatim_lock = asyncio.Lock()
_nominatim_last = 0.0
_cache: dict[str, list[dict]] = {}


async def candidates(cli: httpx.AsyncClient, q: str) -> list[dict]:
    """Possible places for a name, best first: OpenStreetMap Nominatim (addresses, places and
    points of interest worldwide, ranked by importance); Open-Meteo geocoding if that fails."""
    key = q.lower()
    if key in _cache:
        return _cache[key]
    global _nominatim_last
    out: list[dict] = []
    try:
        async with _nominatim_lock:
            wait = _NOMINATIM_GAP - (time.monotonic() - _nominatim_last)
            if wait > 0:
                await asyncio.sleep(wait)
            _nominatim_last = time.monotonic()
            r = await cli.get("https://nominatim.openstreetmap.org/search",
                              params={"q": q, "format": "jsonv2", "limit": 4, "accept-language": "nl"})
        r.raise_for_status()
        for p in r.json():
            parts = [x.strip() for x in p.get("display_name", "").split(",")]
            label = ", ".join(dict.fromkeys(x for x in parts[:3] + parts[-1:] if x and not x.replace(" ", "").isdigit()))
            out.append({"label": label, "name": p.get("name") or parts[0], "lat": float(p["lat"]), "lon": float(p["lon"]),
                        "kind": p.get("type", ""), "source": "OpenStreetMap"})
    except (httpx.HTTPError, ValueError, KeyError):
        pass
    if not out:
        try:
            r = await cli.get("https://geocoding-api.open-meteo.com/v1/search", params={"name": q, "count": 3, "language": "nl"})
            r.raise_for_status()
            for p in r.json().get("results") or []:
                label = ", ".join(x for x in (p.get("name"), p.get("admin1"), p.get("country")) if x)
                out.append({"label": label, "name": p.get("name", ""), "lat": float(p["latitude"]),
                            "lon": float(p["longitude"]), "kind": "plaats", "source": "Open-Meteo"})
        except (httpx.HTTPError, ValueError, KeyError):
            pass
    if out:
        _cache[key] = out
        if len(_cache) > 500:
            _cache.pop(next(iter(_cache)))
    return out


def best(q: str, cands: list[dict]) -> dict | None:
    """The default pick: the source's own ranking (Nominatim orders by importance). The tools
    always name the place they used, so the model can check it and pick an alternative from
    plaats_zoeken, or pass coordinates."""
    return cands[0] if cands else None


async def geocode(cli: httpx.AsyncClient, name: str) -> tuple[str, float, float]:
    """→ (label, lat, lon). Empty or 'thuis'/'hier' is zone.home; 'lat,lon' is used as is."""
    q = (name or "").strip()
    if not q or q.lower().strip(" .!?") in HOME_WORDS:
        return await home(cli)
    m = re.fullmatch(r"\s*(-?\d+(?:\.\d+)?)\s*,\s*(-?\d+(?:\.\d+)?)\s*", q)
    if m:
        return q, float(m.group(1)), float(m.group(2))
    c = best(q, await candidates(cli, q))
    if c is None:
        raise GeoError(f"plaats '{name}' niet gevonden")
    return c["label"], c["lat"], c["lon"]


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
