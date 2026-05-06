---
name: agrimix-northern-soils
description: |
  Geospatial database and pipeline for Agrimix soil/rainfall/land-value analysis
  across MLA cattle (NRM) regions north of Dubbo, NSW. Trigger when the user
  asks anything about soil orders, rainfall bands, cattle distribution, land
  values, or carbon sequestration potential at the NRM/region scale in northern
  Australia. Also trigger for soil x rainfall cross-tab queries, region
  comparisons, or extending the pipeline with new data layers (vegetation, land
  use, productivity, etc.). The DuckDB file at db/agrimix.duckdb is the source
  of truth; query it directly rather than rebuilding from rasters.
---

# Agrimix Northern Soils Pipeline

## What this is

A reproducible pipeline that turns raw rasters and shapefiles into a single
DuckDB database (`db/agrimix.duckdb`) covering 21 MLA cattle / NRM regions
north of Dubbo, NSW. The database has hectares of every (region, soil order,
rainfall band) combination, plus region attributes (cattle, MAP, $/ha).

**Region scope:**
- All 14 Queensland NRMs (excluding marine-only Torres Strait waters)
- Northern Territory (whole — single NRM polygon)
- 5 northern NSW NRMs whose centroid is at or north of Dubbo (~32.25°S):
  North Coast, Northern Tablelands, North West NSW, Western, Central West
- WA Rangelands (whole — caveat: extends well south of Dubbo)

## Working with the database

**Always query the existing DuckDB file first.** Don't re-run extraction
unless you need to change band definitions or add a new data layer — the
extraction takes ~30 seconds for the coarse cube but several minutes for the
fine cube on the WA Rangelands polygon.

```python
import duckdb
con = duckdb.connect("db/agrimix.duckdb", read_only=True)
df = con.sql("SELECT * FROM v_region_summary").df()
```

DuckDB's spatial extension is loaded by `05_build_database.py`. Region
geometries are stored as WKT in `regions.geom_wkt` for use with
`ST_GeomFromText()`.

### Most useful tables/views

- `soil_rain_coarse` — 1,764 rows: 21 regions × 14 soils × 6 rainfall bands
  (<250, 250-350, 350-500, 500-750, 750-1000, >1000 mm). Use this for
  high-level lookups.
- `soil_rain_fine` — 12,054 rows: same shape but with 50 mm bands from
  <50 mm to >2000 mm. Use this when you need precision at band boundaries.
- `regions` — one row per region with all attributes. Use as the join hub.
- `v_region_summary` — pre-joined view with totals + dominant soil per region.

### Worked example

Question: *"How much Vertosol gets 750-1000 mm in the Fitzroy?"*
Answer (already validated): **352,347 ha**

```sql
SELECT hectares
FROM soil_rain_coarse
WHERE nrm_region = 'Fitzroy'
  AND soil_name = 'Vertosol'
  AND band_label = '750-1000 mm';
```

## Pipeline structure

Five numbered scripts plus a runner:

| Step | Script                            | Inputs                            | Outputs                                  |
|------|-----------------------------------|-----------------------------------|------------------------------------------|
| 1    | `01_build_map_grid.py`            | 30 SILO monthly_rain.nc (1991-2020) | `data/processed/MAP_aus_2.5min.tif`    |
| 2    | `02_select_regions.py`            | NRM Regions 2020 shapefile        | `data/processed/nrm_dubbo_north.gpkg`    |
| 3    | `03_soil_rain_extraction.py`      | SLGA ASC raster + MAP + regions   | Two parquet files (coarse + fine cubes)  |
| 4    | `04_region_attributes.py`         | regions + MAP + hardcoded tables  | `region_attributes.parquet`              |
| 5    | `05_build_database.py`            | All processed parquet/gpkg files  | `db/agrimix.duckdb`                      |

**Run order matters.** Use `python scripts/run_pipeline.py` for a full rebuild.

### Re-running individual steps

Each script is independent given its inputs from earlier steps. Most common
partial re-runs:

