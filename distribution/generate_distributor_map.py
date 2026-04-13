#!/usr/bin/env python3

"""Generate a rough location map for distributor suppliers.

This script geocodes supplier addresses with Nominatim, caches the results,
and writes a simple SVG map for use in the research archive.
"""

from __future__ import annotations

import json
import re
import time
import urllib.parse
import urllib.request
from pathlib import Path


ROOT = Path(__file__).resolve().parent
CACHE_PATH = ROOT / "distributor_locations.json"
SVG_PATH = ROOT / "distributor_locations.svg"

SUPPLIERS = [
    {
        "name": "Oke Produce",
        "address": "111 Court Street, Oshawa, ON L1H 4W5",
        "listed": False,
        "dx": 14,
        "dy": -16,
    },
    {
        "name": "J.E. Russell Produce",
        "address": "165 The Queensway, Suite 332, Toronto, ON M8Y 1H8",
        "listed": True,
        "dx": 28,
        "dy": -86,
    },
    {
        "name": "Oishi Foods / BuyMushroom.ca",
        "address": "P.O. Box 433 Station C, Toronto, ON M6J 3P5",
        "geocode_query": "Oishi Foods Toronto ON",
        "listed": False,
        "dx": 110,
        "dy": -120,
    },
    {
        "name": "Gambles Produce",
        "address": "302 Dwight Avenue, Toronto, ON M8V 2W7",
        "listed": True,
        "dx": 32,
        "dy": -48,
    },
    {
        "name": "Bondi Produce",
        "address": "188 New Toronto Street, Toronto, ON M8V 2E8",
        "listed": False,
        "dx": 36,
        "dy": -4,
    },
    {
        "name": "Green Liner Produce",
        "address": "151 Regal Road, Guelph, ON N1K 1E2",
        "listed": False,
        "dx": 14,
        "dy": -16,
    },
    {
        "name": "Sanfilippo Wholesale",
        "address": "333 Peel Street, Collingwood, ON L9Y 3W3",
        "listed": False,
        "dx": 14,
        "dy": -16,
    },
    {
        "name": "Fresh Taste Produce",
        "address": "165 The Queensway, Toronto, ON M8Y 1H8",
        "listed": True,
        "dx": 32,
        "dy": 16,
    },
    {
        "name": "Mister Produce",
        "address": "1290 Blundell Road, Mississauga, ON L4Y 1M5",
        "listed": False,
        "dx": -230,
        "dy": -12,
    },
    {
        "name": "Deluxe Produce",
        "address": "40 Magnetic Drive, Unit 1, Toronto, ON M3J 2C4",
        "listed": False,
        "dx": 14,
        "dy": -16,
    },
    {
        "name": "Dom Amodeo Produce",
        "address": "165 The Queensway, Unit 150, Toronto, ON M8Y 1H8",
        "listed": True,
        "dx": 32,
        "dy": 42,
    },
    {
        "name": "F.G. Lister",
        "address": "475 Horner Avenue, Toronto, ON M8W 4X7",
        "listed": True,
        "dx": 24,
        "dy": 74,
    },
    {
        "name": "Fresh Advancement",
        "address": "165 The Queensway, Toronto, ON M8Y 1H8",
        "listed": True,
        "dx": 34,
        "dy": 94,
    },
    {
        "name": "Rite-Pak Produce",
        "address": "165 The Queensway, Toronto, ON M8Y 1H8",
        "listed": True,
        "dx": 34,
        "dy": 124,
    },
    {
        "name": "AM Produce",
        "address": "Akron Road, Etobicoke, ON M8W 1T2",
        "listed": False,
        "dx": -170,
        "dy": 18,
    },
]


def load_cache() -> dict[str, dict[str, float]]:
    if not CACHE_PATH.exists():
        return {}
    return json.loads(CACHE_PATH.read_text())


def save_cache(cache: dict[str, dict[str, float]]) -> None:
    CACHE_PATH.write_text(json.dumps(cache, indent=2, sort_keys=True) + "\n")


def geocode(query: str) -> tuple[float, float]:
    encoded = urllib.parse.quote(query)
    url = f"https://nominatim.openstreetmap.org/search?format=jsonv2&limit=1&q={encoded}"
    req = urllib.request.Request(url, headers={"User-Agent": "Codex distributor map generator"})
    with urllib.request.urlopen(req, timeout=30) as response:
        payload = json.loads(response.read().decode("utf-8"))
    if not payload:
        raise RuntimeError(f"No geocoding result for {query!r}")
    return float(payload[0]["lat"]), float(payload[0]["lon"])


def candidate_queries(supplier: dict[str, object]) -> list[str]:
    queries = []
    primary = str(supplier.get("geocode_query", supplier["address"]))
    queries.append(primary)

    address = str(supplier["address"])
    if address not in queries:
        queries.append(address)

    stripped = re.sub(r",?\s+(Suite|Unit)\s+[^,]+", "", address, flags=re.IGNORECASE)
    if stripped not in queries:
        queries.append(stripped)

    city_fallback = ", ".join(part.strip() for part in address.split(",")[-2:])
    if city_fallback not in queries:
        queries.append(city_fallback)

    return queries


def get_coordinates() -> list[dict[str, object]]:
    cache = load_cache()
    changed = False
    enriched = []
    for supplier in SUPPLIERS:
        chosen_query = None
        coords = None
        for query in candidate_queries(supplier):
            if query in cache:
                chosen_query = query
                coords = cache[query]
                break
            try:
                lat, lon = geocode(query)
            except RuntimeError:
                continue
            cache[query] = {"lat": lat, "lon": lon}
            chosen_query = query
            coords = cache[query]
            changed = True
            time.sleep(1.1)
            break

        if coords is None or chosen_query is None:
            raise RuntimeError(f"Unable to geocode supplier {supplier['name']!r}")

        enriched.append({**supplier, "lat": coords["lat"], "lon": coords["lon"]})
    if changed:
        save_cache(cache)
    return enriched


