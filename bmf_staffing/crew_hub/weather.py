"""
Per-base weather for the Today board, decoded into plain language.

METARs come from the FAA/NOAA Aviation Weather Center data API
(https://aviationweather.gov/data/api/) — free, no API key. Responses are
cached in-process for CACHE_SECONDS so the board never hammers the API,
and any network failure degrades to "weather unavailable" without
breaking the page.

This is a planning reference for the board only — crews still get their
official weather briefing through normal channels.
"""

from __future__ import annotations

import json
import logging
import threading
import time
import urllib.parse
import urllib.request

from django.conf import settings

logger = logging.getLogger(__name__)

API_URL = "https://aviationweather.gov/api/data/metar"
CACHE_SECONDS = 600  # be polite to the free API; METARs update hourly anyway
FETCH_TIMEOUT = 6  # seconds; the Today board should never hang on weather

# Flight-category chip colors (standard aviation convention).
CATEGORY_STYLE = {
    "VFR": {"color": "#2e7d32", "meaning": "good conditions"},
    "MVFR": {"color": "#1d6fb8", "meaning": "marginal conditions"},
    "IFR": {"color": "#C12126", "meaning": "instrument conditions"},
    "LIFR": {"color": "#8e24aa", "meaning": "very low ceiling / visibility"},
    "N/A": {"color": "#8a8a8a", "meaning": "no recent report"},
}

# Common METAR weather codes -> plain words.
WEATHER_CODES = {
    "RA": "rain",
    "SN": "snow",
    "DZ": "drizzle",
    "TS": "thunderstorms",
    "FG": "fog",
    "BR": "mist",
    "HZ": "haze",
    "FZRA": "freezing rain",
    "FZDZ": "freezing drizzle",
    "PL": "ice pellets",
    "GR": "hail",
    "GS": "small hail",
    "SG": "snow grains",
    "IC": "ice crystals",
    "UP": "unknown precipitation",
    "SQ": "squalls",
    "FC": "funnel cloud",
    "DU": "dust",
    "SA": "blowing sand",
    "FU": "smoke",
    "VA": "volcanic ash",
    "PO": "dust whirls",
    "DS": "dust storm",
    "SS": "sandstorm",
}

_COMPASS = [
    "north",
    "northeast",
    "east",
    "southeast",
    "south",
    "southwest",
    "west",
    "northwest",
]

_cache_lock = threading.Lock()
_cache: dict[str, tuple[float, dict]] = {}


def station_map() -> dict[str, str]:
    """base name -> ICAO station, from settings."""
    return dict(getattr(settings, "CREW_HUB_WEATHER_STATIONS", []))


