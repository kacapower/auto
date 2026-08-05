#!/usr/bin/env python3
"""
Rain consensus alert bot.

Checks 3 weather APIs (Open-Meteo, Tomorrow.io, WeatherAPI.com) for rain in the
next 2 hours at a fixed location, and sends a dot-coded consensus message to
Telegram.

IMPORTANT — read this before relying on it:
None of these three APIs expose true spatial radar (i.e. "is it raining
anywhere within a 10km circle"). They are all POINT forecasts for the exact
lat/lon you query. So:

  - The "200m radius" check is, in practice, just your exact coordinates —
    all three APIs already have finer grid resolution than 200m at your
    location, so there's nothing extra to query there.
  - The "10km radius" check is approximated by sampling 4 extra points
    (north/south/east/west, ~10km out) using Open-Meteo ONLY. Open-Meteo is
    free/unlimited, whereas Tomorrow.io and WeatherAPI.com free tiers have
    tight daily/hourly call limits — running every 20 minutes already uses
    most of that budget for the 3-way consensus check alone, so multiplying
    calls across all three providers for a radius sweep would exhaust your
    quota within hours.

Consensus logic (dots), based on the 3 APIs at your exact location:
  0/3 rain -> silent, UNLESS the 10km sweep finds nearby rain, in which case
              a short heads-up text is sent instead of staying silent.
  1/3 rain -> 🟢🔴🔴
  2/3 rain -> 🟢🟢🔴
  3/3 rain -> 🟢🟢🟢
  2/3 or 3/3 -> also appends a text warning ("more than 1 API" condition).

Timing tier (only when rain_count > 0):
  - "within the next 15 minutes"  -> any provider flags rain in hour 0 AND
                                      Open-Meteo's 15-min feed confirms it in
                                      the very next 15-min block
  - "within the next hour"        -> any provider flags rain in hour 0, but
                                      not confirmed within the next 15 min
  - "within the next 2 hours"     -> no provider flags hour 0, but at least
                                      one flags hour 1 (60-120 min out)
  Only Open-Meteo has sub-hourly data, so the 15-min tier can only ever be
  confirmed via Open-Meteo — Tomorrow.io and WeatherAPI.com only contribute
  at hourly resolution.

Env vars required:
  TOMORROW_API_KEY
  WEATHERAPI_KEY
  TELEGRAM_BOT_TOKEN                          (one bot, shared)
  TELEGRAM_CHAT_IDS                           (comma-separated, e.g. "111,222")
Open-Meteo needs no API key.
"""

import os
import sys
import math
from datetime import datetime
from zoneinfo import ZoneInfo

import requests

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

LAT = 26.019375
LON = 83.571676

# next-N-hours window to check
FORECAST_HOURS = 2

# "predicts rain" thresholds — tune these if you get too many/few alerts
OPEN_METEO_PROB_THRESHOLD = 40      # percent
OPEN_METEO_MM_THRESHOLD = 0.1       # mm, backup check if probability field missing
TOMORROW_PROB_THRESHOLD = 40        # percent
TOMORROW_INTENSITY_THRESHOLD = 0.1  # mm/hr
WEATHERAPI_PROB_THRESHOLD = 40      # percent

# 10km sampling ring (approximate offsets in degrees)
RADIUS_KM = 3
DEG_LAT_PER_KM = 1 / 111.0
DEG_LON_PER_KM = 1 / (111.320 * math.cos(math.radians(LAT)))

REQUEST_TIMEOUT = 15

TOMORROW_API_KEY = os.environ.get("TOMORROW_API_KEY", "")
WEATHERAPI_KEY = os.environ.get("WEATHERAPI_KEY", "")

TELEGRAM_BOT_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN", "")
TELEGRAM_CHAT_IDS = [
    cid.strip() for cid in os.environ.get("TELEGRAM_CHAT_IDS", "").split(",") if cid.strip()
]


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _current_hour_index(times, tz_name):
    """Given a list of ISO-ish hourly time strings and a timezone name,
    return the index of the first entry at or after 'now'."""
    try:
        tz = ZoneInfo(tz_name)
    except Exception:
        tz = ZoneInfo("UTC")
    now = datetime.now(tz)
    now_str = now.strftime("%Y-%m-%dT%H:00")
    for i, t in enumerate(times):
        if t.replace(" ", "T")[:13] + ":00" >= now_str:
            return i
    return 0


