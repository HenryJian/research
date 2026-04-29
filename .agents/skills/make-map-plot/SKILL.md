---
name: make-map-plot
description: Create or update repository map plots for research documents from point configs where each point has coordinate, name, and category fields. Use when Codex needs to build a static map image, add a basemap, plot buyer/supplier/competitor/delivery/route/market-area locations, regenerate map artifacts, or keep map intermediates out of topic directories.
---

# Make Map Plot

## Overview

Create practical map plots for this farm research repository. Keep final user-facing assets in the topic's `resources/` directory, and keep scratch files in the repo-level `temp/` directory.

## Workflow

1. Identify the document, map purpose, and inclusion rules before plotting. For buyer lead maps, respect each file's scope rules, exclusions, and current relationship constraints.
2. Put durable point/config data in the topic directory's `resources/` subdirectory. Each point must provide `coordinate`, `name`, and `category`.
3. Use `scripts/make_map_plot.py` from this skill to render a temporary SVG from the JSON config. Store only durable, document-facing assets in `resources/`, usually a PNG referenced by markdown. Store intermediate SVGs, geocode caches, downloaded geographic data, render screenshots, and raw scratch outputs under `temp/<topic>/<plot-name>/`.
4. Do not invent locations. Use source addresses, cached coordinates, documented manual fallback coordinates, or current geocoding. If network geocoding is needed and sandbox DNS fails, request escalation for that command.
5. Include a real geographic context layer: a basemap, simplified water/land/city/road layer, or other meaningful geography. A bare latitude-longitude grid is not enough unless the user explicitly asks for one.
6. For dense maps, use numbered markers plus a side index instead of overlapping long labels. Include a legend and a short note that the map is for planning, not navigation.
7. Render the final image from the intermediate source, then visually inspect it. Check marker count, out-of-scope exclusions, readable labels, nonblank rendering, and markdown links.

## Point Config

Use a JSON config with a `points` array. The reusable script requires these fields for each point:

```json
{
  "coordinate": {"lat": 43.552584, "lon": -80.3046717},
  "name": "Green Liner Produce",
  "category": "High"
}
```

The script also accepts optional `marker_offset: [dx, dy]` for dense maps and optional top-level `categories` and `basemap` sections for styling and geographic context.

## File Placement

Use this pattern unless a local file says otherwise:

```python
REPO_ROOT = Path(__file__).resolve().parents[1]
TOPIC = Path(__file__).resolve().parent.name
RESOURCE_ROOT = Path(__file__).resolve().parent / "resources"
TEMP_ROOT = REPO_ROOT / "temp" / TOPIC / "plot-name"
SVG_PATH = TEMP_ROOT / "plot-name.svg"
CACHE_PATH = TEMP_ROOT / "plot-name-geocode-cache.json"
PNG_PATH = RESOURCE_ROOT / "plot-name.png"
TEMP_ROOT.mkdir(parents=True, exist_ok=True)
RESOURCE_ROOT.mkdir(parents=True, exist_ok=True)
```

Commit the generator, `resources/` assets, and markdown changes when appropriate. Do not commit files under `temp/` unless the user explicitly asks for a scratch artifact to become durable research material. Keep raw source documents, datasets, and PDFs in existing `raw/` folders unless the user asks to reorganize source material too.

## Rendering

First render SVG from config:

```bash
.venv/bin/python .agents/skills/make-map-plot/scripts/make_map_plot.py \
  /path/to/topic/resources/map_points.json \
  --output-svg /path/to/repo/temp/topic/plot-name/map.svg
```

For an SVG-backed static plot, render with headless Chrome or another existing repo tool. Example:

```bash
'/Applications/Google Chrome.app/Contents/MacOS/Google Chrome' \
  --headless --disable-gpu --window-size=1400,980 \
  --screenshot='/path/to/topic/resources/map.png' \
  'file:///path/to/repo/temp/topic/plot-name/map.svg'
```

After rendering, inspect the image visually and run a text/search check for excluded records when the map has explicit exclusion rules.
