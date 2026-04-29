#!/usr/bin/env python3

"""Render a static SVG map plot from a small JSON config.

Point records must provide:
- coordinate: {"lat": number, "lon": number} or [lat, lon]
- name: display name
- category: category key used for marker style and legend

The script writes SVG only. Render PNGs from the SVG with a browser or another
project-approved renderer so the final image can live in the topic resources/
directory while SVG scratch output stays under temp/.
"""

from __future__ import annotations

import argparse
import html
import json
import math
import textwrap
from pathlib import Path
from typing import Any


DEFAULT_PALETTE = [
    "#c2410c",
    "#2563eb",
    "#64748b",
    "#16a34a",
    "#9333ea",
    "#ca8a04",
    "#0891b2",
]


def escape(value: object) -> str:
    return html.escape(str(value), quote=True)


def shorten(value: str, width: int) -> str:
    return escape(textwrap.shorten(value, width=width, placeholder="..."))


def mercator_y(lat: float) -> float:
    lat = max(min(lat, 85.0), -85.0)
    radians = math.radians(lat)
    return math.log(math.tan(math.pi / 4.0 + radians / 2.0))


def parse_coordinate(value: object, label: str) -> tuple[float, float]:
    if isinstance(value, dict):
        if "lat" not in value or "lon" not in value:
            raise ValueError(f"{label} coordinate object must include lat and lon")
        return float(value["lat"]), float(value["lon"])

    if isinstance(value, list) and len(value) == 2:
        return float(value[0]), float(value[1])

    raise ValueError(f"{label} coordinate must be {{'lat', 'lon'}} or [lat, lon]")


def parse_lon_lat_pair(value: object, label: str) -> tuple[float, float]:
    if isinstance(value, dict):
        if "lat" not in value or "lon" not in value:
            raise ValueError(f"{label} coordinate object must include lat and lon")
        return float(value["lon"]), float(value["lat"])

    if isinstance(value, list) and len(value) == 2:
        return float(value[0]), float(value[1])

    raise ValueError(f"{label} basemap coordinate must be {{'lat', 'lon'}} or [lon, lat]")


def load_config(path: Path) -> dict[str, Any]:
    config = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(config, dict):
        raise ValueError("Config root must be an object")
    if "points" not in config or not isinstance(config["points"], list):
        raise ValueError("Config must include a points array")
    return config


def normalize_points(raw_points: list[object]) -> list[dict[str, Any]]:
    points = []
    for index, raw_point in enumerate(raw_points, start=1):
        if not isinstance(raw_point, dict):
            raise ValueError(f"Point {index} must be an object")
        for field in ("coordinate", "name", "category"):
            if field not in raw_point:
                raise ValueError(f"Point {index} is missing required field {field!r}")

        lat, lon = parse_coordinate(raw_point["coordinate"], f"Point {index}")
        marker_offset = raw_point.get("marker_offset", [0, 0])
        if not (isinstance(marker_offset, list) and len(marker_offset) == 2):
            raise ValueError(f"Point {index} marker_offset must be [dx, dy] when provided")

        points.append(
            {
                **raw_point,
                "lat": lat,
                "lon": lon,
                "marker_dx": float(marker_offset[0]),
                "marker_dy": float(marker_offset[1]),
            }
        )
    return points


def category_styles(points: list[dict[str, Any]], config: dict[str, Any]) -> dict[str, dict[str, Any]]:
    raw_styles = config.get("categories", {})
    if raw_styles is None:
        raw_styles = {}
    if not isinstance(raw_styles, dict):
        raise ValueError("categories must be an object when provided")

    categories = []
    for point in points:
        category = str(point["category"])
        if category not in categories:
            categories.append(category)

    def sort_key(category: str) -> tuple[float, str]:
        style = raw_styles.get(category, {})
        if isinstance(style, dict) and "order" in style:
            return (float(style["order"]), category)
        return (float(categories.index(category)), category)

    styles: dict[str, dict[str, Any]] = {}
    for palette_index, category in enumerate(sorted(categories, key=sort_key)):
        raw_style = raw_styles.get(category, {})
        if raw_style is None:
            raw_style = {}
        if not isinstance(raw_style, dict):
            raise ValueError(f"Category style for {category!r} must be an object")
        styles[category] = {
            "fill": raw_style.get("color", DEFAULT_PALETTE[palette_index % len(DEFAULT_PALETTE)]),
            "label": raw_style.get("label", category),
            "order": raw_style.get("order", palette_index),
        }
    return styles


