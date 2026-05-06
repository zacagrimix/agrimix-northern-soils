"""Step 3: Memory-efficient soil × rainfall cross-tab per NRM region.

Streams the SLGA 90 m ASC raster row-by-row through each NRM polygon, sampling
WorldClim mean annual precipitation at each soil pixel via nearest-neighbour
resampling. Each pixel contributes its true ellipsoidal area (lat-weighted) to
the (soil_code, band_idx) bucket it falls into.

Outputs:
  data/processed/soil_rain_coarse.parquet   (6 rainfall bands)
  data/processed/soil_rain_fine.parquet     (50 mm bands, <50 to >2000 mm)

Run: python -m scripts.03_soil_rain_extraction
"""
from __future__ import annotations
import math
import sys
import time
from pathlib import Path

import geopandas as gpd
import numpy as np
import pandas as pd
import rasterio
from rasterio.features import geometry_mask
from rasterio.warp import reproject, Resampling
from rasterio.windows import Window, from_bounds

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from scripts.config import (
    ASC_LEGEND, COARSE_RAIN_BANDS, FINE_RAIN_BANDS,
    SLGA_RASTER, MAP_RASTER, NRM_DUBBO_NORTH, PROCESSED,
)
from scripts.geom_utils import per_row_pixel_areas


def extract_soil_rain_cube(bands: list[tuple[str, int, int]], label: str) -> pd.DataFrame:
    """Extract a soil × rainfall cube using the supplied band definition.
    Returns a long-form dataframe with columns:
      NRM_REGION, STATE, soil_code, soil, band_idx, band_label, band_low, band_high, ha
    """
    n_soils = 14
    n_bands = len(bands)
    bucket_size = n_soils * n_bands

    print(f"\n=== Extracting cube: {label} ({n_bands} bands) ===")
    regions = gpd.read_file(NRM_DUBBO_NORTH).to_crs("EPSG:4326")

    records: list[dict] = []
    t0 = time.time()

    with rasterio.open(SLGA_RASTER) as soil_src, rasterio.open(MAP_RASTER) as rain_src:
        s_transform = soil_src.transform
        s_height = soil_src.height
        s_width = soil_src.width

        print(f"Pre-computing per-row pixel areas ({s_height} rows)...")
        row_area_m2 = per_row_pixel_areas(s_transform, s_height)

        rain_arr = rain_src.read(1)
        r_transform = rain_src.transform
        r_height, r_width = rain_arr.shape

        def map_row_for_slga_row(slga_row: int) -> int:
            slga_lat = s_transform.f + (slga_row + 0.5) * s_transform.e
            return int((slga_lat - r_transform.f) / r_transform.e)

        for _, region in regions.iterrows():
            name = region["NRM_REGION"]
            state = region["STATE"]
            geom = region.geometry
            if geom is None or geom.is_empty:
                continue

            minx, miny, maxx, maxy = geom.bounds
            w = from_bounds(minx, miny, maxx, maxy, transform=s_transform)
            col_off = max(int(math.floor(w.col_off)), 0)
            row_off = max(int(math.floor(w.row_off)), 0)
            col_end = min(int(math.ceil(w.col_off + w.width)), s_width)
            row_end = min(int(math.ceil(w.row_off + w.height)), s_height)
            win_w = col_end - col_off
            win_h = row_end - row_off
            win_transform = rasterio.windows.transform(
                Window(col_off, row_off, win_w, win_h), s_transform
            )

            try:
                geom_mask_window = geometry_mask(
                    [geom.__geo_interface__],
                    out_shape=(win_h, win_w),
                    transform=win_transform,
                    all_touched=False,
                    invert=True,
                )
            except Exception as e:
                print(f"  {name}: geometry_mask error: {e}")
                continue

            slga_cols = np.arange(col_off, col_end)
            slga_lons = s_transform.c + (slga_cols + 0.5) * s_transform.a
            map_cols = ((slga_lons - r_transform.c) / r_transform.a).astype(np.int32)
            map_cols = np.clip(map_cols, 0, r_width - 1)

            bucket_areas = np.zeros(bucket_size, dtype=np.float64)
            for r_local in range(win_h):
                slga_row = row_off + r_local
                row_mask = geom_mask_window[r_local]
                if not row_mask.any():
                    continue
                soil_row = soil_src.read(
                    1, window=Window(col_off, slga_row, win_w, 1)
                ).flatten()
                map_row = max(0, min(map_row_for_slga_row(slga_row), r_height - 1))
                map_vals = rain_arr[map_row, map_cols]

                valid = (
                    row_mask
                    & (soil_row >= 1)
                    & (soil_row <= 14)
                    & (map_vals != -32768)
                    & (map_vals >= 0)
                )
                if not valid.any():
                    continue
                mv = map_vals[valid]
                sv = soil_row[valid]
                band_idx = np.full(mv.shape, -1, dtype=np.int8)
                for bi, (_, lo, hi) in enumerate(bands):
                    band_idx[(mv >= lo) & (mv < hi)] = bi
                keep = band_idx >= 0
                if not keep.any():
                    continue
                sv = sv[keep]
                band_idx = band_idx[keep]
                combined = (sv.astype(np.int32) - 1) * n_bands + band_idx.astype(np.int32)
                counts = np.bincount(combined, minlength=bucket_size)
                bucket_areas += counts * row_area_m2[slga_row]

            ha = bucket_areas / 1e4
            for soil_code in range(1, 15):
                for bi, (band_label, lo, hi) in enumerate(bands):
                    idx = (soil_code - 1) * n_bands + bi
                    records.append({
                        "NRM_REGION": name,
                        "STATE": state,
                        "soil_code": soil_code,
                        "soil": ASC_LEGEND[soil_code],
                        "band_idx": bi,
                        "band_label": band_label,
                        "band_low": lo,
                        "band_high": hi,
                        "ha": round(float(ha[idx]), 1),
                    })
            total_ha = ha.sum()
            print(f"  {name:40s} ({state}) total {total_ha/1e6:6.2f} Mha", flush=True)

    print(f"Elapsed: {time.time() - t0:.1f}s | rows: {len(records):,}")
    return pd.DataFrame(records)


def main() -> None:
    PROCESSED.mkdir(parents=True, exist_ok=True)

    coarse_df = extract_soil_rain_cube(COARSE_RAIN_BANDS, label="coarse (6 bands)")
    coarse_path = PROCESSED / "soil_rain_coarse.parquet"
    coarse_df.to_parquet(coarse_path, index=False)
    print(f"Saved -> {coarse_path}")

    fine_df = extract_soil_rain_cube(FINE_RAIN_BANDS, label=f"fine ({len(FINE_RAIN_BANDS)} bands)")
    fine_path = PROCESSED / "soil_rain_fine.parquet"
    fine_df.to_parquet(fine_path, index=False)
    print(f"Saved -> {fine_path}")


if __name__ == "__main__":
    main()