def _current_quarter_hour_index(times, tz_name):
    """Same idea as _current_hour_index but for 15-minute-resolution time
    strings (e.g. Open-Meteo's minutely_15 'time' list)."""
    try:
        tz = ZoneInfo(tz_name)
    except Exception:
        tz = ZoneInfo("UTC")
    now = datetime.now(tz)
    now_str = now.strftime("%Y-%m-%dT%H:%M")
    for i, t in enumerate(times):
        if t.replace(" ", "T")[:16] >= now_str:
            return i
    return 0


# ---------------------------------------------------------------------------
# Provider 1: Open-Meteo (also used for the 10km radius sweep)
# ---------------------------------------------------------------------------

def open_meteo_rain_at(lat, lon):
    """Returns True if Open-Meteo predicts rain in the next FORECAST_HOURS
    hours at (lat, lon)."""
    url = "https://api.open-meteo.com/v1/forecast"
    params = {
        "latitude": lat,
        "longitude": lon,
        "hourly": "precipitation_probability,precipitation",
        "forecast_days": 2,
        "timezone": "auto",
    }
    r = requests.get(url, params=params, timeout=REQUEST_TIMEOUT)
    r.raise_for_status()
    data = r.json()

    times = data["hourly"]["time"]
    probs = data["hourly"].get("precipitation_probability", [])
    mms = data["hourly"].get("precipitation", [])
    tz_name = data.get("timezone", "UTC")

    idx = _current_hour_index(times, tz_name)
    window_probs = probs[idx: idx + FORECAST_HOURS] if probs else []
    window_mms = mms[idx: idx + FORECAST_HOURS] if mms else []

    if any(p >= OPEN_METEO_PROB_THRESHOLD for p in window_probs):
        return True
    if any(m >= OPEN_METEO_MM_THRESHOLD for m in window_mms):
        return True
    return False


def open_meteo_center_hourly():
    """Returns a list of bools, one per hour in the next FORECAST_HOURS hours,
    e.g. [True, False] means 'rain in hour 1 (next ~60 min), not hour 2'."""
    url = "https://api.open-meteo.com/v1/forecast"
    params = {
        "latitude": LAT,
        "longitude": LON,
        "hourly": "precipitation_probability,precipitation",
        "forecast_days": 2,
        "timezone": "auto",
    }
    r = requests.get(url, params=params, timeout=REQUEST_TIMEOUT)
    r.raise_for_status()
    data = r.json()

    times = data["hourly"]["time"]
    probs = data["hourly"].get("precipitation_probability", [])
    mms = data["hourly"].get("precipitation", [])
    tz_name = data.get("timezone", "UTC")

    idx = _current_hour_index(times, tz_name)
    out = []
    for i in range(FORECAST_HOURS):
        p = probs[idx + i] if idx + i < len(probs) else 0
        m = mms[idx + i] if idx + i < len(mms) else 0
        out.append(p >= OPEN_METEO_PROB_THRESHOLD or m >= OPEN_METEO_MM_THRESHOLD)
    return out


def open_meteo_center_rain():
    return any(open_meteo_center_hourly())


def open_meteo_next_15min_rain():
    """Checks Open-Meteo's 15-minute-resolution feed for the very next
    15-minute block specifically. This is the only provider with sub-hourly
    data, so it's only used to sharpen an already-detected 'within the hour'
    result down to 'within 15 minutes'."""
    url = "https://api.open-meteo.com/v1/forecast"
    params = {
        "latitude": LAT,
        "longitude": LON,
        "minutely_15": "precipitation",
        "forecast_days": 1,
        "timezone": "auto",
    }
    r = requests.get(url, params=params, timeout=REQUEST_TIMEOUT)
    r.raise_for_status()
    data = r.json()

    times = data["minutely_15"]["time"]
    mms = data["minutely_15"].get("precipitation", [])
    tz_name = data.get("timezone", "UTC")

    idx = _current_quarter_hour_index(times, tz_name)
    if idx < len(mms):
        return mms[idx] >= OPEN_METEO_MM_THRESHOLD
    return False


def open_meteo_10km_sweep():
    """Samples 4 points ~10km N/S/E/W of the location. Returns a list of hits,
    each a dict with 'direction', 'lat', 'lon' — empty list if no rain found."""
    dlat = RADIUS_KM * DEG_LAT_PER_KM
    dlon = RADIUS_KM * DEG_LON_PER_KM
    points = [
        ("North", LAT + dlat, LON),
        ("South", LAT - dlat, LON),
        ("East", LAT, LON + dlon),
        ("West", LAT, LON - dlon),
    ]
    hits = []
    for direction, plat, plon in points:
        try:
            if open_meteo_rain_at(plat, plon):
                hits.append({"direction": direction, "lat": plat, "lon": plon})
        except Exception as e:
            print(f"[warn] Open-Meteo sweep point ({plat:.4f},{plon:.4f}) failed: {e}", file=sys.stderr)
    return hits


