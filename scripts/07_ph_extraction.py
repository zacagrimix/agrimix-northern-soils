"""Step 7: 4-D cube — region × soil × rainfall band × pH band.

Streams the SLGA 90 m ASC raster row-by-row through each NRM polygon,
sampling WorldClim mean annual precipitation AND SoilGrids 0-30 cm pH at
each soil pixel. Buckets pixel area by (soil, rain_band, pH_band) per region.

Outputs:
  data/processed/soil_rain_ph_coarse.parquet
"""
from __future__ import annotations

import math
import sys
import time
from pathlib import Path

import duckdb
import numpy as np
import pandas as pd
import rasterio
from rasterio.features import geometry_mask
from rasterio.windows import Window, from_bounds
from shapely.wkt import loads as wkt_loads

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from scripts.config import (
    ASC_LEGEND, COARSE_RAIN_BANDS, DB_PATH, MAP_RASTER, PROCESSED, SLGA_RASTER,
)
from scripts.geom_utils import per_row_pixel_areas

PH_RASTER = PROCESSED / "pH_water_0-30cm_north_500m.tif"

# pH bands. SoilGrids stores pH × 10 as int16, so thresholds are also × 10.
# Bands: <5.0, 5.0-5.5, 5.5-6.0, ..., 8.0-8.5, >8.5
PH_BANDS = [
    ("<5.0", 0.0, 5.0),
    ("5.0-5.5", 5.0, 5.5),
    ("5.5-6.0", 5.5, 6.0),
    ("6.0-6.5", 6.0, 6.5),
    ("6.5-7.0", 6.5, 7.0),
    ("7.0-7.5", 7.0, 7.5),
    ("7.5-8.0", 7.5, 8.0),
    ("8.0-8.5", 8.0, 8.5),
    (">8.5", 8.5, 14.0),
]


