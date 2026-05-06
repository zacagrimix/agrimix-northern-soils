"""Step 1: Build mean annual precipitation (MAP) grid for Australia from
SILO 1991-2020 monthly rainfall.

Downloads 30 monthly_rain.nc files from SILO's S3 bucket (one per year,
~14 MB each = ~420 MB total), sums each year's 12 months to an annual total,
and averages across the 30-year window to produce the long-term mean.

Source: SILO (Queensland Government / Bureau of Meteorology)
        https://www.longpaddock.qld.gov.au/silo/
License: CC-BY 4.0
Resolution: 0.05° (~5 km)
Period: 1991-2020 (30-year climatological normal)

Output: data/processed/MAP_aus_2.5min.tif
        (filename kept for compatibility with downstream scripts; the actual
        resolution is now 0.05° / SILO native, not WorldClim 2.5 arc-min)

Run: python scripts/01_build_map_grid.py
"""
from __future__ import annotations

import sys
import time
from pathlib import Path

import numpy as np
import rasterio
import requests
import xarray as xr
from rasterio.transform import from_origin

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from scripts.config import MAP_RASTER, PROCESSED, RAW

SILO_DIR = RAW / "silo"
S3_BASE = (
    "https://silo-open-data.s3.ap-southeast-2.amazonaws.com"
    "/Official/annual/monthly_rain"
)
YEARS = list(range(1991, 2021))  # 30-year normal


def download_year(year: int) -> Path:
    out = SILO_DIR / f"{year}.monthly_rain.nc"
    if out.exists() and out.stat().st_size > 1_000_000:
        return out
    url = f"{S3_BASE}/{year}.monthly_rain.nc"
    print(f"  downloading {year}...", flush=True)
    r = requests.get(url, stream=True, timeout=180)
    r.raise_for_status()
    tmp = out.with_suffix(".nc.tmp")
    with open(tmp, "wb") as f:
        for chunk in r.iter_content(chunk_size=1 << 20):
            f.write(chunk)
    tmp.rename(out)
    return out


def main() -> None:
    PROCESSED.mkdir(parents=True, exist_ok=True)
    SILO_DIR.mkdir(parents=True, exist_ok=True)

    print(
        f"Building 1991-2020 mean annual rainfall from SILO "
        f"({len(YEARS)} years)..."
    )
    t0 = time.time()

    annual_sum = None
    valid_count = None
    lat = lon = None

    for year in YEARS:
        nc = download_year(year)
        with xr.open_dataset(nc) as ds:
            annual = ds["monthly_rain"].sum(
                dim="time", skipna=False
            ).values
            valid = ~np.isnan(annual)
            if annual_sum is None:
                annual_sum = np.zeros_like(annual, dtype=np.float64)
                valid_count = np.zeros_like(annual, dtype=np.int32)
                lat = ds.lat.values.copy()
                lon = ds.lon.values.copy()
            annual_sum += np.where(valid, annual, 0)
            valid_count += valid.astype(np.int32)
        print(f"  {year} OK", flush=True)

    mean_map = np.where(
        valid_count > 0,
        annual_sum / np.maximum(valid_count, 1),
        -32768.0,
    )
    out = np.where(
        mean_map == -32768.0, -32768, np.round(mean_map)
    ).astype(np.int16)

    # GeoTIFF expects North-up (row 0 = north). SILO lat is ascending -> flip.
    if lat[0] < lat[-1]:
        out = np.flipud(out)
        lat_top = lat[-1] + 0.025
    else:
        lat_top = lat[0] + 0.025
    lon_left = lon[0] - 0.025
    transform = from_origin(lon_left, lat_top, 0.05, 0.05)

    valid_vals = out[out != -32768]
    print(
        f"\nMAP stats (1991-2020): "
        f"min={valid_vals.min()} mm, "
        f"max={valid_vals.max()} mm, "
        f"mean={valid_vals.mean():.0f} mm, "
        f"pixels={valid_vals.size:,}"
    )

    profile = {
        "driver": "GTiff",
        "height": out.shape[0],
        "width": out.shape[1],
        "count": 1,
        "dtype": "int16",
        "crs": "EPSG:4326",
        "transform": transform,
        "nodata": -32768,
        "compress": "deflate",
        "tiled": True,
    }
    with rasterio.open(MAP_RASTER, "w", **profile) as dst:
        dst.write(out, 1)
    print(
        f"Saved -> {MAP_RASTER} "
        f"({MAP_RASTER.stat().st_size / 1e6:.1f} MB) "
        f"in {time.time() - t0:.1f}s"
    )


if __name__ == "__main__":
    main()
