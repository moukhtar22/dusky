#!/usr/bin/env python3

import argparse
import http.client
import json
import sys
import time
import urllib.request
from pathlib import Path
from typing import Any, Never
from urllib.error import URLError

# Consolidated WMO Codes: O(1) unified lookup for both icon and description
WEATHER_CODES: dict[int, tuple[str, str]] = {
    0: ("", "Clear sky"),
    1: ("", "Mainly clear"),
    2: ("", "Partly cloudy"),
    3: ("", "Overcast"),
    45: ("󰖑", "Fog"),
    48: ("󰖑", "Depositing rime fog"),
    51: ("", "Light drizzle"),
    53: ("", "Moderate drizzle"),
    55: ("", "Dense drizzle"),
    56: ("", "Light freezing drizzle"),
    57: ("", "Dense freezing drizzle"),
    61: ("", "Slight rain"),
    63: ("", "Moderate rain"),
    65: ("", "Heavy rain"),
    66: ("", "Light freezing rain"),
    67: ("", "Heavy freezing rain"),
    71: ("", "Slight snow"),
    73: ("", "Moderate snow"),
    75: ("", "Heavy snow"),
    77: ("", "Snow grains"),
    80: ("", "Slight rain showers"),
    81: ("", "Moderate rain showers"),
    82: ("", "Violent rain showers"),
    85: ("", "Slight snow showers"),
    86: ("", "Heavy snow showers"),
    95: ("", "Thunderstorm"),
    96: ("", "Thunderstorm with slight hail"),
    99: ("", "Thunderstorm with heavy hail"),
}

IMPERIAL_COUNTRIES = {"US", "LR", "MM"}
STATE_FILE = Path.home() / ".config" / "dusky" / "settings" / "waybar_weather"

def print_waybar_data(text: str, tooltip: str, alt: str = "Weather", css_class: str | list[str] = "weather") -> None:
    """Safely outputs JSON to stdout and flushes the buffer for Waybar polling."""
    out = {
        "text": text,
        "alt": alt,
        "tooltip": tooltip,
        "class": css_class
    }
    print(json.dumps(out), flush=True)

def fail_gracefully(message: str, tooltip: str = "") -> Never:
    """Exits 0 to prevent Waybar from aggressively restarting the thread on network drops."""
    print_waybar_data("󰖐 Err", tooltip or message, "Error", "error")
    sys.exit(0)

def fetch_json(url: str, timeout: int = 5) -> dict[str, Any] | None:
    """Fetches and parses JSON with strict exception handling and a compliant User-Agent."""
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36"})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as response:
            return json.loads(response.read())
    except (URLError, json.JSONDecodeError, TimeoutError, http.client.HTTPException, OSError):
        return None

def get_ip_location() -> tuple[float | None, float | None, str, str]:
    """Retrieves IP-based location, returning (lat, lon, country_code, city)."""
    data = fetch_json("http://ip-api.com/json/")
    if data and data.get("status") == "success":
        return data.get("lat"), data.get("lon"), data.get("countryCode", ""), data.get("city", "")
    return None, None, "", ""

def read_state(ignore_ttl: bool = False, ttl_seconds: int = 3600) -> str | None:
    """Reads the state file. If ignore_ttl is True, bypasses the age check."""
    try:
        age = time.time() - STATE_FILE.stat().st_mtime
        if ignore_ttl or age < ttl_seconds:
            return STATE_FILE.read_text(encoding="utf-8")
    except OSError:
        pass
    return None

def write_state(waybar_json_string: str) -> None:
    """Safely writes the final Waybar output to the Dusky state directory."""
    try:
        STATE_FILE.parent.mkdir(parents=True, exist_ok=True)
        STATE_FILE.write_text(waybar_json_string, encoding="utf-8")
    except OSError:
        pass

