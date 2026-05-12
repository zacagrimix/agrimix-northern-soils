"""Shared constants and paths for the soil/rainfall pipeline."""
from pathlib import Path

# ---- Paths ----
ROOT = Path(__file__).resolve().parents[1]
RAW = ROOT / "data" / "raw"
PROCESSED = ROOT / "data" / "processed"
DB_PATH = ROOT / "db" / "agrimix.duckdb"
OUTPUTS = ROOT / "outputs"

SLGA_RASTER = RAW / "slga" / "ASC_EV_C_P_AU_TRN_N.cog.tif"
SLGA_LEGEND = RAW / "slga" / "ASC_legend.txt"
NRM_SHAPEFILE = RAW / "nrm" / "NRM_regions_2020.shp"
WORLDCLIM_DIR = RAW / "worldclim"

MAP_RASTER = PROCESSED / "MAP_aus_2.5min.tif"      # Built by 01_build_map_grid.py
NRM_DUBBO_NORTH = PROCESSED / "nrm_dubbo_north.gpkg"  # Built by 03_select_regions.py

# ---- Region selection ----
DUBBO_LATITUDE = -32.25  # Approximate latitude of Dubbo, NSW

# ---- ASC Soil Orders (Isbell 2nd edition - matches SLGA pixel values 1-14) ----
ASC_LEGEND = {
    1: "Vertosol",
    2: "Sodosol",
    3: "Dermosol",
    4: "Chromosol",
    5: "Ferrosol",
    6: "Kurosol",
    7: "Tenosol",
    8: "Kandosol",
    9: "Hydrosol",
    10: "Podosol",
    11: "Rudosol",
    12: "Calcarosol",
    13: "Organosol",
    14: "Anthroposol",
}

# ---- Coarse rainfall bands (100 mm increments, with a <250 catch-all) ----
COARSE_RAIN_BANDS = [
    ("<250 mm",      0,    250),
    ("250-350 mm",   250,  350),
    ("350-450 mm",   350,  450),
    ("450-550 mm",   450,  550),
    ("550-650 mm",   550,  650),
    ("650-750 mm",   650,  750),
    ("750-850 mm",   750,  850),
    ("850-950 mm",   850,  950),
    ("950-1000 mm",  950,  1000),
    (">1000 mm",     1000, 99999),
]

# ---- Fine rainfall bands (50 mm increments, <50 to >2000) ----
def fine_rain_bands(step=50, low=0, high=2000):
    bands = [(f"<{step} mm", 0, step)]
    for x in range(step, high, step):
        bands.append((f"{x}-{x+step} mm", x, x+step))
    bands.append((f">{high} mm", high, 99999))
    return bands

FINE_RAIN_BANDS = fine_rain_bands()

# ---- WGS84 ellipsoid for accurate pixel area calculation ----
EARTH_RADIUS_EQ = 6378137.0
EARTH_FLATTENING = 1 / 298.257223563
EARTH_E2 = 2 * EARTH_FLATTENING - EARTH_FLATTENING ** 2
