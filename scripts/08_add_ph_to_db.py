"""Step 8 (incremental): add pH tables to an existing DuckDB without full rebuild.

Adds:
  ph_bands_coarse        — lookup (band_idx, band_label, band_low, band_high)
  soil_rain_ph_coarse    — 4-D fact table (region × soil × rain × pH)
  v_ph_totals_northern   — view: ha per pH band across regions
  v_region_ph_summary    — view: pH ha summary per region

For a fresh full pipeline rebuild, 05_build_database.py also includes these.
"""
from __future__ import annotations

import sys
from pathlib import Path

import duckdb
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from scripts.config import DB_PATH, PROCESSED

PH_PARQUET = PROCESSED / "soil_rain_ph_coarse.parquet"


def main() -> None:
    if not PH_PARQUET.exists():
        raise SystemExit(f"Missing {PH_PARQUET}; run 07_ph_extraction.py first.")

    con = duckdb.connect(str(DB_PATH))
    print(f"Connected to {DB_PATH}")

    # Drop pH-related objects if re-running
    for obj in [
        "v_region_ph_summary", "v_ph_totals_northern",
        "soil_rain_ph_coarse", "ph_bands_coarse",
    ]:
        con.execute(f"DROP VIEW IF EXISTS {obj}")
        con.execute(f"DROP TABLE IF EXISTS {obj}")

    # ---- Lookup ----
    df = con.execute(f"""
        SELECT DISTINCT ph_band_idx, ph_band_label, ph_band_low, ph_band_high
        FROM read_parquet('{PH_PARQUET.as_posix()}')
        ORDER BY ph_band_idx
    """).df()
    con.execute("""
        CREATE TABLE ph_bands_coarse (
            band_idx INTEGER,
            band_label VARCHAR,
            band_low DOUBLE,
            band_high DOUBLE
        )
    """)
    con.register("ph_lookup_in", df)
    con.execute("""
        INSERT INTO ph_bands_coarse
        SELECT ph_band_idx, ph_band_label, ph_band_low, ph_band_high
        FROM ph_lookup_in
    """)
    con.unregister("ph_lookup_in")
    con.execute("CREATE INDEX idx_ph_band ON ph_bands_coarse(band_idx)")
    print(f"Created ph_bands_coarse ({len(df)} rows)")

    # ---- Fact table ----
    con.execute(f"""
        CREATE TABLE soil_rain_ph_coarse AS
        SELECT
            "NRM_REGION"::VARCHAR        AS nrm_region,
            "STATE"::VARCHAR             AS state,
            "soil_code"::INTEGER         AS soil_code,
            "soil"::VARCHAR              AS soil_name,
            "rain_band_idx"::INTEGER     AS rain_band_idx,
            "rain_band_label"::VARCHAR   AS rain_band_label,
            "rain_band_low"::INTEGER     AS rain_band_low_mm,
            "rain_band_high"::INTEGER    AS rain_band_high_mm,
            "ph_band_idx"::INTEGER       AS ph_band_idx,
            "ph_band_label"::VARCHAR     AS ph_band_label,
            "ph_band_low"::DOUBLE        AS ph_band_low,
            "ph_band_high"::DOUBLE       AS ph_band_high,
            "ha"::DOUBLE                 AS hectares
        FROM read_parquet('{PH_PARQUET.as_posix()}')
    """)
    con.execute("CREATE INDEX idx_srph_region ON soil_rain_ph_coarse(nrm_region)")
    con.execute("CREATE INDEX idx_srph_soil ON soil_rain_ph_coarse(soil_code)")
    con.execute("CREATE INDEX idx_srph_rain ON soil_rain_ph_coarse(rain_band_idx)")
    con.execute("CREATE INDEX idx_srph_ph ON soil_rain_ph_coarse(ph_band_idx)")
    nrows = con.execute("SELECT COUNT(*) FROM soil_rain_ph_coarse").fetchone()[0]
    print(f"Created soil_rain_ph_coarse ({nrows:,} rows)")

    # ---- Views ----
    con.execute("""
        CREATE VIEW v_ph_totals_northern AS
        SELECT band_idx, band_label, band_low, band_high,
               ROUND(SUM(hectares)) AS hectares
        FROM soil_rain_ph_coarse src
        JOIN ph_bands_coarse pb ON src.ph_band_idx = pb.band_idx
        GROUP BY band_idx, band_label, band_low, band_high
        ORDER BY band_idx
    """)
    con.execute("""
        CREATE VIEW v_region_ph_summary AS
        SELECT
            nrm_region, state,
            ROUND(SUM(hectares)) AS total_mapped_ha,
            ROUND(SUM(hectares * (ph_band_low + ph_band_high) / 2.0)
                  / NULLIF(SUM(hectares), 0), 2) AS mean_ph_water,
            (SELECT ph_band_label FROM soil_rain_ph_coarse src2
             WHERE src2.nrm_region = src.nrm_region
             GROUP BY ph_band_label, ph_band_idx
             ORDER BY SUM(hectares) DESC, ph_band_idx LIMIT 1) AS dominant_ph_band
        FROM soil_rain_ph_coarse src
        GROUP BY nrm_region, state
        ORDER BY mean_ph_water
    """)
    print("Created views: v_ph_totals_northern, v_region_ph_summary")

    # ---- Cross-check: pH cube totals vs existing soil_rain_coarse ----
    print("\nValidation:")
    check = con.execute("""
        WITH ph AS (
            SELECT nrm_region, soil_name, rain_band_label,
                   SUM(hectares) AS ha_ph
            FROM soil_rain_ph_coarse
            GROUP BY nrm_region, soil_name, rain_band_label
        ),
        existing AS (
            SELECT nrm_region, soil_name, band_label AS rain_band_label,
                   SUM(hectares) AS ha_existing
            FROM soil_rain_coarse
            GROUP BY nrm_region, soil_name, band_label
        )
        SELECT
            COUNT(*) AS combos,
            ROUND(AVG(ABS(ha_ph - ha_existing)), 1) AS avg_abs_diff,
            ROUND(MAX(ABS(ha_ph - ha_existing)), 1) AS max_abs_diff,
            ROUND(SUM(ha_ph)/1e6, 1) AS pH_cube_total_Mha,
            ROUND(SUM(ha_existing)/1e6, 1) AS existing_total_Mha
        FROM ph p JOIN existing e USING (nrm_region, soil_name, rain_band_label)
    """).fetchone()
    print(
        f"  combos={check[0]:,}, avg_diff={check[1]} ha, "
        f"max_diff={check[2]} ha"
    )
    print(
        f"  pH-cube total: {check[3]} Mha ; existing soil_rain: "
        f"{check[4]} Mha"
    )
    print(
        "  NOTE: pH cube is smaller because pixels with no pH data "
        "(ocean fringe / nodata) drop out."
    )

    print("\nNorthern total by pH band (Mha):")
    df = con.execute("""
        SELECT band_label, ROUND(hectares/1e6, 1) AS Mha
        FROM v_ph_totals_northern ORDER BY band_idx
    """).fetchall()
    for label, mha in df:
        print(f"  {label:>10s}  {mha:>5.1f}")

    con.close()
    print(f"\nDB updated: {DB_PATH}")


if __name__ == "__main__":
    main()
