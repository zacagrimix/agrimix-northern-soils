"""Step 5: Build the DuckDB database from processed outputs.

Schema (all tables in main schema):

  asc_soil_orders          — lookup (soil_code, soil_name)
  rainfall_bands_coarse    — lookup (band_idx, band_label, band_low_mm, band_high_mm)
  rainfall_bands_fine      — lookup (band_idx, band_label, band_low_mm, band_high_mm)
  regions                  — one row per NRM cattle region (Dubbo north)
  soil_rain_coarse         — fact table: hectares per (region, soil, coarse_band)
  soil_rain_fine           — fact table: hectares per (region, soil, fine_band)

Plus convenience views:
  v_region_summary         — region attrs joined with totals/dominant soil
  v_soil_totals_northern   — total ha per soil across all regions
  v_rain_totals_northern   — total ha per rainfall band across all regions

Output: db/agrimix.duckdb

Run: python -m scripts.05_build_database
"""
from __future__ import annotations
import sys
from pathlib import Path

import duckdb
import geopandas as gpd
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from scripts.config import (
    ASC_LEGEND, COARSE_RAIN_BANDS, FINE_RAIN_BANDS,
    DB_PATH, PROCESSED, NRM_DUBBO_NORTH,
)


def main() -> None:
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    if DB_PATH.exists():
        DB_PATH.unlink()
        print(f"Removed existing database at {DB_PATH}")

    con = duckdb.connect(str(DB_PATH))
    con.execute("INSTALL spatial; LOAD spatial;")
    print(f"Connected to DuckDB at {DB_PATH}")

    # ---- Lookup tables ----
    print("Creating lookup tables...")
    soil_lookup = pd.DataFrame(
        [{"soil_code": code, "soil_name": name} for code, name in ASC_LEGEND.items()]
    )
    con.execute("CREATE TABLE asc_soil_orders AS SELECT * FROM soil_lookup")
    con.execute("CREATE INDEX idx_soil_code ON asc_soil_orders(soil_code)")

    coarse_lookup = pd.DataFrame(
        [{"band_idx": i, "band_label": lbl, "band_low_mm": lo, "band_high_mm": hi}
         for i, (lbl, lo, hi) in enumerate(COARSE_RAIN_BANDS)]
    )
    con.execute("CREATE TABLE rainfall_bands_coarse AS SELECT * FROM coarse_lookup")
    con.execute("CREATE INDEX idx_coarse_band ON rainfall_bands_coarse(band_idx)")

    fine_lookup = pd.DataFrame(
        [{"band_idx": i, "band_label": lbl, "band_low_mm": lo, "band_high_mm": hi}
         for i, (lbl, lo, hi) in enumerate(FINE_RAIN_BANDS)]
    )
    con.execute("CREATE TABLE rainfall_bands_fine AS SELECT * FROM fine_lookup")
    con.execute("CREATE INDEX idx_fine_band ON rainfall_bands_fine(band_idx)")

    # ---- Regions ----
    print("Loading regions table...")
    regions_df = pd.read_parquet(PROCESSED / "region_attributes.parquet")

    # Load polygons too as WKT so DuckDB Spatial can use ST_GeomFromText
    print("Adding region polygons (as WKT)...")
    polys = gpd.read_file(NRM_DUBBO_NORTH).to_crs("EPSG:4326")
    polys["geom_wkt"] = polys.geometry.to_wkt()
    geom_df = polys[["NRM_REGION", "geom_wkt"]]
    regions_df = regions_df.merge(geom_df, on="NRM_REGION", how="left")
    con.execute("""
        CREATE TABLE regions (
            nrm_id INTEGER,
            nrm_region VARCHAR PRIMARY KEY,
            state VARCHAR,
            area_desc VARCHAR,
            cent_lat DOUBLE,
            cent_lon DOUBLE,
            geom_area_ha DOUBLE,
            mean_map_mm DOUBLE,
            cattle_head_jun2021 BIGINT,
            land_value_per_ha_aud INTEGER,
            bb_region VARCHAR,
            land_value_method VARCHAR,
            geom_wkt VARCHAR
        )
    """)
    con.register("regions_in", regions_df.rename(columns={
        "NRM_REGION": "nrm_region", "STATE": "state", "NRM_ID": "nrm_id",
        "AREA_DESC": "area_desc",
    }))
    con.execute("""
        INSERT INTO regions
        SELECT nrm_id, nrm_region, state, area_desc, cent_lat, cent_lon,
               geom_area_ha, mean_MAP_mm, cattle_head_jun2021,
               land_value_per_ha_aud, bb_region, land_value_method, geom_wkt
        FROM regions_in
    """)
    con.unregister("regions_in")
    con.execute("CREATE INDEX idx_region_state ON regions(state)")

    # ---- Soil x rain fact tables ----
    print("Loading coarse soil x rainfall fact table...")
    coarse_path = PROCESSED / "soil_rain_coarse.parquet"
    con.execute(f"""
        CREATE TABLE soil_rain_coarse AS
        SELECT
            "NRM_REGION"::VARCHAR    AS nrm_region,
            "STATE"::VARCHAR         AS state,
            "soil_code"::INTEGER     AS soil_code,
            "soil"::VARCHAR          AS soil_name,
            "band_idx"::INTEGER      AS band_idx,
            "band_label"::VARCHAR    AS band_label,
            "band_low"::INTEGER      AS band_low_mm,
            "band_high"::INTEGER     AS band_high_mm,
            "ha"::DOUBLE             AS hectares
        FROM read_parquet('{coarse_path.as_posix()}')
    """)
    con.execute("CREATE INDEX idx_src_region ON soil_rain_coarse(nrm_region)")
    con.execute("CREATE INDEX idx_src_soil ON soil_rain_coarse(soil_code)")
    con.execute("CREATE INDEX idx_src_band ON soil_rain_coarse(band_idx)")

    print("Loading fine soil x rainfall fact table...")
    fine_path = PROCESSED / "soil_rain_fine.parquet"
    con.execute(f"""
        CREATE TABLE soil_rain_fine AS
        SELECT
            "NRM_REGION"::VARCHAR    AS nrm_region,
            "STATE"::VARCHAR         AS state,
            "soil_code"::INTEGER     AS soil_code,
            "soil"::VARCHAR          AS soil_name,
            "band_idx"::INTEGER      AS band_idx,
            "band_label"::VARCHAR    AS band_label,
            "band_low"::INTEGER      AS band_low_mm,
            "band_high"::INTEGER     AS band_high_mm,
            "ha"::DOUBLE             AS hectares
        FROM read_parquet('{fine_path.as_posix()}')
    """)
    con.execute("CREATE INDEX idx_srf_region ON soil_rain_fine(nrm_region)")
    con.execute("CREATE INDEX idx_srf_soil ON soil_rain_fine(soil_code)")
    con.execute("CREATE INDEX idx_srf_band ON soil_rain_fine(band_idx)")

    # ---- Convenience views ----
    print("Creating views...")
    con.execute("""
        CREATE VIEW v_region_summary AS
        SELECT
            r.nrm_region, r.state, r.cent_lat, r.cent_lon,
            r.geom_area_ha, r.mean_map_mm,
            r.cattle_head_jun2021,
            r.land_value_per_ha_aud, r.bb_region,
            (SELECT SUM(hectares) FROM soil_rain_coarse src WHERE src.nrm_region = r.nrm_region)
                AS total_mapped_ha,
            (SELECT soil_name FROM soil_rain_coarse src WHERE src.nrm_region = r.nrm_region
             GROUP BY soil_name ORDER BY SUM(hectares) DESC LIMIT 1)
                AS dominant_soil
        FROM regions r
        ORDER BY r.cent_lat DESC
    """)

    con.execute("""
        CREATE VIEW v_soil_totals_northern AS
        SELECT soil_code, soil_name, SUM(hectares) AS hectares
        FROM soil_rain_coarse
        GROUP BY soil_code, soil_name
        ORDER BY hectares DESC
    """)

    con.execute("""
        CREATE VIEW v_rain_totals_northern AS
        SELECT band_idx, band_label, band_low_mm, band_high_mm,
               SUM(hectares) AS hectares
        FROM soil_rain_coarse
        GROUP BY band_idx, band_label, band_low_mm, band_high_mm
        ORDER BY band_idx
    """)

    con.execute("""
        CREATE VIEW v_total_estimated_value AS
        SELECT r.nrm_region, r.state,
               r.cattle_head_jun2021,
               (SELECT SUM(hectares) FROM soil_rain_coarse src WHERE src.nrm_region = r.nrm_region) AS total_ha,
               r.land_value_per_ha_aud,
               (SELECT SUM(hectares) FROM soil_rain_coarse src WHERE src.nrm_region = r.nrm_region) * r.land_value_per_ha_aud / 1e6 AS estimated_value_aud_million
        FROM regions r
        ORDER BY estimated_value_aud_million DESC
    """)

    # ---- Validation ----
    print("\nValidation queries:")
    nrows = con.execute("SELECT COUNT(*) FROM soil_rain_coarse").fetchone()[0]
    print(f"  soil_rain_coarse rows: {nrows:,} (expect 21 * 14 * 6 = 1,764)")
    nrows = con.execute("SELECT COUNT(*) FROM soil_rain_fine").fetchone()[0]
    nbands = len(FINE_RAIN_BANDS)
    print(f"  soil_rain_fine rows:   {nrows:,} (expect 21 * 14 * {nbands} = {21*14*nbands:,})")
    nregions = con.execute("SELECT COUNT(*) FROM regions").fetchone()[0]
    print(f"  regions rows:          {nregions} (expect 21)")

    print("\nSample query: top 5 (region, soil, band) by hectares")
    res = con.execute("""
        SELECT nrm_region, soil_name, band_label, ROUND(hectares) AS ha
        FROM soil_rain_coarse
        WHERE hectares > 0
        ORDER BY hectares DESC
        LIMIT 5
    """).df()
    print(res.to_string(index=False))

    print("\nSample query: how much Vertosol gets 750-1000 mm in the Fitzroy?")
    res = con.execute("""
        SELECT nrm_region, soil_name, band_label, ROUND(hectares) AS ha
        FROM soil_rain_coarse
        WHERE nrm_region = 'Fitzroy' AND soil_name = 'Vertosol' AND band_label = '750-1000 mm'
    """).df()
    print(res.to_string(index=False))

    con.close()
    print(f"\nDatabase built: {DB_PATH}")
    print(f"Size: {DB_PATH.stat().st_size / 1024:.1f} KB")


if __name__ == "__main__":
    main()
