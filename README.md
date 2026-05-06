# Agrimix Northern Soils — DuckDB Pipeline

Geospatial database of soil orders, mean annual rainfall, cattle counts and
indicative land values for every MLA cattle (NRM) region north of Dubbo, NSW.

## What's in the database

`db/agrimix.duckdb` (built by `scripts/05_build_database.py`):

| Table / view              | Rows  | Description                                                                |
|---------------------------|------:|----------------------------------------------------------------------------|
| `regions`                 | 21    | NRM cattle regions north of Dubbo (centroid, area, MAP, cattle, $/ha)      |
| `asc_soil_orders`         | 14    | Lookup: soil_code (1-14) -> Isbell ASC Soil Order name                     |
| `rainfall_bands_coarse`   | 6     | Lookup: <250, 250-350, 350-500, 500-750, 750-1000, >1000 mm                |
| `rainfall_bands_fine`     | 41    | Lookup: 50 mm bands from <50 mm to >2000 mm                                |
| `soil_rain_coarse`        | 1,764 | **Fact table**: hectares per (region, soil, coarse_band)                   |
| `soil_rain_fine`          | 12,054| **Fact table**: hectares per (region, soil, 50mm_band)                     |
| `v_region_summary`        | 21    | View: regions joined with totals and dominant soil                         |
| `v_soil_totals_northern`  | 14    | View: total ha per soil across all 21 regions                              |
| `v_rain_totals_northern`  | 6     | View: total ha per coarse rainfall band across all 21 regions              |
| `v_total_estimated_value` | 21    | View: total mapped ha × $/ha = AUD estimated land value per region         |

## Quick start

```bash
# 1. Set up environment (Python 3.11+)
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt

# 2. Run the full pipeline (~60 seconds end-to-end on a typical laptop)
python scripts/run_pipeline.py

# 3. Query the database
python -c "import duckdb; con = duckdb.connect('db/agrimix.duckdb'); print(con.sql('SELECT * FROM v_region_summary').df())"
```

## Example queries

**How much Vertosol gets 750-1000 mm in the Fitzroy?**
```sql
SELECT hectares
FROM soil_rain_coarse
WHERE nrm_region = 'Fitzroy'
  AND soil_name = 'Vertosol'
  AND band_label = '750-1000 mm';
```

**Top cracking-clay (Vertosol) country in the 500-750 mm zone, ranked**
```sql
SELECT nrm_region, ROUND(hectares) AS ha
FROM soil_rain_coarse
WHERE soil_name = 'Vertosol' AND band_label = '500-750 mm'
ORDER BY hectares DESC;
```

**Soil profile of any region using 50 mm bands**
```sql
SELECT band_label, soil_name, ROUND(hectares) AS ha
FROM soil_rain_fine
WHERE nrm_region = 'Burdekin' AND hectares > 1000
ORDER BY band_idx, hectares DESC;
```

**All soils above the 750 mm threshold, by region**
```sql
SELECT nrm_region, soil_name, SUM(hectares) AS ha
FROM soil_rain_fine
WHERE band_low_mm >= 750
GROUP BY nrm_region, soil_name
HAVING SUM(hectares) > 100000
ORDER BY nrm_region, ha DESC;
```

## Data sources

| Layer                    | Source                                                                | Licence    | Vintage      |
|--------------------------|-----------------------------------------------------------------------|------------|--------------|
| Soil order (ASC, 90 m)   | TERN/CSIRO SLGA v2 Australian Soil Classification Map (Searle 2021)   | CC-BY 4.0  | DOI 10.25919/vkjn-3013 |
| Mean annual precip       | SILO (Qld Govt / BoM), 0.05° monthly grids, 30-yr mean                | CC-BY 4.0  | 1991-2020    |
| NRM region polygons      | Australian Government DCCEEW — NRM Regions 2020                       | CC-BY 4.0  | 2020         |
| Cattle counts by region  | MLA Cattle Distribution Map (ABS source)                              | MLA        | June 2021    |
| Land values              | Bendigo Bank Agribusiness Australian Farmland Values 2025             | Public     | 2024 sales   |

## Repository layout

```
agrimix-northern-soils/
├── README.md                 ← this file
├── SKILL.md                  ← Claude Code orientation
├── requirements.txt
├── data/
│   ├── raw/                  ← bundled raw inputs (~213 MB)
│   │   ├── slga/             ← SLGA ASC raster + legend
│   │   ├── silo/             ← 30 yearly monthly_rain.nc files (1991-2020)
│   │   ├── nrm/              ← NRM Regions 2020 shapefile
│   │   ├── mla/              ← MLA cattle distribution (PDF source link)
│   │   └── bendigo_bank/     ← Australian Farmland Values 2025 PDF
│   └── processed/            ← intermediate outputs (built by scripts)
│       ├── MAP_aus_2.5min.tif
│       ├── nrm_dubbo_north.gpkg
│       ├── region_attributes.parquet
│       ├── soil_rain_coarse.parquet
│       └── soil_rain_fine.parquet
├── scripts/
│   ├── config.py             ← paths, lookups, band definitions
│   ├── geom_utils.py         ← WGS84 ellipsoidal pixel area math
│   ├── 01_build_map_grid.py
│   ├── 02_select_regions.py
│   ├── 03_soil_rain_extraction.py
│   ├── 04_region_attributes.py
│   ├── 05_build_database.py
│   └── run_pipeline.py       ← runs all 5 steps end-to-end
├── db/
│   └── agrimix.duckdb        ← the database
├── notebooks/                ← Jupyter notebooks for ad-hoc analysis
└── outputs/                  ← XLSX reports, exports
```

## Caveats

- **SLGA ASC overall accuracy ≈ 61%** at pixel level. Where 1:100k ASRIS polygons
  exist they override the modelled value, so cropping/grazing zones are highest
  accuracy.
- **SILO 1991-2020 climatology** is the current rainfall normal. Northern Australia
  is in a wetter post-2000 regime than the older WorldClim 1970-2000 baseline; SILO
  captures this. Note: pre-2010 SILO has thinner station coverage in the WA/NT
  pastoral interior, so far-west totals are model-extrapolated.
- **WA Rangelands and NT** are each single very large NRM polygons. Sub-regional
  splits (Kimberley vs Pilbara vs Goldfields; Top End vs Centre) are not resolved.
- **ASC is Isbell 2nd edition** (2002). The 3rd-edition Arenosol order is not
  represented; deep sands sit under Tenosol/Rudosol/Calcarosol.
- **Land values are sale prices including improvements**, not unimproved bare-land
  values, and are skewed by parcel-size mix. Treat as indicative for scoping.

## Re-running

The pipeline is fully deterministic. Re-running drops and rebuilds
`db/agrimix.duckdb`. Intermediate parquet/GeoTIFF artefacts in
`data/processed/` are also regenerated.

For a partial re-run (e.g. just rebuild the DB after edits to step 5):

```bash
python scripts/05_build_database.py
```