def basemap_bounds(config: dict[str, Any]) -> list[tuple[float, float]]:
    basemap = config.get("basemap", {})
    if not isinstance(basemap, dict):
        return []

    coords: list[tuple[float, float]] = []
    for polygon in basemap.get("polygons", []):
        for point in polygon.get("points", []):
            coords.append(parse_lon_lat_pair(point, "Basemap polygon point"))
    for line in basemap.get("lines", []):
        for point in line.get("points", []):
            coords.append(parse_lon_lat_pair(point, "Basemap line point"))
    for place in basemap.get("places", []):
        if "coordinate" in place:
            coords.append(parse_lon_lat_pair(place["coordinate"], "Basemap place"))
    return coords


def render_svg(config: dict[str, Any]) -> str:
    points = normalize_points(config["points"])
    styles = category_styles(points, config)
    category_order = sorted(styles, key=lambda category: (float(styles[category]["order"]), category))

    width = int(config.get("width", 1400))
    height = int(config.get("height", 980))
    map_left = int(config.get("map_left", 70))
    map_top = int(config.get("map_top", 92))
    map_width = int(config.get("map_width", 900))
    map_height = int(config.get("map_height", 792))
    map_bottom = map_top + map_height
    panel_left = int(config.get("panel_left", map_left + map_width + 38))
    panel_top = int(config.get("panel_top", map_top))
    panel_width = int(config.get("panel_width", width - panel_left - 70))
    panel_height = int(config.get("panel_height", map_height))

    lats = [float(point["lat"]) for point in points]
    lons = [float(point["lon"]) for point in points]
    bounds = config.get("bounds")
    if isinstance(bounds, dict):
        lat_min = float(bounds["lat_min"])
        lat_max = float(bounds["lat_max"])
        lon_min = float(bounds["lon_min"])
        lon_max = float(bounds["lon_max"])
    else:
        lat_padding = float(config.get("lat_padding", 0.35))
        lon_padding = float(config.get("lon_padding", 0.45))
        lat_min = min(lats) - lat_padding
        lat_max = max(lats) + float(config.get("lat_padding_top", 0.30))
        lon_min = min(lons) - lon_padding
        lon_max = max(lons) + float(config.get("lon_padding_right", 0.32))

    merc_min = mercator_y(lat_min)
    merc_max = mercator_y(lat_max)

    def project(lon: float, lat: float) -> tuple[float, float]:
        x = map_left + (lon - lon_min) * map_width / (lon_max - lon_min)
        y = map_bottom - (mercator_y(lat) - merc_min) * map_height / (merc_max - merc_min)
        return x, y

    def point_string(coords: list[tuple[float, float]]) -> str:
        return " ".join(f"{x:.1f},{y:.1f}" for x, y in coords)

    parts = [
        f'<svg xmlns="http://www.w3.org/2000/svg" width="{width}" height="{height}" viewBox="0 0 {width} {height}">',
        "<defs>",
        f'<clipPath id="mapClip"><rect x="{map_left}" y="{map_top}" width="{map_width}" height="{map_height}" rx="18" /></clipPath>',
        "</defs>",
        '<rect width="100%" height="100%" fill="#fbfaf4" />',
        f'<text x="{map_left}" y="46" font-family="Helvetica, Arial, sans-serif" font-size="28" font-weight="700" fill="#24303a">{escape(config.get("title", "Map plot"))}</text>',
    ]
    subtitle = config.get("subtitle")
    if subtitle:
        parts.append(
            f'<text x="{map_left}" y="74" font-family="Helvetica, Arial, sans-serif" font-size="15" fill="#57636c">{escape(subtitle)}</text>'
        )

    parts.extend(
        [
            f'<rect x="{map_left}" y="{map_top}" width="{map_width}" height="{map_height}" fill="#f3efe4" stroke="#d4cbbb" stroke-width="1.5" rx="18" />',
            f'<g clip-path="url(#mapClip)">',
            f'<rect x="{map_left}" y="{map_top}" width="{map_width}" height="{map_height}" fill="#f4efe4" />',
        ]
    )

    basemap = config.get("basemap", {})
    if isinstance(basemap, dict):
        for polygon in basemap.get("polygons", []):
            coords = [project(*parse_lon_lat_pair(point, "Basemap polygon point")) for point in polygon["points"]]
            parts.append(
                f'<polygon points="{point_string(coords)}" fill="{escape(polygon.get("fill", "#cfe6f4"))}" stroke="{escape(polygon.get("stroke", "#96bfd3"))}" stroke-width="{float(polygon.get("stroke_width", 1.5))}" opacity="{float(polygon.get("opacity", 0.96))}" />'
            )

        for line in basemap.get("lines", []):
            coords = [project(*parse_lon_lat_pair(point, "Basemap line point")) for point in line["points"]]
            stroke = escape(line.get("stroke", "#c7a35f"))
            parts.append(
                f'<polyline points="{point_string(coords)}" fill="none" stroke="{stroke}" stroke-width="{float(line.get("stroke_width", 3.2))}" stroke-linecap="round" stroke-linejoin="round" opacity="{float(line.get("opacity", 0.78))}" />'
            )
            if "inner_stroke" in line:
                parts.append(
                    f'<polyline points="{point_string(coords)}" fill="none" stroke="{escape(line["inner_stroke"])}" stroke-width="{float(line.get("inner_stroke_width", 1.3))}" stroke-linecap="round" stroke-linejoin="round" opacity="{float(line.get("inner_opacity", 0.9))}" />'
                )

        for label in basemap.get("labels", []):
            lon, lat = parse_lon_lat_pair(label["coordinate"], "Basemap label")
            x, y = project(lon, lat)
            parts.append(
                f'<text x="{x:.1f}" y="{y:.1f}" text-anchor="middle" font-family="Helvetica, Arial, sans-serif" font-size="{int(label.get("font_size", 16))}" font-style="{escape(label.get("font_style", "italic"))}" fill="{escape(label.get("fill", "#4a7c93"))}" opacity="{float(label.get("opacity", 0.86))}">{escape(label["name"])}</text>'
            )

        for place in basemap.get("places", []):
            lon, lat = parse_lon_lat_pair(place["coordinate"], "Basemap place")
            x, y = project(lon, lat)
            parts.append(f'<circle cx="{x:.1f}" cy="{y:.1f}" r="2.2" fill="#6b7280" opacity="0.72" />')
            parts.append(
                f'<text x="{x + 5:.1f}" y="{y - 5:.1f}" font-family="Helvetica, Arial, sans-serif" font-size="12" fill="#4b5563" opacity="0.88">{escape(place["name"])}</text>'
            )

    for index, point in enumerate(points, start=1):
        actual_x, actual_y = project(float(point["lon"]), float(point["lat"]))
        marker_x = actual_x + float(point["marker_dx"])
        marker_y = actual_y + float(point["marker_dy"])
        fill = styles[str(point["category"])]["fill"]
        if abs(marker_x - actual_x) > 0.1 or abs(marker_y - actual_y) > 0.1:
            parts.append(
                f'<line x1="{actual_x:.1f}" y1="{actual_y:.1f}" x2="{marker_x:.1f}" y2="{marker_y:.1f}" stroke="{fill}" stroke-width="1.1" opacity="0.58" />'
            )
            parts.append(f'<circle cx="{actual_x:.1f}" cy="{actual_y:.1f}" r="3.0" fill="{fill}" opacity="0.62" />')
        parts.append(
            f'<circle cx="{marker_x:.1f}" cy="{marker_y:.1f}" r="12" fill="{fill}" stroke="#fffdf7" stroke-width="2.5" />'
        )
        parts.append(
            f'<text x="{marker_x:.1f}" y="{marker_y + 0.6:.1f}" text-anchor="middle" dominant-baseline="central" font-family="Helvetica, Arial, sans-serif" font-size="10" font-weight="700" fill="#ffffff">{index}</text>'
        )

    parts.extend(
        [
            "</g>",
            f'<rect x="{map_left}" y="{map_top}" width="{map_width}" height="{map_height}" fill="none" stroke="#d4cbbb" stroke-width="1.5" rx="18" />',
            f'<rect x="{panel_left}" y="{panel_top}" width="{panel_width}" height="{panel_height}" fill="#fffdf7" stroke="#d8d2c3" stroke-width="1.3" rx="18" />',
            f'<text x="{panel_left + 22}" y="{panel_top + 34}" font-family="Helvetica, Arial, sans-serif" font-size="18" font-weight="700" fill="#24303a">{escape(config.get("index_title", "Index"))}</text>',
        ]
    )
    index_note = config.get("index_note")
    if index_note:
        parts.append(
            f'<text x="{panel_left + 22}" y="{panel_top + 57}" font-family="Helvetica, Arial, sans-serif" font-size="12" fill="#64717a">{escape(index_note)}</text>'
        )

    legend_y = panel_top + 94
    for offset, category in enumerate(category_order):
        y = legend_y + offset * 25
        style = styles[category]
        parts.append(f'<circle cx="{panel_left + 30}" cy="{y}" r="7" fill="{style["fill"]}" />')
        parts.append(
            f'<text x="{panel_left + 46}" y="{y + 4}" font-family="Helvetica, Arial, sans-serif" font-size="13" fill="#24303a">{escape(style["label"])}</text>'
        )

    list_y = panel_top + 190
    row_height = float(config.get("index_row_height", 24))
    max_label_width = int(config.get("index_label_width", 42))
    for index, point in enumerate(points, start=1):
        y = list_y + (index - 1) * row_height
        style = styles[str(point["category"])]
        label = f'{index:02d} {point["name"]}'
        parts.append(f'<circle cx="{panel_left + 30}" cy="{y - 4}" r="8.5" fill="{style["fill"]}" />')
        parts.append(
            f'<text x="{panel_left + 30}" y="{y - 3.5}" text-anchor="middle" dominant-baseline="central" font-family="Helvetica, Arial, sans-serif" font-size="7.5" font-weight="700" fill="#ffffff">{index}</text>'
        )
        parts.append(
            f'<text x="{panel_left + 47}" y="{y}" font-family="Helvetica, Arial, sans-serif" font-size="12.2" fill="#24303a">{shorten(label, max_label_width)}</text>'
        )

    footer = config.get("footer")
    if footer:
        parts.append(
            f'<text x="{map_left}" y="{height - 38}" font-family="Helvetica, Arial, sans-serif" font-size="12" fill="#68757d">{escape(footer)}</text>'
        )

    parts.append("</svg>")
    return "\n".join(parts) + "\n"


def main() -> None:
    parser = argparse.ArgumentParser(description="Render a generic category map plot SVG from JSON.")
    parser.add_argument("config", type=Path, help="Path to map config JSON")
    parser.add_argument("--output-svg", type=Path, required=True, help="SVG path to write")
    args = parser.parse_args()

    config = load_config(args.config)
    args.output_svg.parent.mkdir(parents=True, exist_ok=True)
    args.output_svg.write_text(render_svg(config), encoding="utf-8")
    print(f"Wrote {args.output_svg}")


if __name__ == "__main__":
    main()
