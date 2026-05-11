"""
Sentinel-2 vegetation indices via Element84 Earth Search (free, no auth).

Finds the latest cloud-free Sentinel-2 L2A scene over a lat/lon point and
computes four indices from Cloud-Optimised GeoTIFF pixels:

  NDVI  = (NIR - Red)   / (NIR + Red)          — vegetation density
  LAI   = -ln(1 - NDVI/0.95) / 0.5             — leaf area index (Beer-Lambert)
  NDWI  = (NIR - SWIR1.6) / (NIR + SWIR1.6)     — vegetation water stress (Gao 1996)
  NDRE  = (NIR - RedEdge1) / (NIR + RedEdge1)  — chlorophyll / nitrogen status

All results are cached in memory with a 5-day TTL (Sentinel-2 revisit time).
"""

from __future__ import annotations

import logging
import time
from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import date, datetime, timedelta, timezone

import numpy as np
import rasterio
from pyproj import Transformer
from pystac_client import Client
from rasterio.windows import Window

logger = logging.getLogger(__name__)

_STAC_URL = "https://earth-search.aws.element84.com/v1"
_COLLECTION = "sentinel-2-l2a"
_CACHE_TTL_SECONDS = 5 * 24 * 3600  # 5 days

_cache: dict[str, tuple[dict, float]] = {}  # key → (indices_dict, expires_at)


_K_CANOPY = 0.45  # extinction coefficient for agricultural crops (wheat/maize range)


def _ndvi_to_lai(ndvi: float) -> float:
    ndvi_clipped = max(0.0, min(ndvi, 0.94))
    fpar = min(ndvi_clipped / 0.95, 0.99)
    return float(-np.log(1.0 - fpar) / _K_CANOPY)


def _read_pixel(href: str, lon: float, lat: float) -> float:
    """Read a single pixel value from a Cloud-Optimised GeoTIFF."""
    with rasterio.open(href) as ds:
        transformer = Transformer.from_crs("EPSG:4326", ds.crs, always_xy=True)
        x, y = transformer.transform(lon, lat)
        row, col = ds.index(x, y)
        row = max(0, min(row, ds.height - 1))
        col = max(0, min(col, ds.width - 1))
        value = ds.read(1, window=Window(col, row, 1, 1))[0, 0]
    return float(value)


def _safe_index(b1: float, b2: float) -> float:
    denom = b1 + b2
    return float((b1 - b2) / denom) if abs(denom) > 1e-6 else 0.0


def fetch_indices(
    latitude: float,
    longitude: float,
    max_cloud_cover: float = 30.0,
    lookback_days: int = 30,
) -> dict:
    """
    Return a dict with ndvi, lai, ndwi, ndre, scene_date for the most recent
    suitable Sentinel-2 scene.

    Raises RuntimeError if no suitable scene is found.
    """
    cache_key = f"{latitude:.4f}_{longitude:.4f}"
    cached = _cache.get(cache_key)
    if cached and cached[1] > time.monotonic():
        logger.debug("Sentinel-2 cache hit for %s", cache_key)
        return cached[0]

    now = datetime.now(timezone.utc)
    start = (now - timedelta(days=lookback_days)).strftime("%Y-%m-%dT00:00:00Z")
    end = now.strftime("%Y-%m-%dT23:59:59Z")

    bbox = [longitude - 0.01, latitude - 0.01, longitude + 0.01, latitude + 0.01]

    catalog = Client.open(_STAC_URL)
    results = catalog.search(
        collections=[_COLLECTION],
        bbox=bbox,
        datetime=f"{start}/{end}",
        query={"eo:cloud_cover": {"lt": max_cloud_cover}},
        max_items=10,
    )

    items = sorted(
        results.items(),
        key=lambda i: i.datetime or datetime.min.replace(tzinfo=timezone.utc),
        reverse=True,
    )
    if not items:
        raise RuntimeError(
            f"No cloud-free Sentinel-2 scene found for ({latitude}, {longitude}) "
            f"in the last {lookback_days} days with cloud cover < {max_cloud_cover}%"
        )

    item = items[0]
    scene_date = item.datetime.strftime("%Y-%m-%d") if item.datetime else "unknown"
    logger.info("Using Sentinel-2 scene from %s (cloud cover: %.1f%%)",
                scene_date, item.properties.get("eo:cloud_cover", 0))

    b04  = _read_pixel(item.assets["red"].href,      longitude, latitude) / 10000.0
    b08  = _read_pixel(item.assets["nir"].href,      longitude, latitude) / 10000.0
    swir = _read_pixel(item.assets["swir16"].href,   longitude, latitude) / 10000.0
    bre  = _read_pixel(item.assets["rededge1"].href, longitude, latitude) / 10000.0

    if b04 + b08 < 1e-6:
        raise RuntimeError("Invalid pixel values (both NIR+Red are zero — likely nodata)")

    ndvi = _safe_index(b08, b04)
    lai  = _ndvi_to_lai(ndvi)
    ndwi = _safe_index(b08, swir)   # Gao 1996: (NIR-SWIR)/(NIR+SWIR)
    ndre = _safe_index(b08, bre)

    logger.info(
        "Sentinel-2 NDVI=%.3f LAI=%.2f NDWI=%.3f NDRE=%.3f (scene: %s)",
        ndvi, lai, ndwi, ndre, scene_date,
    )

    result = {
        "ndvi":       round(ndvi, 3),
        "lai":        round(lai,  2),
        "ndwi":       round(ndwi, 3),
        "ndre":       round(ndre, 3),
        "scene_date": scene_date,
    }
    _cache[cache_key] = (result, time.monotonic() + _CACHE_TTL_SECONDS)
    return result