def scale(value: float, low: float, high: float, out_low: float, out_high: float) -> float:
    return out_low + (value - low) * (out_high - out_low) / (high - low)


def render_svg(points: list[dict[str, object]]) -> str:
    width = 1280
    height = 900
    margin_left = 90
    margin_right = 70
    margin_top = 90
    margin_bottom = 90

    lats = [float(point["lat"]) for point in points]
    lons = [float(point["lon"]) for point in points]
    lat_min = min(lats) - 0.12
    lat_max = max(lats) + 0.12
    lon_min = min(lons) - 0.22
    lon_max = max(lons) + 0.22

    plot_left = margin_left
    plot_right = width - margin_right
    plot_top = margin_top
    plot_bottom = height - margin_bottom

    def x_pos(lon: float) -> float:
        return scale(lon, lon_min, lon_max, plot_left, plot_right)

    def y_pos(lat: float) -> float:
        return scale(lat, lat_min, lat_max, plot_bottom, plot_top)

    grid_lon_values = [-80.3, -80.0, -79.7, -79.4, -79.1, -78.8]
    grid_lat_values = [43.55, 43.75, 43.95, 44.15, 44.35, 44.55]

    parts = [
        f'<svg xmlns="http://www.w3.org/2000/svg" width="{width}" height="{height}" viewBox="0 0 {width} {height}">',
        '<rect width="100%" height="100%" fill="#fbfaf4" />',
        f'<rect x="{plot_left}" y="{plot_top}" width="{plot_right - plot_left}" height="{plot_bottom - plot_top}" fill="#fffdf7" stroke="#d8d2c3" stroke-width="1.5" rx="18" />',
        '<text x="90" y="52" font-family="Helvetica, Arial, sans-serif" font-size="28" font-weight="700" fill="#24303a">Ontario distributor supplier locations (rough)</text>',
        '<text x="90" y="78" font-family="Helvetica, Arial, sans-serif" font-size="15" fill="#57636c">Geocoded from the public addresses in distributors.md. Orange = not listed in OFTB, blue = listed in OFTB.</text>',
    ]

    for lon in grid_lon_values:
        x = x_pos(lon)
        parts.append(f'<line x1="{x:.1f}" y1="{plot_top}" x2="{x:.1f}" y2="{plot_bottom}" stroke="#ece6d9" stroke-width="1" />')
        parts.append(
            f'<text x="{x:.1f}" y="{plot_bottom + 28}" text-anchor="middle" font-family="Helvetica, Arial, sans-serif" font-size="13" fill="#6c777e">{abs(lon):.1f} W</text>'
        )

    for lat in grid_lat_values:
        y = y_pos(lat)
        parts.append(f'<line x1="{plot_left}" y1="{y:.1f}" x2="{plot_right}" y2="{y:.1f}" stroke="#ece6d9" stroke-width="1" />')
        parts.append(
            f'<text x="{plot_left - 16}" y="{y + 4:.1f}" text-anchor="end" font-family="Helvetica, Arial, sans-serif" font-size="13" fill="#6c777e">{lat:.2f} N</text>'
        )

    parts.extend(
        [
            f'<rect x="{width - 330}" y="98" width="240" height="86" rx="14" fill="#fffdf7" stroke="#d8d2c3" stroke-width="1.2" />',
            f'<circle cx="{width - 300}" cy="128" r="8" fill="#d97706" />',
            f'<text x="{width - 282}" y="133" font-family="Helvetica, Arial, sans-serif" font-size="15" fill="#24303a">Not listed in OFTB</text>',
            f'<circle cx="{width - 300}" cy="160" r="8" fill="#2563eb" />',
            f'<text x="{width - 282}" y="165" font-family="Helvetica, Arial, sans-serif" font-size="15" fill="#24303a">Listed in OFTB</text>',
        ]
    )

    for point in points:
        x = x_pos(float(point["lon"]))
        y = y_pos(float(point["lat"]))
        color = "#2563eb" if point["listed"] else "#d97706"
        label_x = x + int(point["dx"])
        label_y = y + int(point["dy"])
        parts.append(f'<circle cx="{x:.1f}" cy="{y:.1f}" r="7" fill="{color}" stroke="#fffdf7" stroke-width="2" />')
        parts.append(f'<line x1="{x:.1f}" y1="{y:.1f}" x2="{label_x:.1f}" y2="{label_y:.1f}" stroke="{color}" stroke-width="1.2" opacity="0.8" />')
        parts.append(
            f'<text x="{label_x + 4:.1f}" y="{label_y - 2:.1f}" font-family="Helvetica, Arial, sans-serif" font-size="14" font-weight="600" fill="#24303a">{point["name"]}</text>'
        )
        parts.append(
            f'<text x="{label_x + 4:.1f}" y="{label_y + 16:.1f}" font-family="Helvetica, Arial, sans-serif" font-size="12" fill="#66737c">{point["address"]}</text>'
        )

    parts.append("</svg>")
    return "\n".join(parts) + "\n"


def main() -> None:
    points = get_coordinates()
    SVG_PATH.write_text(render_svg(points))
    print(f"Wrote {SVG_PATH}")
    print(f"Wrote {CACHE_PATH}")


if __name__ == "__main__":
    main()
