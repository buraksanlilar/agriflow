"""
Sentinel-2 LAI estimator via Element84 Earth Search (free, no auth).

Finds the latest cloud-free Sentinel-2 L2A scene over a lat/lon point,
reads the B04 (Red) and B08 (NIR) Cloud-Optimised GeoTIFF pixels,
computes NDVI, then converts to LAI using Beer-Lambert approximation.

LAI = -ln(1 - min(NDVI / 0.95, 0.99)) / 0.5

Result is cached in memory with a 5-day TTL (Sentinel-2 revisit time).
"""

from __future__ import annotations

import logging
import time
from datetime import datetime, timedelta, timezone

import numpy as np
import rasterio
from pyproj import Transformer
from pystac_client import Client
from rasterio.windows import Window

logger = logging.getLogger(__name__)

_STAC_URL = "https://earth-search.aws.element84.com/v1"
_COLLECTION = "sentinel-2-l2a"
_CACHE_TTL_SECONDS = 5 * 24 * 3600  # 5 days

_cache: dict[str, tuple[float, float]] = {}  # key → (lai, expires_at)


def _ndvi_to_lai(ndvi: float) -> float:
    ndvi_clipped = max(0.0, min(ndvi, 0.94))
    fpar = ndvi_clipped / 0.95
    fpar = min(fpar, 0.99)
    return float(-np.log(1.0 - fpar) / 0.5)


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


def fetch_lai(
    latitude: float,
    longitude: float,
    max_cloud_cover: float = 30.0,
    lookback_days: int = 30,
) -> tuple[float, str]:
    """
    Return (lai_value, scene_date_str) for the most recent suitable Sentinel-2 scene.

    Raises RuntimeError if no suitable scene is found.
    """
    cache_key = f"{latitude:.4f}_{longitude:.4f}"
    cached = _cache.get(cache_key)
    if cached and cached[1] > time.monotonic():
        lai, _ = cached
        logger.debug("Sentinel-2 LAI cache hit for %s: %.2f", cache_key, lai)
        return lai, "cached"

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

    # Sort client-side (newest first) — sortby not supported by all STAC APIs
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

    b04_href = item.assets["red"].href
    b08_href = item.assets["nir"].href

    b04 = _read_pixel(b04_href, longitude, latitude) / 10000.0
    b08 = _read_pixel(b08_href, longitude, latitude) / 10000.0

    if b04 + b08 < 1e-6:
        raise RuntimeError("Invalid pixel values (both bands are zero — likely nodata)")

    ndvi = (b08 - b04) / (b08 + b04)
    lai = _ndvi_to_lai(ndvi)

    logger.info("Sentinel-2 NDVI=%.3f → LAI=%.2f (scene: %s)", ndvi, lai, scene_date)

    _cache[cache_key] = (lai, time.monotonic() + _CACHE_TTL_SECONDS)
    return lai, scene_date