def reverse_geocode(lat, lon):
    """Best-effort place name lookup via OpenStreetMap Nominatim (free, no key
    needed). Returns None on any failure — this is a nice-to-have, never
    something the core alert logic depends on."""
    try:
        url = "https://nominatim.openstreetmap.org/reverse"
        params = {"lat": lat, "lon": lon, "format": "jsonv2", "zoom": 14}
        headers = {"User-Agent": "personal-rain-alert-bot/1.0"}
        r = requests.get(url, params=params, headers=headers, timeout=REQUEST_TIMEOUT)
        r.raise_for_status()
        data = r.json()
        addr = data.get("address", {})
        return (
            addr.get("village")
            or addr.get("town")
            or addr.get("suburb")
            or addr.get("county")
            or addr.get("state_district")
        )
    except Exception as e:
        print(f"[warn] reverse geocode failed for ({lat:.4f},{lon:.4f}): {e}", file=sys.stderr)
        return None


def describe_sweep_hits(hits):
    """Turns sweep hits into a human-readable string like
    'North (near Bhinga), East'."""
    parts = []
    for h in hits:
        place = reverse_geocode(h["lat"], h["lon"])
        if place:
            parts.append(f"{h['direction']} (near {place})")
        else:
            parts.append(h["direction"])
    return ", ".join(parts)


# ---------------------------------------------------------------------------
# Provider 2: Tomorrow.io
# ---------------------------------------------------------------------------

def tomorrow_io_hourly():
    """Returns a list of bools, one per hour in the next FORECAST_HOURS hours."""
    if not TOMORROW_API_KEY:
        print("[warn] TOMORROW_API_KEY not set, skipping Tomorrow.io", file=sys.stderr)
        return [False] * FORECAST_HOURS
    url = "https://api.tomorrow.io/v4/weather/forecast"
    params = {
        "location": f"{LAT},{LON}",
        "apikey": TOMORROW_API_KEY,
        "timesteps": "1h",
    }
    r = requests.get(url, params=params, timeout=REQUEST_TIMEOUT)
    r.raise_for_status()
    data = r.json()

    hourly = data.get("timelines", {}).get("hourly", [])[:FORECAST_HOURS]
    out = []
    for h in hourly:
        v = h.get("values", {})
        prob = v.get("precipitationProbability", 0) or 0
        intensity = v.get("precipitationIntensity", 0) or 0
        out.append(prob >= TOMORROW_PROB_THRESHOLD or intensity >= TOMORROW_INTENSITY_THRESHOLD)
    while len(out) < FORECAST_HOURS:
        out.append(False)
    return out


def tomorrow_io_rain():
    return any(tomorrow_io_hourly())


# ---------------------------------------------------------------------------
# Provider 3: WeatherAPI.com
# ---------------------------------------------------------------------------

def weatherapi_hourly():
    """Returns a list of bools, one per hour in the next FORECAST_HOURS hours."""
    if not WEATHERAPI_KEY:
        print("[warn] WEATHERAPI_KEY not set, skipping WeatherAPI.com", file=sys.stderr)
        return [False] * FORECAST_HOURS
    url = "https://api.weatherapi.com/v1/forecast.json"
    params = {
        "key": WEATHERAPI_KEY,
        "q": f"{LAT},{LON}",
        "days": 2,
        "aqi": "no",
        "alerts": "no",
    }
    r = requests.get(url, params=params, timeout=REQUEST_TIMEOUT)
    r.raise_for_status()
    data = r.json()

    tz_name = data.get("location", {}).get("tz_id", "UTC")
    all_hours = []
    for day in data.get("forecast", {}).get("forecastday", []):
        all_hours.extend(day.get("hour", []))

    times = [h["time"] for h in all_hours]
    idx = _current_hour_index(times, tz_name)
    window = all_hours[idx: idx + FORECAST_HOURS]

    out = []
    for h in window:
        chance = h.get("chance_of_rain", 0) or 0
        will_rain = h.get("will_it_rain", 0)
        out.append(chance >= WEATHERAPI_PROB_THRESHOLD or will_rain == 1)
    while len(out) < FORECAST_HOURS:
        out.append(False)
    return out


def weatherapi_rain():
    return any(weatherapi_hourly())