def main() -> None:
    n_soils = len(ASC_LEGEND)
    n_rain = len(COARSE_RAIN_BANDS)
    n_ph = len(PH_BANDS)
    bucket_size = n_soils * n_rain * n_ph
    print(
        f"Cube shape: 21 regions × {n_soils} soils × {n_rain} rain × "
        f"{n_ph} pH = {21 * bucket_size:,} rows max"
    )

    con = duckdb.connect(str(DB_PATH), read_only=True)
    region_rows = con.execute(
        "SELECT nrm_region, state, geom_wkt FROM regions ORDER BY nrm_region"
    ).fetchall()
    con.close()

    # Pre-compute pH band thresholds in SoilGrids units (pH × 10).
    ph_thresholds_x10 = [(int(round(lo * 10)), int(round(hi * 10)))
                         for _, lo, hi in PH_BANDS]

    records: list[dict] = []
    t0 = time.time()

    with rasterio.open(SLGA_RASTER) as soil_src, \
         rasterio.open(MAP_RASTER) as rain_src, \
         rasterio.open(PH_RASTER) as ph_src:
        s_transform = soil_src.transform
        s_height, s_width = soil_src.height, soil_src.width

        print(f"Pre-computing per-row pixel areas ({s_height} rows)...")
        row_area_m2 = per_row_pixel_areas(s_transform, s_height)

        rain_arr = rain_src.read(1)
        r_transform = rain_src.transform
        r_height, r_width = rain_arr.shape

        ph_arr = ph_src.read(1)
        p_transform = ph_src.transform
        p_height, p_width = ph_arr.shape

        def map_row_for_slga_row(slga_row: int) -> int:
            slga_lat = s_transform.f + (slga_row + 0.5) * s_transform.e
            return int((slga_lat - r_transform.f) / r_transform.e)

        def ph_row_for_slga_row(slga_row: int) -> int:
            slga_lat = s_transform.f + (slga_row + 0.5) * s_transform.e
            return int((slga_lat - p_transform.f) / p_transform.e)

        for name, state, wkt in region_rows:
            geom = wkt_loads(wkt)
            if geom.is_empty:
                continue
            minx, miny, maxx, maxy = geom.bounds
            w = from_bounds(minx, miny, maxx, maxy, transform=s_transform)
            col_off = max(int(math.floor(w.col_off)), 0)
            row_off = max(int(math.floor(w.row_off)), 0)
            col_end = min(int(math.ceil(w.col_off + w.width)), s_width)
            row_end = min(int(math.ceil(w.row_off + w.height)), s_height)
            win_w, win_h = col_end - col_off, row_end - row_off
            if win_w <= 0 or win_h <= 0:
                continue
            win_transform = rasterio.windows.transform(
                Window(col_off, row_off, win_w, win_h), s_transform
            )
            geom_mask_window = geometry_mask(
                [geom.__geo_interface__],
                out_shape=(win_h, win_w),
                transform=win_transform,
                all_touched=False,
                invert=True,
            )

            slga_cols = np.arange(col_off, col_end)
            slga_lons = s_transform.c + (slga_cols + 0.5) * s_transform.a
            map_cols = np.clip(
                ((slga_lons - r_transform.c) / r_transform.a).astype(np.int32),
                0, r_width - 1,
            )
            ph_cols = np.clip(
                ((slga_lons - p_transform.c) / p_transform.a).astype(np.int32),
                0, p_width - 1,
            )

            bucket = np.zeros(bucket_size, dtype=np.float64)
            for r_local in range(win_h):
                slga_row = row_off + r_local
                row_mask = geom_mask_window[r_local]
                if not row_mask.any():
                    continue
                soil_row = soil_src.read(
                    1, window=Window(col_off, slga_row, win_w, 1)
                ).flatten()
                map_row_idx = max(0, min(map_row_for_slga_row(slga_row),
                                         r_height - 1))
                map_vals = rain_arr[map_row_idx, map_cols]
                ph_row_idx = max(0, min(ph_row_for_slga_row(slga_row),
                                        p_height - 1))
                ph_vals = ph_arr[ph_row_idx, ph_cols]

                valid = (
                    row_mask
                    & (soil_row >= 1) & (soil_row <= n_soils)
                    & (map_vals >= 0) & (map_vals != -32768)
                    & (ph_vals > 0) & (ph_vals != -32768)
                )
                if not valid.any():
                    continue
                sv = soil_row[valid]
                mv = map_vals[valid]
                pv = ph_vals[valid]

                rb = np.full(mv.shape, -1, dtype=np.int8)
                for bi, (_, lo, hi) in enumerate(COARSE_RAIN_BANDS):
                    rb[(mv >= lo) & (mv < hi)] = bi
                pb = np.full(pv.shape, -1, dtype=np.int8)
                for bi, (lo_x10, hi_x10) in enumerate(ph_thresholds_x10):
                    pb[(pv >= lo_x10) & (pv < hi_x10)] = bi

                keep = (rb >= 0) & (pb >= 0)
                if not keep.any():
                    continue
                sv = sv[keep]
                rb = rb[keep]
                pb = pb[keep]
                idx = (
                    (sv.astype(np.int32) - 1) * (n_rain * n_ph)
                    + rb.astype(np.int32) * n_ph
                    + pb.astype(np.int32)
                )
                counts = np.bincount(idx, minlength=bucket_size)
                bucket += counts * row_area_m2[slga_row]

            ha = bucket / 1e4
            for soil_code in range(1, n_soils + 1):
                for rb_i, (rl, rlo, rhi) in enumerate(COARSE_RAIN_BANDS):
                    for pb_i, (pl, plo, phi_) in enumerate(PH_BANDS):
                        i = ((soil_code - 1) * n_rain * n_ph
                             + rb_i * n_ph + pb_i)
                        records.append({
                            "NRM_REGION": name,
                            "STATE": state,
                            "soil_code": soil_code,
                            "soil": ASC_LEGEND[soil_code],
                            "rain_band_idx": rb_i,
                            "rain_band_label": rl,
                            "rain_band_low": rlo,
                            "rain_band_high": rhi,
                            "ph_band_idx": pb_i,
                            "ph_band_label": pl,
                            "ph_band_low": plo,
                            "ph_band_high": phi_,
                            "ha": round(float(ha[i]), 1),
                        })
            print(
                f"  {name:40s} ({state}) total {ha.sum() / 1e6:6.2f} Mha",
                flush=True,
            )

    print(f"\nElapsed: {time.time() - t0:.1f}s | rows: {len(records):,}")
    df = pd.DataFrame(records)
    out = PROCESSED / "soil_rain_ph_coarse.parquet"
    df.to_parquet(out, index=False)
    print(f"Saved -> {out} ({out.stat().st_size / 1e6:.1f} MB)")


if __name__ == "__main__":
    main()