def _compass(degrees) -> str:
    try:
        return _COMPASS[int((float(degrees) + 22.5) // 45) % 8]
    except (TypeError, ValueError):
        return ""


def _c_to_f(celsius) -> int | None:
    try:
        return round(float(celsius) * 9 / 5 + 32)
    except (TypeError, ValueError):
        return None


def _visibility_miles(visib) -> float | None:
    """The API reports statute miles, sometimes as '10+' or '0.5'."""
    if visib is None:
        return None
    try:
        return float(str(visib).rstrip("+"))
    except ValueError:
        return None


def _ceiling_ft(clouds) -> int | None:
    """Lowest broken/overcast/obscured layer base, if any."""
    bases = [
        int(layer["base"])
        for layer in clouds or []
        if layer.get("cover") in ("BKN", "OVC", "VV") and layer.get("base") is not None
    ]
    return min(bases) if bases else None


def flight_category(visibility_mi: float | None, ceiling: int | None) -> str:
    """VFR / MVFR / IFR / LIFR from visibility (mi) and ceiling (ft)."""
    if visibility_mi is None and ceiling is None:
        return "N/A"
    vis = visibility_mi if visibility_mi is not None else 99.0
    ceil = ceiling if ceiling is not None else 99999
    if vis < 1 or ceil < 500:
        return "LIFR"
    if vis < 3 or ceil < 1000:
        return "IFR"
    if vis <= 5 or ceil <= 3000:
        return "MVFR"
    return "VFR"


def _sky_phrase(clouds) -> str:
    clouds = clouds or []
    covers = {layer.get("cover") for layer in clouds}
    if not clouds or covers <= {"CLR", "SKC", "CAVOK", "NSC"}:
        return "clear skies"
    ceiling = _ceiling_ft(clouds)
    if ceiling is not None:
        cover = next(
            (
                layer.get("cover")
                for layer in clouds
                if layer.get("base") == ceiling and layer.get("cover") != "VV"
            ),
            "OVC",
        )
        word = "overcast" if cover == "OVC" else "broken clouds"
        return f"{word} at {ceiling:,} ft"
    lowest = min(
        (layer for layer in clouds if layer.get("base") is not None),
        key=lambda layer: layer["base"],
        default=None,
    )
    if lowest is None:
        return "some clouds"
    return f"scattered clouds at {int(lowest['base']):,} ft"


def _wind_phrase(wdir, wspd, wgst) -> str:
    # METAR winds are knots; report mph for plain language.
    try:
        speed = round(float(wspd) * 1.15078)
    except (TypeError, ValueError):
        return ""
    if speed == 0:
        return "calm wind"
    direction = "variable" if str(wdir).upper() == "VRB" else _compass(wdir)
    phrase = f"wind {speed} mph" if not direction else f"{direction} wind {speed} mph"
    try:
        gust = round(float(wgst) * 1.15078)
    except (TypeError, ValueError):
        gust = 0
    if gust:
        phrase += f" gusting {gust}"
    return phrase


def _weather_phrase(wx_string) -> str:
    """'-RA BR' -> 'light rain, mist'."""
    if not wx_string:
        return ""
    words = []
    for token in str(wx_string).split():
        prefix = ""
        if token.startswith("+"):
            prefix, token = "heavy ", token[1:]
        elif token.startswith("-"):
            prefix, token = "light ", token[1:]
        if token.startswith("VC"):
            prefix, token = "nearby ", token[2:]
        shower = ""
        if token.startswith("SH"):
            shower, token = " showers", token[2:]
        word = WEATHER_CODES.get(token)
        if word:
            words.append(f"{prefix}{word}{shower}")
    return ", ".join(words)


def _vis_phrase(visibility_mi: float | None, raw) -> str:
    if visibility_mi is None:
        return ""
    if str(raw).endswith("+"):
        return f"{int(visibility_mi)}+ mi visibility"
    if visibility_mi == int(visibility_mi):
        return f"{int(visibility_mi)} mi visibility"
    return f"{visibility_mi:g} mi visibility"


def decode_metar(data: dict) -> dict:
    """One API METAR record -> chip + plain-English summary."""
    visibility = _visibility_miles(data.get("visib"))
    ceiling = _ceiling_ft(data.get("clouds"))
    category = flight_category(visibility, ceiling)

    parts = [
        _weather_phrase(data.get("wxString")),
        _sky_phrase(data.get("clouds")),
        _vis_phrase(visibility, data.get("visib")),
        _wind_phrase(data.get("wdir"), data.get("wspd"), data.get("wgst")),
    ]
    temp_f = _c_to_f(data.get("temp"))
    if temp_f is not None:
        parts.append(f"{temp_f}°F")
    summary = ", ".join(p for p in parts if p)
    summary = summary[0].upper() + summary[1:] if summary else "No details reported"

    observed = str(data.get("reportTime") or "")
    return {
        "station": str(data.get("icaoId") or ""),
        "category": category,
        "color": CATEGORY_STYLE[category]["color"],
        "meaning": CATEGORY_STYLE[category]["meaning"],
        "summary": summary,
        "observed": observed[11:16] if len(observed) >= 16 else "",
        "raw": str(data.get("rawOb") or ""),
    }


def _fetch_metars(stations: list[str]) -> dict[str, dict]:
    """station -> raw METAR record from the API (latest per station)."""
    query = urllib.parse.urlencode({"ids": ",".join(stations), "format": "json"})
    request = urllib.request.Request(
        f"{API_URL}?{query}", headers={"User-Agent": "BMF-Crew-Hub"}
    )
    with urllib.request.urlopen(request, timeout=FETCH_TIMEOUT) as response:
        records = json.loads(response.read().decode("utf-8"))
    latest: dict[str, dict] = {}
    for record in records or []:
        station = str(record.get("icaoId") or "")
        if station and station not in latest:
            latest[station] = record
    return latest


def get_base_weather() -> list[dict]:
    """Decoded weather per base for the Today board (cached, never raises)."""
    stations = station_map()
    if not stations:
        return []
    key = ",".join(sorted(stations.values()))

    with _cache_lock:
        cached = _cache.get(key)
        if cached and time.monotonic() - cached[0] < CACHE_SECONDS:
            records = cached[1]
        else:
            records = None
    if records is None:
        try:
            records = _fetch_metars(sorted(set(stations.values())))
        except Exception:  # noqa: BLE001 - weather must never break the board
            logger.warning("Base weather fetch failed", exc_info=True)
            records = {}
        # Cache failures too, so an outage costs one attempt per TTL window
        # instead of a slow fetch on every page view.
        with _cache_lock:
            _cache[key] = (time.monotonic(), records)

    rows = []
    for base, station in stations.items():
        record = records.get(station)
        if record:
            decoded = decode_metar(record)
        else:
            decoded = {
                "station": station,
                "category": "N/A",
                "color": CATEGORY_STYLE["N/A"]["color"],
                "meaning": CATEGORY_STYLE["N/A"]["meaning"],
                "summary": "Weather unavailable right now",
                "observed": "",
                "raw": "",
            }
        decoded["base"] = base
        rows.append(decoded)
    return rows