# ---------------------------------------------------------------------------
# Telegram
# ---------------------------------------------------------------------------

def send_telegram(text):
    if not TELEGRAM_BOT_TOKEN:
        print("[error] TELEGRAM_BOT_TOKEN not set, cannot send any messages.", file=sys.stderr)
        return

    sent_to_any = False
    url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
    for chat_id in TELEGRAM_CHAT_IDS:
        if not chat_id:
            continue
        try:
            r = requests.post(
                url,
                data={"chat_id": chat_id, "text": text},
                timeout=REQUEST_TIMEOUT,
            )
            r.raise_for_status()
            sent_to_any = True
        except Exception as e:
            print(f"[error] Telegram send failed for chat_id {chat_id}: {e}", file=sys.stderr)
    if not sent_to_any:
        print("[error] No Telegram message was sent (no valid chat IDs).", file=sys.stderr)


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main():
    results = {}
    hourly_breakdowns = {}  # provider -> [hour0_bool, hour1_bool]

    try:
        om_hourly = open_meteo_center_hourly()
        hourly_breakdowns["Open-Meteo"] = om_hourly
        results["Open-Meteo"] = any(om_hourly)
    except Exception as e:
        print(f"[error] Open-Meteo check failed: {e}", file=sys.stderr)
        hourly_breakdowns["Open-Meteo"] = [False] * FORECAST_HOURS
        results["Open-Meteo"] = False

    try:
        tm_hourly = tomorrow_io_hourly()
        hourly_breakdowns["Tomorrow.io"] = tm_hourly
        results["Tomorrow.io"] = any(tm_hourly)
    except Exception as e:
        print(f"[error] Tomorrow.io check failed: {e}", file=sys.stderr)
        hourly_breakdowns["Tomorrow.io"] = [False] * FORECAST_HOURS
        results["Tomorrow.io"] = False

    try:
        wa_hourly = weatherapi_hourly()
        hourly_breakdowns["WeatherAPI.com"] = wa_hourly
        results["WeatherAPI.com"] = any(wa_hourly)
    except Exception as e:
        print(f"[error] WeatherAPI.com check failed: {e}", file=sys.stderr)
        hourly_breakdowns["WeatherAPI.com"] = [False] * FORECAST_HOURS
        results["WeatherAPI.com"] = False

    rain_count = sum(1 for v in results.values() if v)
    print(f"Results: {results} -> {rain_count}/3 predict rain", file=sys.stderr)
    print(f"Hourly breakdown: {hourly_breakdowns}", file=sys.stderr)

    if rain_count == 1:
        # Normally silent, unless the 10km sweep finds something nearby.
        try:
            hits = open_meteo_10km_sweep()
        except Exception as e:
            print(f"[error] 10km sweep failed: {e}", file=sys.stderr)
            hits = []

        if hits:
            desc = describe_sweep_hits(hits)
            send_telegram(
                "🌦️ Heads up:  "
                f"the next 2 hours,rain showing up to the {desc} "
                "~3km away."
            )
        # else: stay completely silent, as originally specified
        return

    # --- Work out the 3-tier timing: 15 min / 1 hour / 2 hours ---
    hour0_triggered = any(b[0] for b in hourly_breakdowns.values() if len(b) > 0)
    hour1_triggered = any(b[1] for b in hourly_breakdowns.values() if len(b) > 1)

    if hour0_triggered:
        try:
            imminent = open_meteo_next_15min_rain()
        except Exception as e:
            print(f"[warn] 15-min check failed: {e}", file=sys.stderr)
            imminent = False
        if imminent:
            tier_text = "⏱️  15 minutes."
        else:
            tier_text = "⏱️  next hour."
    elif hour1_triggered:
        tier_text = "⏱️  2 hours (not immediate)."
    else:
        tier_text = ""  # shouldn't happen given rain_count > 0, but just in case

    dots = "🟢" * rain_count + "🔴" * (3 - rain_count)
    message = dots
    if tier_text:
        message += f"\n{tier_text}"

    if rain_count >= 2:
        message += (
            "\n🌧️ Rain "
            " ."
        )

    # Also mention the 10km sweep for context, it's cheap (Open-Meteo only)
    try:
        hits = open_meteo_10km_sweep()
        if hits:
            desc = describe_sweep_hits(hits)
            message += f"\nA {desc}, ~3km away."
    except Exception as e:
        print(f"[warn] 3km sweep failed: {e}", file=sys.stderr)

    send_telegram(message)


if __name__ == "__main__":
    main()
