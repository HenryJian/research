#!/usr/bin/env python3

"""Render a static map plot from a small JSON config.

Point records must provide:
- coordinate: {"lat": number, "lon": number} or [lat, lon]
- name: display name
- category: category key used for marker style and legend

The script can write SVG and can write PNG directly with Pillow. Keep final
document-facing PNGs in topic resources/ and intermediate SVGs under temp/.
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


def shorten_plain(value: str, width: int) -> str:
    return textwrap.shorten(value, width=width, placeholder="...")


def mercator_y(lat: float) -> float:
    lat = max(min(lat, 85.0), -85.0)
    radians = math.radians(lat)
    return math.log(math.tan(math.pi / 4.0 + radians / 2.0))


def config_bool(config: dict[str, Any], key: str, default: bool = False) -> bool:
    value = config.get(key, default)
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        return value.strip().lower() in {"1", "true", "yes", "on"}
    return bool(value)


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


def layout_marker_positions(
    points: list[dict[str, Any]],
    config: dict[str, Any],
    project: Any,
    map_left: float,
    map_top: float,
    map_width: float,
    map_height: float,
) -> list[dict[str, Any]]:
    layouts = []
    for index, point in enumerate(points, start=1):
        actual_x, actual_y = project(float(point["lon"]), float(point["lat"]))
        layouts.append(
            {
                "index": index,
                "point": point,
                "actual_x": actual_x,
                "actual_y": actual_y,
                "marker_x": actual_x + float(point["marker_dx"]),
                "marker_y": actual_y + float(point["marker_dy"]),
                "preferred_dx": float(point["marker_dx"]),
                "preferred_dy": float(point["marker_dy"]),
            }
        )

    if not config_bool(config, "avoid_marker_overlap"):
        return layouts

    marker_radius = float(config.get("marker_radius", 12))
    min_distance = float(config.get("marker_min_distance", marker_radius * 2 + 4))
    edge_padding = float(config.get("marker_edge_padding", marker_radius + 4))
    max_offset = float(config.get("marker_max_offset", 140))
    angle_degrees = [-90, -60, -30, 0, 30, 60, 90, 120, 150, 180, -150, -120, -45, 45, 135, -135]
    ring_distances = [0, 18, 30, 42, 56, 72, 90, 112, max_offset]

    def is_inside(x: float, y: float) -> bool:
        return (
            map_left + edge_padding <= x <= map_left + map_width - edge_padding
            and map_top + edge_padding <= y <= map_top + map_height - edge_padding
        )

    def overlap_penalty(x: float, y: float, placed: list[dict[str, Any]]) -> float:
        penalty = 0.0
        for other in placed:
            distance = math.hypot(x - float(other["marker_x"]), y - float(other["marker_y"]))
            if distance < min_distance:
                penalty += (min_distance - distance) ** 2
        return penalty

    def candidates(layout: dict[str, Any]) -> list[tuple[float, float, float]]:
        actual_x = float(layout["actual_x"])
        actual_y = float(layout["actual_y"])
        preferred_dx = float(layout["preferred_dx"])
        preferred_dy = float(layout["preferred_dy"])
        preferred_length = math.hypot(preferred_dx, preferred_dy)
        angles = list(angle_degrees)
        if preferred_length > 0.1:
            preferred_angle = math.degrees(math.atan2(preferred_dy, preferred_dx))
            angles = [preferred_angle, preferred_angle - 25, preferred_angle + 25, *angles]

        seen: set[tuple[int, int]] = set()
        options: list[tuple[float, float, float]] = []

        def add(dx: float, dy: float) -> None:
            x = actual_x + dx
            y = actual_y + dy
            key = (round(x), round(y))
            if key in seen or not is_inside(x, y):
                return
            seen.add(key)
            movement_cost = math.hypot(dx - preferred_dx, dy - preferred_dy)
            offset_cost = math.hypot(dx, dy) * 0.08
            options.append((movement_cost + offset_cost, x, y))

        add(preferred_dx, preferred_dy)
        if preferred_length > 0.1:
            for radius in ring_distances:
                if radius == 0:
                    continue
                scale = radius / preferred_length
                add(preferred_dx * scale, preferred_dy * scale)

        for radius in ring_distances:
            for angle in angles:
                radians = math.radians(angle)
                add(math.cos(radians) * radius, math.sin(radians) * radius)

        return sorted(options)

    def density(layout: dict[str, Any]) -> tuple[int, int]:
        neighbors = 0
        for other in layouts:
            if other is layout:
                continue
            distance = math.hypot(
                float(layout["actual_x"]) - float(other["actual_x"]),
                float(layout["actual_y"]) - float(other["actual_y"]),
            )
            if distance < min_distance * 2.6:
                neighbors += 1
        return -neighbors, int(layout["index"])

    placed: list[dict[str, Any]] = []
    for layout in sorted(layouts, key=density):
        best: tuple[float, float, float] | None = None
        best_score = float("inf")
        for base_score, x, y in candidates(layout):
            penalty = overlap_penalty(x, y, placed)
            score = base_score + penalty * 1000
            if penalty == 0:
                best = (score, x, y)
                break
            if score < best_score:
                best_score = score
                best = (score, x, y)
        if best is not None:
            _, layout["marker_x"], layout["marker_y"] = best
        placed.append(layout)

    return layouts


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

    if config_bool(config, "preserve_aspect"):
        lon_unit_min = math.radians(lon_min)
        lon_unit_max = math.radians(lon_max)
        lon_span = lon_unit_max - lon_unit_min
        merc_span = merc_max - merc_min
        scale = min(map_width / lon_span, map_height / merc_span)
        x_origin = map_left + (map_width - lon_span * scale) / 2.0
        y_origin = map_top + (map_height - merc_span * scale) / 2.0

        def project(lon: float, lat: float) -> tuple[float, float]:
            x = x_origin + (math.radians(lon) - lon_unit_min) * scale
            y = y_origin + (merc_max - mercator_y(lat)) * scale
            return x, y

    else:

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
            radius = float(place.get("radius", 2.2))
            fill = escape(place.get("fill", "#6b7280"))
            opacity = float(place.get("opacity", 0.72))
            label_dx = float(place.get("label_dx", 5))
            label_dy = float(place.get("label_dy", -5))
            font_size = int(place.get("font_size", 12))
            font_weight = escape(place.get("font_weight", "400"))
            label_fill = escape(place.get("label_fill", "#4b5563"))
            label_opacity = float(place.get("label_opacity", 0.88))
            parts.append(f'<circle cx="{x:.1f}" cy="{y:.1f}" r="{radius}" fill="{fill}" opacity="{opacity}" />')
            parts.append(
                f'<text x="{x + label_dx:.1f}" y="{y + label_dy:.1f}" font-family="Helvetica, Arial, sans-serif" font-size="{font_size}" font-weight="{font_weight}" fill="{label_fill}" opacity="{label_opacity}">{escape(place["name"])}</text>'
            )

    north_arrow = config.get("north_arrow", False)
    if north_arrow:
        arrow_config = north_arrow if isinstance(north_arrow, dict) else {}
        arrow_x = float(arrow_config.get("x", map_left + map_width - 42))
        arrow_y = float(arrow_config.get("y", map_top + 58))
        arrow_fill = escape(arrow_config.get("fill", "#24303a"))
        arrow_label = escape(arrow_config.get("label", "N"))
        parts.append(
            f'<polygon points="{arrow_x:.1f},{arrow_y - 28:.1f} {arrow_x - 8:.1f},{arrow_y - 8:.1f} {arrow_x:.1f},{arrow_y - 13:.1f} {arrow_x + 8:.1f},{arrow_y - 8:.1f}" fill="{arrow_fill}" opacity="0.9" />'
        )
        parts.append(
            f'<line x1="{arrow_x:.1f}" y1="{arrow_y - 10:.1f}" x2="{arrow_x:.1f}" y2="{arrow_y + 14:.1f}" stroke="{arrow_fill}" stroke-width="2.2" stroke-linecap="round" opacity="0.86" />'
        )
        parts.append(
            f'<text x="{arrow_x:.1f}" y="{arrow_y + 34:.1f}" text-anchor="middle" font-family="Helvetica, Arial, sans-serif" font-size="13" font-weight="700" fill="{arrow_fill}" opacity="0.9">{arrow_label}</text>'
        )

    marker_layouts = layout_marker_positions(points, config, project, map_left, map_top, map_width, map_height)
    for marker_layout in marker_layouts:
        index = marker_layout["index"]
        point = marker_layout["point"]
        actual_x = float(marker_layout["actual_x"])
        actual_y = float(marker_layout["actual_y"])
        marker_x = float(marker_layout["marker_x"])
        marker_y = float(marker_layout["marker_y"])
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


def render_png(config: dict[str, Any], output_path: Path) -> None:
    try:
        from PIL import Image, ImageDraw, ImageFont
    except ImportError as exc:
        raise RuntimeError("PNG rendering requires Pillow in the active Python environment") from exc

    points = normalize_points(config["points"])
    styles = category_styles(points, config)
    category_order = sorted(styles, key=lambda category: (float(styles[category]["order"]), category))

    scale = int(config.get("png_scale", 2))
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

    def s(value: float) -> int:
        return int(round(value * scale))

    def color(value: object, alpha: float = 1.0) -> tuple[int, int, int, int]:
        text = str(value).strip()
        if text.startswith("#"):
            text = text[1:]
        if len(text) == 3:
            text = "".join(ch * 2 for ch in text)
        red = int(text[0:2], 16)
        green = int(text[2:4], 16)
        blue = int(text[4:6], 16)
        return red, green, blue, max(0, min(255, int(round(alpha * 255))))

    def font(size: int, weight: str = "400", italic: bool = False) -> Any:
        candidates = []
        if weight in {"700", "bold", "Bold"} and italic:
            candidates = [
                "/System/Library/Fonts/Supplemental/Arial Bold Italic.ttf",
                "/Library/Fonts/Arial Bold Italic.ttf",
            ]
        elif weight in {"700", "bold", "Bold"}:
            candidates = [
                "/System/Library/Fonts/Supplemental/Arial Bold.ttf",
                "/Library/Fonts/Arial Bold.ttf",
                "/System/Library/Fonts/Supplemental/Arial.ttf",
            ]
        elif italic:
            candidates = [
                "/System/Library/Fonts/Supplemental/Arial Italic.ttf",
                "/Library/Fonts/Arial Italic.ttf",
                "/System/Library/Fonts/Supplemental/Arial.ttf",
            ]
        else:
            candidates = [
                "/System/Library/Fonts/Supplemental/Arial.ttf",
                "/Library/Fonts/Arial.ttf",
            ]
        for candidate in candidates:
            try:
                return ImageFont.truetype(candidate, s(size))
            except OSError:
                continue
        return ImageFont.load_default(size=s(size))

    def text_size(draw: Any, text: str, text_font: Any) -> tuple[float, float]:
        bbox = draw.textbbox((0, 0), text, font=text_font)
        return bbox[2] - bbox[0], bbox[3] - bbox[1]

    lats = [float(point["lat"]) for point in points]
    lons = [float(point["lon"]) for point in points]
    bounds = config.get("bounds")
    if isinstance(bounds, dict):
        lat_min = float(bounds["lat_min"])
        lat_max = float(bounds["lat_max"])
        lon_min = float(bounds["lon_min"])
        lon_max = float(bounds["lon_max"])
    else:
        lat_min = min(lats) - float(config.get("lat_padding", 0.35))
        lat_max = max(lats) + float(config.get("lat_padding_top", 0.30))
        lon_min = min(lons) - float(config.get("lon_padding", 0.45))
        lon_max = max(lons) + float(config.get("lon_padding_right", 0.32))

    merc_min = mercator_y(lat_min)
    merc_max = mercator_y(lat_max)

    if config_bool(config, "preserve_aspect"):
        lon_unit_min = math.radians(lon_min)
        lon_unit_max = math.radians(lon_max)
        lon_span = lon_unit_max - lon_unit_min
        merc_span = merc_max - merc_min
        render_scale = min(map_width / lon_span, map_height / merc_span)
        x_origin = map_left + (map_width - lon_span * render_scale) / 2.0
        y_origin = map_top + (map_height - merc_span * render_scale) / 2.0

        def project(lon: float, lat: float) -> tuple[float, float]:
            x = x_origin + (math.radians(lon) - lon_unit_min) * render_scale
            y = y_origin + (merc_max - mercator_y(lat)) * render_scale
            return x, y

    else:

        def project(lon: float, lat: float) -> tuple[float, float]:
            x = map_left + (lon - lon_min) * map_width / (lon_max - lon_min)
            y = map_bottom - (mercator_y(lat) - merc_min) * map_height / (merc_max - merc_min)
            return x, y

    image = Image.new("RGB", (s(width), s(height)), "#fbfaf4")
    draw = ImageDraw.Draw(image)

    title_font = font(28, "700")
    subtitle_font = font(15)
    draw.text((s(map_left), s(24)), str(config.get("title", "Map plot")), font=title_font, fill="#24303a")
    subtitle = config.get("subtitle")
    if subtitle:
        draw.text((s(map_left), s(61)), str(subtitle), font=subtitle_font, fill="#57636c")

    map_layer = Image.new("RGBA", (s(width), s(height)), (0, 0, 0, 0))
    map_draw = ImageDraw.Draw(map_layer)
    map_box = [s(map_left), s(map_top), s(map_left + map_width), s(map_top + map_height)]
    map_draw.rounded_rectangle(map_box, radius=s(18), fill=color("#f4efe4"), outline=color("#d4cbbb"), width=s(1.5))

    basemap = config.get("basemap", {})
    if isinstance(basemap, dict):
        for polygon in basemap.get("polygons", []):
            coords = [(s(x), s(y)) for x, y in [project(*parse_lon_lat_pair(point, "Basemap polygon point")) for point in polygon["points"]]]
            map_draw.polygon(coords, fill=color(polygon.get("fill", "#cfe6f4"), float(polygon.get("opacity", 0.96))))
            map_draw.line(coords + [coords[0]], fill=color(polygon.get("stroke", "#96bfd3")), width=s(float(polygon.get("stroke_width", 1.5))), joint="curve")

        for line in basemap.get("lines", []):
            coords = [(s(x), s(y)) for x, y in [project(*parse_lon_lat_pair(point, "Basemap line point")) for point in line["points"]]]
            map_draw.line(coords, fill=color(line.get("stroke", "#c7a35f"), float(line.get("opacity", 0.78))), width=s(float(line.get("stroke_width", 3.2))), joint="curve")
            if "inner_stroke" in line:
                map_draw.line(coords, fill=color(line["inner_stroke"], float(line.get("inner_opacity", 0.9))), width=s(float(line.get("inner_stroke_width", 1.3))), joint="curve")

        for label in basemap.get("labels", []):
            lon, lat = parse_lon_lat_pair(label["coordinate"], "Basemap label")
            x, y = project(lon, lat)
            label_font = font(int(label.get("font_size", 16)), italic=label.get("font_style", "italic") == "italic")
            label_text = str(label["name"])
            label_width, label_height = text_size(map_draw, label_text, label_font)
            map_draw.text((s(x) - label_width / 2, s(y) - label_height / 2), label_text, font=label_font, fill=color(label.get("fill", "#4a7c93"), float(label.get("opacity", 0.86))))

        for place in basemap.get("places", []):
            lon, lat = parse_lon_lat_pair(place["coordinate"], "Basemap place")
            x, y = project(lon, lat)
            radius = float(place.get("radius", 2.2))
            place_fill = place.get("fill", "#6b7280")
            opacity = float(place.get("opacity", 0.72))
            label_dx = float(place.get("label_dx", 5))
            label_dy = float(place.get("label_dy", -5))
            font_size = int(place.get("font_size", 12))
            font_weight = str(place.get("font_weight", "400"))
            label_fill = place.get("label_fill", "#4b5563")
            label_opacity = float(place.get("label_opacity", 0.88))
            map_draw.ellipse([s(x - radius), s(y - radius), s(x + radius), s(y + radius)], fill=color(place_fill, opacity))
            map_draw.text((s(x + label_dx), s(y + label_dy)), str(place["name"]), font=font(font_size, font_weight), fill=color(label_fill, label_opacity))

    north_arrow = config.get("north_arrow", False)
    if north_arrow:
        arrow_config = north_arrow if isinstance(north_arrow, dict) else {}
        arrow_x = float(arrow_config.get("x", map_left + map_width - 42))
        arrow_y = float(arrow_config.get("y", map_top + 58))
        arrow_fill = arrow_config.get("fill", "#24303a")
        arrow_label = str(arrow_config.get("label", "N"))
        map_draw.polygon(
            [
                (s(arrow_x), s(arrow_y - 28)),
                (s(arrow_x - 8), s(arrow_y - 8)),
                (s(arrow_x), s(arrow_y - 13)),
                (s(arrow_x + 8), s(arrow_y - 8)),
            ],
            fill=color(arrow_fill, 0.9),
        )
        map_draw.line(
            (s(arrow_x), s(arrow_y - 10), s(arrow_x), s(arrow_y + 14)),
            fill=color(arrow_fill, 0.86),
            width=s(2.2),
        )
        arrow_font = font(13, "700")
        label_width, _ = text_size(map_draw, arrow_label, arrow_font)
        map_draw.text((s(arrow_x) - label_width / 2, s(arrow_y + 20)), arrow_label, font=arrow_font, fill=color(arrow_fill, 0.9))

    marker_layouts = layout_marker_positions(points, config, project, map_left, map_top, map_width, map_height)
    for marker_layout in marker_layouts:
        index = marker_layout["index"]
        point = marker_layout["point"]
        actual_x = float(marker_layout["actual_x"])
        actual_y = float(marker_layout["actual_y"])
        marker_x = float(marker_layout["marker_x"])
        marker_y = float(marker_layout["marker_y"])
        fill = styles[str(point["category"])]["fill"]
        if abs(marker_x - actual_x) > 0.1 or abs(marker_y - actual_y) > 0.1:
            map_draw.line((s(actual_x), s(actual_y), s(marker_x), s(marker_y)), fill=color(fill, 0.58), width=s(1.1))
            map_draw.ellipse([s(actual_x - 3), s(actual_y - 3), s(actual_x + 3), s(actual_y + 3)], fill=color(fill, 0.62))
        map_draw.ellipse([s(marker_x - 12), s(marker_y - 12), s(marker_x + 12), s(marker_y + 12)], fill=color("#fffdf7"))
        map_draw.ellipse([s(marker_x - 9.5), s(marker_y - 9.5), s(marker_x + 9.5), s(marker_y + 9.5)], fill=color(fill))
        number_font = font(10, "700")
        number = str(index)
        number_width, number_height = text_size(map_draw, number, number_font)
        map_draw.text((s(marker_x) - number_width / 2, s(marker_y) - number_height / 2), number, font=number_font, fill="#ffffff")

    mask = Image.new("L", (s(width), s(height)), 0)
    mask_draw = ImageDraw.Draw(mask)
    mask_draw.rounded_rectangle(map_box, radius=s(18), fill=255)
    image = Image.composite(Image.alpha_composite(image.convert("RGBA"), map_layer).convert("RGB"), image, mask)
    draw = ImageDraw.Draw(image)
    draw.rounded_rectangle(map_box, radius=s(18), outline="#d4cbbb", width=s(1.5))

    panel_box = [s(panel_left), s(panel_top), s(panel_left + panel_width), s(panel_top + panel_height)]
    draw.rounded_rectangle(panel_box, radius=s(18), fill="#fffdf7", outline="#d8d2c3", width=s(1.3))
    draw.text((s(panel_left + 22), s(panel_top + 14)), str(config.get("index_title", "Index")), font=font(18, "700"), fill="#24303a")
    index_note = config.get("index_note")
    if index_note:
        draw.text((s(panel_left + 22), s(panel_top + 45)), str(index_note), font=font(12), fill="#64717a")

    legend_y = panel_top + 94
    for offset, category in enumerate(category_order):
        y = legend_y + offset * 25
        style = styles[category]
        draw.ellipse([s(panel_left + 23), s(y - 7), s(panel_left + 37), s(y + 7)], fill=style["fill"])
        draw.text((s(panel_left + 46), s(y - 9)), str(style["label"]), font=font(13), fill="#24303a")

    list_y = panel_top + 190
    row_height = float(config.get("index_row_height", 24))
    max_label_width = int(config.get("index_label_width", 42))
    for index, point in enumerate(points, start=1):
        y = list_y + (index - 1) * row_height
        style = styles[str(point["category"])]
        label = shorten_plain(f'{index:02d} {point["name"]}', max_label_width)
        draw.ellipse([s(panel_left + 21.5), s(y - 12.5), s(panel_left + 38.5), s(y + 4.5)], fill=style["fill"])
        item_font = font(7.5, "700")
        index_text = str(index)
        index_width, index_height = text_size(draw, index_text, item_font)
        draw.text((s(panel_left + 30) - index_width / 2, s(y - 4) - index_height / 2), index_text, font=item_font, fill="#ffffff")
        draw.text((s(panel_left + 47), s(y - 12)), label, font=font(12), fill="#24303a")

    footer = config.get("footer")
    if footer:
        draw.text((s(map_left), s(height - 48)), str(footer), font=font(12), fill="#68757d")

    if scale != 1:
        image = image.resize((width, height), Image.Resampling.LANCZOS)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    image.save(output_path)


def main() -> None:
    parser = argparse.ArgumentParser(description="Render a generic category map plot from JSON.")
    parser.add_argument("config", type=Path, help="Path to map config JSON")
    parser.add_argument("--output-svg", type=Path, help="SVG path to write")
    parser.add_argument("--output-png", type=Path, help="PNG path to write using Pillow")
    args = parser.parse_args()

    if not args.output_svg and not args.output_png:
        parser.error("At least one of --output-svg or --output-png is required")

    config = load_config(args.config)
    if args.output_svg:
        args.output_svg.parent.mkdir(parents=True, exist_ok=True)
        args.output_svg.write_text(render_svg(config), encoding="utf-8")
        print(f"Wrote {args.output_svg}")
    if args.output_png:
        render_png(config, args.output_png)
        print(f"Wrote {args.output_png}")


if __name__ == "__main__":
    main()