def fetch_lai(
    latitude: float,
    longitude: float,
    max_cloud_cover: float = 30.0,
    lookback_days: int = 30,
) -> tuple[float, str]:
    """Convenience wrapper — returns (lai, scene_date_str)."""
    indices = fetch_indices(latitude, longitude, max_cloud_cover, lookback_days)
    return indices["lai"], indices["scene_date"]


def fetch_season_indices(
    latitude: float,
    longitude: float,
    start_date: date,
    end_date: date,
    max_cloud_cover: float = 35.0,
) -> list[dict]:
    """
    Fetch all available Sentinel-2 scenes between start_date and end_date.

    Groups scenes into 2-week windows, picks the least cloudy from each window,
    then reads pixels in parallel. Returns a list of index dicts ordered by date:
      [{date, ndvi, lai, ndwi, ndre}, ...]
    """
    start_str = f"{start_date.isoformat()}T00:00:00Z"
    end_str   = f"{end_date.isoformat()}T23:59:59Z"
    bbox = [longitude - 0.01, latitude - 0.01, longitude + 0.01, latitude + 0.01]

    catalog = Client.open(_STAC_URL)
    results = catalog.search(
        collections=[_COLLECTION],
        bbox=bbox,
        datetime=f"{start_str}/{end_str}",
        query={"eo:cloud_cover": {"lt": max_cloud_cover}},
        max_items=300,
    )

    items = sorted(
        results.items(),
        key=lambda i: i.datetime or datetime.min.replace(tzinfo=timezone.utc),
    )
    if not items:
        logger.warning("No Sentinel-2 scenes found for (%s, %s) between %s and %s",
                       latitude, longitude, start_date, end_date)
        return []

    # One scene per 2-week window — pick least cloudy
    epoch = items[0].datetime or datetime.now(timezone.utc)
    groups: dict[int, list] = defaultdict(list)
    for item in items:
        dt = item.datetime or epoch
        window = (dt - epoch).days // 14
        groups[window].append(item)

    selected = [
        min(g, key=lambda i: i.properties.get("eo:cloud_cover", 100))
        for g in groups.values()
    ]
    selected.sort(key=lambda i: i.datetime or datetime.min.replace(tzinfo=timezone.utc))
    logger.info("Sentinel-2 season: %d scenes selected from %d available", len(selected), len(items))

    def _read_scene(item) -> dict | None:
        try:
            scene_date = item.datetime.strftime("%Y-%m-%d") if item.datetime else "unknown"
            b04  = _read_pixel(item.assets["red"].href,      longitude, latitude) / 10000.0
            b08  = _read_pixel(item.assets["nir"].href,      longitude, latitude) / 10000.0
            swir = _read_pixel(item.assets["swir16"].href,   longitude, latitude) / 10000.0
            bre  = _read_pixel(item.assets["rededge1"].href, longitude, latitude) / 10000.0
            if b04 + b08 < 1e-6:
                return None
            ndvi = _safe_index(b08, b04)
            return {
                "date": scene_date,
                "ndvi": round(ndvi, 3),
                "lai":  round(_ndvi_to_lai(ndvi), 2),
                "ndwi": round(_safe_index(b08, swir), 3),
                "ndre": round(_safe_index(b08, bre), 3),
            }
        except Exception as e:
            logger.warning("Scene read failed (%s): %s", item.datetime, e)
            return None

    records: list[dict] = []
    with ThreadPoolExecutor(max_workers=4) as pool:
        futures = {pool.submit(_read_scene, item): item for item in selected}
        for fut in as_completed(futures):
            result = fut.result()
            if result:
                records.append(result)

    records.sort(key=lambda r: r["date"])
    return records
