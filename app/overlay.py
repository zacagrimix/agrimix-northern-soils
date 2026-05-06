"""Render pixel-level soil/rainfall/pH overlays for the lookup map.

Reads the SLGA ASC 90 m soil raster (and the WorldClim 2.5 min MAP grid +
SoilGrids pH 0-30 cm aggregate when needed), then for each layer paints
matching pixels in that layer's colour onto its own transparent RGBA image.

A "layer" is (soil_codes_filter, band_ranges_filter, ph_ranges_filter, rgba).
Each layer runs against the same windowed/decimated rasters and shares the
region polygon mask. Empty filters mean "any".

Note: pH raster is stored as int16 with values = pH × 10 (e.g. 65 = pH 6.5),
nodata = -32768. Filters are passed in pH units (e.g. (5.5, 6.0)) and scaled
internally.
"""
from pathlib import Path

import numpy as np
import rasterio
from rasterio.features import geometry_mask
from rasterio.transform import from_bounds as transform_from_bounds
from rasterio.warp import Resampling, reproject
from rasterio.windows import from_bounds as window_from_bounds
import shapely.geometry
import shapely.ops
import shapely.wkt

PROJECT_ROOT = Path(__file__).resolve().parent.parent
SLGA_PATH = PROJECT_ROOT / "data" / "raw" / "slga" / "ASC_EV_C_P_AU_TRN_N.cog.tif"
MAP_PATH = PROJECT_ROOT / "data" / "processed" / "MAP_aus_2.5min.tif"
PH_PATH = PROJECT_ROOT / "data" / "processed" / "pH_water_0-30cm_north_500m.tif"

NORTH_AUS_BBOX = (113.0, -33.0, 154.0, -10.0)


def render_overlay(
    region_geom_wkts: tuple[str, ...] = (),
    layers: tuple[tuple, ...] = (),
    max_px: int = 800,
) -> tuple[list[np.ndarray], list[list[float]]]:
    """Return (per_layer_rgbas, [[south, west], [north, east]]).

    Each layer = (soil_codes, band_ranges, ph_ranges, (r, g, b, a))
    Returns one RGBA image per layer (same order as input). Pixels matching
    that layer's filters AND the region polygon are painted; everything else
    in that layer is transparent.
    """
    if region_geom_wkts:
        geoms = [shapely.wkt.loads(w) for w in region_geom_wkts]
        region_geom = (
            geoms[0] if len(geoms) == 1 else shapely.ops.unary_union(geoms)
        )
        minx, miny, maxx, maxy = region_geom.bounds
        pad = 0.1
        minx, miny, maxx, maxy = minx - pad, miny - pad, maxx + pad, maxy + pad
    else:
        region_geom = None
        minx, miny, maxx, maxy = NORTH_AUS_BBOX

    with rasterio.open(SLGA_PATH) as src:
        win = window_from_bounds(minx, miny, maxx, maxy, src.transform)
        win_h = max(1, int(round(win.height)))
        win_w = max(1, int(round(win.width)))
        scale = max(1.0, max(win_h, win_w) / max_px)
        out_h = max(1, int(win_h / scale))
        out_w = max(1, int(win_w / scale))
        soil = src.read(
            1, window=win, out_shape=(out_h, out_w),
            resampling=Resampling.nearest,
        )

    out_transform = transform_from_bounds(minx, miny, maxx, maxy, out_w, out_h)

    needs_rain = any(layer[1] for layer in layers)
    rain = None
    if needs_rain:
        with rasterio.open(MAP_PATH) as rsrc:
            rain = np.empty((out_h, out_w), dtype=np.float32)
            reproject(
                source=rasterio.band(rsrc, 1),
                destination=rain,
                dst_transform=out_transform,
                dst_crs="EPSG:4326",
                resampling=Resampling.nearest,
            )

    needs_ph = any(layer[2] for layer in layers)
    ph_x10 = None
    if needs_ph:
        with rasterio.open(PH_PATH) as psrc:
            ph_x10 = np.empty((out_h, out_w), dtype=np.int16)
            reproject(
                source=rasterio.band(psrc, 1),
                destination=ph_x10,
                dst_transform=out_transform,
                dst_crs="EPSG:4326",
                resampling=Resampling.nearest,
                src_nodata=-32768,
                dst_nodata=-32768,
            )

    region_mask = None
    if region_geom is not None:
        region_mask = geometry_mask(
            [shapely.geometry.mapping(region_geom)],
            transform=out_transform,
            invert=True,
            out_shape=(out_h, out_w),
        )

    per_layer_rgba: list[np.ndarray] = []
    for soil_codes_f, band_ranges_f, ph_ranges_f, color in layers:
        if soil_codes_f:
            mask = np.isin(
                soil, np.asarray(soil_codes_f, dtype=soil.dtype)
            )
        else:
            mask = (soil >= 1) & (soil <= 14)

        if band_ranges_f:
            rmask = np.zeros_like(rain, dtype=bool)
            for low, high in band_ranges_f:
                rmask |= (rain >= low) & (rain < high)
            mask &= rmask

        if ph_ranges_f:
            pmask = np.zeros_like(ph_x10, dtype=bool)
            for low, high in ph_ranges_f:
                lo_x10 = int(round(low * 10))
                hi_x10 = int(round(high * 10))
                pmask |= (ph_x10 >= lo_x10) & (ph_x10 < hi_x10)
            mask &= pmask & (ph_x10 != -32768)

        if region_mask is not None:
            mask &= region_mask

        layer_rgba = np.zeros((out_h, out_w, 4), dtype=np.uint8)
        layer_rgba[mask] = color
        per_layer_rgba.append(layer_rgba)

    bounds = [[miny, minx], [maxy, maxx]]
    return per_layer_rgba, bounds