- **DB schema change** → `python scripts/05_build_database.py`
- **New rainfall bands** → edit `config.FINE_RAIN_BANDS`, re-run steps 3 + 5
- **New region** → edit `02_select_regions.py` masks, re-run steps 2-5
- **New region attribute** (e.g. ABARES productivity index) → edit `04_*` and
  rebuild step 5

## Key design decisions

### Why DuckDB

Single-file like SQLite, but built for analytical queries. Reads parquet/CSV
natively. The spatial extension handles all the polygon work needed here
without standing up PostGIS.

### Why ellipsoidal pixel area, not pixel count

The SLGA raster is in EPSG:4326 with 3-arc-second pixels. At -10°S a pixel
is ~8,422 m²; at -44°S it's ~6,189 m². A naive area = pixels × 8,100 m²
overstates by ~10-15% in the Top End and understates by similar amount in
southern NSW. `geom_utils.geodetic_pixel_area()` computes the true WGS84
ellipsoidal area of the trapezoid bounded by each pixel's parallels and
meridians — accurate to better than 0.01%.

### Why nearest-neighbour rainfall resampling

SILO is at 0.05° (~5.5 km) and SLGA is at 3 arc-sec (~90 m). Each
MAP cell covers ~50 × 50 = 2,500 SLGA pixels. Bilinear resampling would
smear band boundaries; nearest-neighbour preserves the rainfall band class
each pixel actually falls into. This is the right choice for class-based
zonal stats.

### Why streaming row-by-row (step 3)

The WA Rangelands NRM polygon is 227 Mha. Its bounding box at 90 m resolution
is ~250M pixels, which OOMs a 32 GB machine if loaded as int32 + bool masks
all at once. The script reads one raster row at a time per polygon — peak
memory stays under 1 GB regardless of polygon size.

## Validation

The cross-tab cubes are validated by summing across all rainfall bands per
region per soil and comparing to the soil-only zonal histogram. Matches
within 1% / 500 ha for every cell — **no pixels are dropped or
double-counted.** See output of `03_soil_rain_extraction.py`.

## Caveats to surface in any report

1. SLGA ASC overall classification accuracy ~61% (Searle 2021). 1:100k
   ASRIS polygons override modelled values where they exist, so cropping
   and grazing zones are highest accuracy.
2. SILO 1991-2020 climatology is the current rainfall normal. Pre-2010 SILO
   has thinner station coverage in the WA/NT pastoral interior, so far-west
   totals are model-extrapolated.
3. ASC = Isbell 2nd edition (2002). 3rd-edition Arenosol is not present;
   deep sands sit under Tenosol/Rudosol/Calcarosol.
4. WA Rangelands and NT are single huge polygons (227 Mha and 142 Mha
   respectively). Sub-regional splits not resolved.
5. Land values are sale prices including improvements, not unimproved
   bare-land. Pastoral medians swing 30-50% YoY because n is tiny —
   QLD-West (Desert Channels) median is from only 13 sales in 2024.

## Extending the pipeline

To add a new continuous data layer (e.g. NDVI, woody cover, ABARES income
index):

1. Drop the raster into `data/raw/<source>/`
2. Write `scripts/0X_my_new_layer.py` that reads the regions and produces
   a parquet keyed on `nrm_region` (and optionally `soil_code`/`band_idx`)
3. Add a load step to `05_build_database.py` to register it as a new table
4. Add a view to `05_build_database.py` if it's commonly joined

To add a new categorical data layer (e.g. land use, woody/non-woody
classification): mirror the structure of `03_soil_rain_extraction.py` —
stream the raster row-by-row, accumulate per-class hectares per region.

## Out of scope (deliberately)

- **Per-paddock or per-property analysis** — pipeline operates at NRM scale.
  Property-level analysis needs different inputs (lot/DP polygons, individual
  soil/property surveys).
- **Carbon stock or change calculations** — pipeline gives you the
  inventory, not the dynamics. Plug into DayCent/RothC for that.
- **Time series** — climatology is a 30-year mean. Inter-annual variation
  needs the SILO daily grid.
