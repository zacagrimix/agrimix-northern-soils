"""Step 4: Compile region-level attributes.

Builds a single dataframe with one row per Dubbo-north NRM region containing:
  - geographic attributes (centroid, geometric area)
  - mean annual precipitation (area-weighted)
  - cattle head count (MLA Cattle Distribution Map, June 2021)
  - indicative land value $/ha (Bendigo Bank Australian Farmland Values 2025)

Output: data/processed/region_attributes.parquet

Run: python -m scripts.04_region_attributes
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
from rasterio.mask import mask as rio_mask

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from scripts.config import MAP_RASTER, NRM_DUBBO_NORTH, PROCESSED
from scripts.geom_utils import per_row_pixel_areas


# Cattle head count by NRM region (MLA Cattle Distribution Map, June 2021 ABS data)
# Source: https://www.mla.com.au/.../mla_cattle-distribution-map-june-2021_v02-final.pdf
MLA_CATTLE_HEAD = {
    "Cape York":                            65741,
    "Co-operative Management Area":         106625,
    "Northern Gulf":                        781785,
    "Wet Tropics":                          138952,           # MLA label: 'Terrain NRM'
    "Burdekin":                             1249795,          # MLA label: 'NQ Dry Tropics'
    "Mackay Whitsunday":                    117627,           # MLA label: 'Reef Catchments'
    "Southern Gulf":                        1126772,
    "Fitzroy":                              2523546,          # MLA label: 'Fitzroy Basin'
    "Burnett Mary":                         768138,
    "Desert Channels":                      1357692,
    "South West Queensland":                618653,
    "Maranoa Balonne and Border Rivers":    1012253,          # MLA label: 'Qld MDB'
    "South East Queensland":                300226,
    "Condamine":                            560814,
    "North Coast":                          380237,
    "Northern Tablelands":                  666946,
    "North West NSW":                       683009,
    "Western":                              175582,
    "Central West":                         495936,
    "Northern Territory":                   2230000,          # NT (single NRM)
    "Rangelands Region":                    1226000,          # WA Rangelands (whole)
}


# Indicative land value $/ha (Bendigo Bank Agribusiness, Australian Farmland Values
# 2025 — full-year 2024 data). Each NRM is mapped to the dominantly overlapping
# Bendigo Bank reporting region.
LAND_VALUE = {
    # NSW
    "North Coast":                     {"price_per_ha": 12956, "bb_region": "NSW - North Coast",                "method": "BB 2024 region median"},
    "Northern Tablelands":             {"price_per_ha": 7499,  "bb_region": "NSW - New England & North West",   "method": "BB 2024 region median"},
    "North West NSW":                  {"price_per_ha": 7499,  "bb_region": "NSW - New England & North West",   "method": "BB 2024 region median"},
    "Western":                         {"price_per_ha": 498,   "bb_region": "NSW - Far West",                   "method": "BB 2024 region median (low n)"},
    "Central West":                    {"price_per_ha": 6958,  "bb_region": "NSW - Central West",               "method": "BB 2024 region median"},
    # QLD
    "Cape York":                       {"price_per_ha": 14599, "bb_region": "QLD - Far North",                  "method": "BB 2024 region median"},
    "Co-operative Management Area":    {"price_per_ha": 14599, "bb_region": "QLD - Far North",                  "method": "BB 2024 region median"},
    "Wet Tropics":                     {"price_per_ha": 14599, "bb_region": "QLD - Far North",                  "method": "BB 2024 region median"},
    "Northern Gulf":                   {"price_per_ha": 637,   "bb_region": "QLD - Central (Gulf country)",     "method": "BB 2024 region median"},
    "Southern Gulf":                   {"price_per_ha": 637,   "bb_region": "QLD - Central (Gulf country)",     "method": "BB 2024 region median"},
    "Burdekin":                        {"price_per_ha": 13878, "bb_region": "QLD - North",                      "method": "BB 2024 region median"},
    "Mackay Whitsunday":               {"price_per_ha": 13878, "bb_region": "QLD - North",                      "method": "BB 2024 region median"},
    "Fitzroy":                         {"price_per_ha": 8789,  "bb_region": "QLD - Southern Coastal",           "method": "BB 2024 region median"},
    "Desert Channels":                 {"price_per_ha": 258,   "bb_region": "QLD - West",                       "method": "BB 2024 region median (low n - 13 sales)"},
    "Burnett Mary":                    {"price_per_ha": 8789,  "bb_region": "QLD - Southern Coastal",           "method": "BB 2024 region median"},
    "South West Queensland":           {"price_per_ha": 4448,  "bb_region": "QLD - Western Downs",              "method": "BB 2024 region median"},
    "South East Queensland":           {"price_per_ha": 15376, "bb_region": "QLD - South East",                 "method": "BB 2024 region median"},
    "Condamine":                       {"price_per_ha": 6473,  "bb_region": "QLD - Central Highlands",          "method": "BB 2024 region median"},
    "Maranoa Balonne and Border Rivers":{"price_per_ha": 4448, "bb_region": "QLD - Western Downs",              "method": "BB 2024 region median"},
    # NT
    "Northern Territory":              {"price_per_ha": 580,   "bb_region": "NT - Top End (most txns)",         "method": "BB 2024 Top End median (cattle regions much lower ~$60/ha)"},
    # WA
    "Rangelands Region":               {"price_per_ha": 70,    "bb_region": "WA Rangelands (no BB region)",     "method": "Estimate - BB does not publish a Rangelands median"},
}


def compute_mean_map_per_region(regions: gpd.GeoDataFrame) -> pd.DataFrame:
    """Area-weighted mean annual precipitation per region (mm)."""
    rows = []
    with rasterio.open(MAP_RASTER) as src:
        per_row = per_row_pixel_areas(src.transform, src.height)
        for _, region in regions.iterrows():
            geom = region.geometry
            arr, out_t = rio_mask(
                src, [geom.__geo_interface__],
                crop=True, all_touched=False, indexes=1, filled=True, nodata=-32768,
            )
            n_rows = arr.shape[0]
            first_row = int(round((out_t.f - src.transform.f) / src.transform.e))
            first_row = max(first_row, 0)
            row_areas = per_row[first_row : first_row + n_rows][:n_rows]
            valid = (arr != -32768) & (arr >= 0)
            pixel_areas = np.broadcast_to(row_areas[:, None], arr.shape)
            if valid.any():
                weighted = (arr[valid].astype(np.float64) * pixel_areas[valid]).sum()
                area_total = pixel_areas[valid].sum()
                mean_map = weighted / area_total
            else:
                mean_map = float("nan")
            rows.append({"NRM_REGION": region["NRM_REGION"], "mean_MAP_mm": round(mean_map, 0)})
    return pd.DataFrame(rows)


def main() -> None:
    PROCESSED.mkdir(parents=True, exist_ok=True)

    print("Loading region polygons...")
    regions = gpd.read_file(NRM_DUBBO_NORTH)

    regions_albers = regions.to_crs("EPSG:3577")
    regions["cent_lat"] = regions_albers.geometry.centroid.to_crs("EPSG:4283").y
    regions["cent_lon"] = regions_albers.geometry.centroid.to_crs("EPSG:4283").x
    regions["geom_area_ha"] = regions_albers.geometry.area / 1e4

    print("Computing area-weighted mean annual precipitation per region...")
    map_df = compute_mean_map_per_region(regions.to_crs("EPSG:4326"))

    df = regions[["NRM_REGION", "STATE", "NRM_ID", "AREA_DESC", "cent_lat",
                   "cent_lon", "geom_area_ha"]].merge(map_df, on="NRM_REGION")

    df["cattle_head_jun2021"] = df["NRM_REGION"].map(MLA_CATTLE_HEAD).astype("Int64")
    df["land_value_per_ha_aud"] = df["NRM_REGION"].map(lambda n: LAND_VALUE[n]["price_per_ha"])
    df["bb_region"] = df["NRM_REGION"].map(lambda n: LAND_VALUE[n]["bb_region"])
    df["land_value_method"] = df["NRM_REGION"].map(lambda n: LAND_VALUE[n]["method"])

    df = df.sort_values("cent_lat", ascending=False).reset_index(drop=True)

    out_path = PROCESSED / "region_attributes.parquet"
    df.to_parquet(out_path, index=False)
    print(f"Saved -> {out_path}")
    print()
    print(df[["NRM_REGION", "STATE", "cent_lat", "geom_area_ha",
               "mean_MAP_mm", "cattle_head_jun2021", "land_value_per_ha_aud"]].to_string(index=False))


if __name__ == "__main__":
    main()