def main() -> None:
    parser = argparse.ArgumentParser(description="Optimized Waybar Weather Module")
    parser.add_argument("--lat", type=float, help="Latitude override")
    parser.add_argument("--lon", type=float, help="Longitude override")
    parser.add_argument("-i", "--interval", type=int, default=3600, help="Update interval in seconds (default: 3600)")
    
    unit_group = parser.add_mutually_exclusive_group()
    unit_group.add_argument("-c", "--celsius", action="store_true", help="Force Celsius")
    unit_group.add_argument("-f", "--fahrenheit", action="store_true", help="Force Fahrenheit")
    
    args = parser.parse_args()

    # 0. Primary Cache Check (Circuit Breaker)
    cached_payload = read_state(ttl_seconds=args.interval)
    if cached_payload:
        print(cached_payload, flush=True)
        sys.exit(0)

    # 1. Validate Coordinate Integrity
    if (args.lat is None) != (args.lon is None):
        parser.error("Arguments --lat and --lon must be provided together.")

    lat: float | None = args.lat
    lon: float | None = args.lon
    city: str = ""
    country_code: str = ""

    # 2. Resolve Coordinates
    if lat is None or lon is None:
        lat, lon, country_code, city = get_ip_location()
        if lat is None or lon is None:
            fail_gracefully("Network Offline", "Failed to determine IP location.")

    # 3. Resolve Unit 
    unit = "metric"
    if args.fahrenheit:
        unit = "imperial"
    elif args.celsius:
        unit = "metric"
    elif country_code in IMPERIAL_COUNTRIES:
        unit = "imperial"

    # 4. Fetch Weather Data (Modernized v1 API)
    temp_unit = "fahrenheit" if unit == "imperial" else "celsius"
    weather_url = (
        f"https://api.open-meteo.com/v1/forecast?"
        f"latitude={lat}&longitude={lon}&"
        f"current=temperature_2m,weather_code&"
        f"temperature_unit={temp_unit}&"
        f"daily=temperature_2m_max,temperature_2m_min,precipitation_probability_max&"
        f"timezone=auto&forecast_days=1"
    )

    weather_data = fetch_json(weather_url, timeout=10)
    
    # 5. Validate Payload & Handle Fallback Rescue
    if not weather_data or weather_data.get("error"):
        stale_payload = read_state(ignore_ttl=True)
        if stale_payload:
            try:
                stale_dict = json.loads(stale_payload)
                stale_dict["class"] = ["weather", "offline"]
                stale_dict["tooltip"] = stale_dict.get("tooltip", "") + "\n\n<span color='red'>⚠ System Offline - Showing Cached Data</span>"
                print(json.dumps(stale_dict), flush=True)
                sys.exit(0)
            except json.JSONDecodeError:
                pass
        
        err_msg = weather_data.get('reason', 'Unknown API Error') if weather_data else "No cache available to display."
        fail_gracefully("Network Offline", err_msg)

    # 6. Safely Extract Data (Strict Validation & Rounding)
    try:
        current = weather_data.get("current")
        if not current:
            fail_gracefully("API Error", "Missing 'current' weather data in response.")
            
        temp = round(current.get("temperature_2m", 0))
        weather_code = current.get("weather_code", -1)

        daily = weather_data.get("daily")
        if not daily:
            fail_gracefully("API Error", "Missing 'daily' forecast data in response.")
            
        daily_temp_max = daily.get("temperature_2m_max", [])
        daily_temp_min = daily.get("temperature_2m_min", [])
        daily_precip = daily.get("precipitation_probability_max", [])

        temp_max = round(daily_temp_max[0]) if daily_temp_max and daily_temp_max[0] is not None else temp
        temp_min = round(daily_temp_min[0]) if daily_temp_min and daily_temp_min[0] is not None else temp
        precip_prob = round(daily_precip[0]) if daily_precip and daily_precip[0] is not None else 0
    except (IndexError, TypeError, ValueError, AttributeError):
        fail_gracefully("Parse Error", "Malformed response from Open-Meteo API.")

    # 7. Build Output
    icon, weather_desc = WEATHER_CODES.get(weather_code, ("", "Unknown"))
    temp_symbol = "°F" if unit == "imperial" else "°C"

    tooltip_text = (
        f'\t\t<span size="xx-large">{temp}{temp_symbol}</span>\t\t\n'
        f'<big>{icon}</big>\n'
        f'<big>{weather_desc}</big>\n'
        f':{temp_max}{temp_symbol}  :{temp_min}{temp_symbol}  |  {precip_prob}%'
    )

    final_payload = {
        "text": f"{icon}   {temp}{temp_symbol}",
        "alt": city if city else "Weather",
        "tooltip": tooltip_text,
        "class": "weather"
    }
    
    final_json_string = json.dumps(final_payload)
    
    # Write directly to the state file, then execute print output
    write_state(final_json_string)
    print(final_json_string, flush=True)

if __name__ == "__main__":
    main()
