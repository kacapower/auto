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
  2/3 or 3/3 -> also appends a text warning ("more than 1 API" condition),
                since that's your 200m-equivalent confirmed case.

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
RADIUS_KM = 10
DEG_LAT_PER_KM = 1 / 111.0
DEG_LON_PER_KM = 1 / (111.320 * math.cos(math.radians(LAT)))

REQUEST_TIMEOUT = 15

TOMORROW_API_KEY = os.environ.get("TOMORROW_API_KEY", "")
WEATHERAPI_KEY = os.environ.get("WEATHERAPI_KEY", "")

TELEGRAM_BOT_TOKEN = os.environ.get("TELEGRAM_TOKEN")
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


def open_meteo_center_rain():
    return open_meteo_rain_at(LAT, LON)


def open_meteo_10km_sweep():
    """Samples 4 points ~10km N/S/E/W of the location. Returns True if any
    of them show rain in the next FORECAST_HOURS hours."""
    dlat = RADIUS_KM * DEG_LAT_PER_KM
    dlon = RADIUS_KM * DEG_LON_PER_KM
    points = [
        (LAT + dlat, LON),  # north
        (LAT - dlat, LON),  # south
        (LAT, LON + dlon),  # east
        (LAT, LON - dlon),  # west
    ]
    for plat, plon in points:
        try:
            if open_meteo_rain_at(plat, plon):
                return True
        except Exception as e:
            print(f"[warn] Open-Meteo sweep point ({plat:.4f},{plon:.4f}) failed: {e}", file=sys.stderr)
    return False


# ---------------------------------------------------------------------------
# Provider 2: Tomorrow.io
# ---------------------------------------------------------------------------

def tomorrow_io_rain():
    if not TOMORROW_API_KEY:
        print("[warn] TOMORROW_API_KEY not set, skipping Tomorrow.io", file=sys.stderr)
        return False
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
    for h in hourly:
        v = h.get("values", {})
        prob = v.get("precipitationProbability", 0) or 0
        intensity = v.get("precipitationIntensity", 0) or 0
        if prob >= TOMORROW_PROB_THRESHOLD or intensity >= TOMORROW_INTENSITY_THRESHOLD:
            return True
    return False


# ---------------------------------------------------------------------------
# Provider 3: WeatherAPI.com
# ---------------------------------------------------------------------------

def weatherapi_rain():
    if not WEATHERAPI_KEY:
        print("[warn] WEATHERAPI_KEY not set, skipping WeatherAPI.com", file=sys.stderr)
        return False
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

    for h in window:
        chance = h.get("chance_of_rain", 0) or 0
        will_rain = h.get("will_it_rain", 0)
        if chance >= WEATHERAPI_PROB_THRESHOLD or will_rain == 1:
            return True
    return False


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

    try:
        results["Open-Meteo"] = open_meteo_center_rain()
    except Exception as e:
        print(f"[error] Open-Meteo check failed: {e}", file=sys.stderr)
        results["Open-Meteo"] = False

    try:
        results["Tomorrow.io"] = tomorrow_io_rain()
    except Exception as e:
        print(f"[error] Tomorrow.io check failed: {e}", file=sys.stderr)
        results["Tomorrow.io"] = False

    try:
        results["WeatherAPI.com"] = weatherapi_rain()
    except Exception as e:
        print(f"[error] WeatherAPI.com check failed: {e}", file=sys.stderr)
        results["WeatherAPI.com"] = False

    rain_count = sum(1 for v in results.values() if v)
    print(f"Results: {results} -> {rain_count}/3 predict rain", file=sys.stderr)

    if rain_count == 0:
        # Normally silent, unless the 10km sweep finds something nearby.
        try:
            nearby = open_meteo_10km_sweep()
        except Exception as e:
            print(f"[error] 10km sweep failed: {e}", file=sys.stderr)
            nearby = False

        if nearby:
            send_telegram(
                "🌦️ Heads up: no rain expected right at your exact location in "
                "the next 2 hours, but rain is showing up within ~10km. Keep an eye out."
            )
        # else: stay completely silent, as originally specified
        return

    dots = "🟢" * rain_count + "🔴" * (3 - rain_count)
    message = dots

    if rain_count >= 2:
        message += (
            "\n🌧️ Rain likely right at your location in the next 2 hours "
            "(confirmed by more than 1 source) — grab an umbrella."
        )

    # Also mention the 10km sweep for context, it's cheap (Open-Meteo only)
    try:
        nearby = open_meteo_10km_sweep()
        if nearby:
            message += "\n(Also detected within ~10km of you.)"
    except Exception as e:
        print(f"[warn] 10km sweep failed: {e}", file=sys.stderr)

    send_telegram(message)


if __name__ == "__main__":
    main()
