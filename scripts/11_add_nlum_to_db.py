"""Step 11 (incremental): add NLUM Primary land-use tables to existing DuckDB.

Adds:
  nlum_classes              — lookup (code, label, is_managed)
  soil_rain_ph_nlum_coarse  — 5-D fact table (region × soil × rain × pH × NLUM)
  v_nlum_totals_northern    — view: ha per NLUM class across all regions

For a fresh rebuild, 05_build_database.py also includes these.
"""
from __future__ import annotations

import sys
from pathlib import Path

import duckdb
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from scripts.config import DB_PATH, NLUM_PRIMARY, PROCESSED

NLUM_PARQUET = PROCESSED / "soil_rain_ph_nlum_coarse.parquet"


def main() -> None:
    if not NLUM_PARQUET.exists():
        raise SystemExit(f"Missing {NLUM_PARQUET}; run 10_nlum_extraction.py first.")

    con = duckdb.connect(str(DB_PATH))
    print(f"Connected to {DB_PATH}")

    for obj in [
        "v_nlum_totals_northern",
        "soil_rain_ph_nlum_coarse",
        "nlum_classes",
    ]:
        con.execute(f"DROP VIEW IF EXISTS {obj}")
        con.execute(f"DROP TABLE IF EXISTS {obj}")

    # ---- Lookup ----
    df = pd.DataFrame(
        [
            {"code": c, "label": lbl, "is_managed": m}
            for c, lbl, m in NLUM_PRIMARY
        ]
    )
    con.execute("""
        CREATE TABLE nlum_classes (
            code INTEGER PRIMARY KEY,
            label VARCHAR,
            is_managed BOOLEAN
        )
    """)
    con.register("nlum_in", df)
    con.execute(
        "INSERT INTO nlum_classes SELECT code, label, is_managed FROM nlum_in"
    )
    con.unregister("nlum_in")
    print(f"Created nlum_classes ({len(df)} rows)")

    # ---- Fact table ----
    con.execute(f"""
        CREATE TABLE soil_rain_ph_nlum_coarse AS
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
            "nlum_code"::INTEGER         AS nlum_code,
            "nlum_label"::VARCHAR        AS nlum_label,
            "nlum_managed"::BOOLEAN      AS nlum_managed,
            "ha"::DOUBLE                 AS hectares
        FROM read_parquet('{NLUM_PARQUET.as_posix()}')
    """)
    con.execute(
        "CREATE INDEX idx_srpn_region ON soil_rain_ph_nlum_coarse(nrm_region)"
    )
    con.execute(
        "CREATE INDEX idx_srpn_soil ON soil_rain_ph_nlum_coarse(soil_code)"
    )
    con.execute(
        "CREATE INDEX idx_srpn_rain ON soil_rain_ph_nlum_coarse(rain_band_idx)"
    )
    con.execute(
        "CREATE INDEX idx_srpn_ph ON soil_rain_ph_nlum_coarse(ph_band_idx)"
    )
    con.execute(
        "CREATE INDEX idx_srpn_nlum ON soil_rain_ph_nlum_coarse(nlum_code)"
    )
    nrows = con.execute(
        "SELECT COUNT(*) FROM soil_rain_ph_nlum_coarse"
    ).fetchone()[0]
    print(f"Created soil_rain_ph_nlum_coarse ({nrows:,} rows)")

    # ---- View ----
    con.execute("""
        CREATE VIEW v_nlum_totals_northern AS
        SELECT
            n.code, n.label, n.is_managed,
            ROUND(SUM(src.hectares)) AS hectares
        FROM soil_rain_ph_nlum_coarse src
        JOIN nlum_classes n ON n.code = src.nlum_code
        GROUP BY n.code, n.label, n.is_managed
        ORDER BY n.code
    """)
    print("Created view: v_nlum_totals_northern")

    print("\nValidation: pH-cube total vs NLUM-cube total")
    res = con.execute("""
        SELECT
            ROUND(SUM(hectares)/1e6, 1) AS Mha
        FROM soil_rain_ph_nlum_coarse
    """).fetchone()[0]
    res_ph = con.execute("""
        SELECT
            ROUND(SUM(hectares)/1e6, 1) AS Mha
        FROM soil_rain_ph_coarse
    """).fetchone()[0]
    print(f"  pH cube:        {res_ph} Mha")
    print(f"  NLUM cube:      {res} Mha")
    print(
        "  NOTE: NLUM cube is smaller because pixels outside NLUM coverage "
        "(coastal margins / fringe nodata) drop out."
    )

    print("\nNorthern total by NLUM Primary class (Mha):")
    for r in con.execute("""
        SELECT code, label, is_managed, ROUND(hectares / 1e6, 1) AS Mha
        FROM v_nlum_totals_northern
        ORDER BY code
    """).fetchall():
        mark = "managed" if r[2] else "EXCLUDED"
        print(f"  [{r[0]}] {r[1]:<42s} {r[3]:>6.1f}  ({mark})")

    con.close()
    print(f"\nDB updated: {DB_PATH}")


if __name__ == "__main__":
    main()
