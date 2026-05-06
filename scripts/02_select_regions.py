"""Step 2: Select NRM cattle regions north of Dubbo, NSW.

Selection rules:
  - All Queensland NRMs except marine-only Torres Strait waters
  - Northern Territory (single NRM)
  - NSW NRMs whose centroid is at or north of Dubbo's latitude (~32.25 S)
    (excludes Lord Howe Island)
  - WA Rangelands (whole region — caveat: extends well south of Dubbo)

Input:  data/raw/nrm/NRM_regions_2020.shp
Output: data/processed/nrm_dubbo_north.gpkg

Run: python -m scripts.02_select_regions
"""
from __future__ import annotations
import sys
from pathlib import Path
import geopandas as gpd

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from scripts.config import NRM_SHAPEFILE, NRM_DUBBO_NORTH, DUBBO_LATITUDE, PROCESSED


def main() -> None:
    PROCESSED.mkdir(parents=True, exist_ok=True)

    print(f"Loading NRM regions shapefile: {NRM_SHAPEFILE}")
    nrm = gpd.read_file(NRM_SHAPEFILE)

    # Compute centroid latitude in equal-area projection then transform back
    nrm_albers = nrm.to_crs("EPSG:3577")     # GDA94 / Australian Albers
    nrm["cent_lat"] = nrm_albers.geometry.centroid.to_crs("EPSG:4283").y
    nrm["cent_lon"] = nrm_albers.geometry.centroid.to_crs("EPSG:4283").x
    nrm["geom_area_ha"] = nrm_albers.geometry.area / 1e4

    mask_qld = (nrm["STATE"] == "QLD") & (
        nrm["AREA_DESC"] != "includes waters of the Torres Strait"
    )
    mask_nt = nrm["STATE"] == "NT"
    mask_nsw = (
        (nrm["STATE"] == "NSW")
        & (nrm["cent_lat"] >= DUBBO_LATITUDE)
        & (nrm["NRM_REGION"] != "North Coast - Lord Howe Island")
    )
    mask_wa = (nrm["STATE"] == "WA") & (nrm["NRM_REGION"] == "Rangelands Region")

    selected = nrm[mask_qld | mask_nt | mask_nsw | mask_wa].copy()
    print(f"Selected {len(selected)} NRM regions (Dubbo-north cattle regions)")

    # Sort by latitude (north -> south) for readability
    selected = selected.sort_values("cent_lat", ascending=False).reset_index(drop=True)

    selected.to_file(NRM_DUBBO_NORTH, driver="GPKG")
    print(f"Saved -> {NRM_DUBBO_NORTH}")
    print()
    print(selected[["NRM_REGION", "STATE", "cent_lat", "geom_area_ha"]].to_string(index=False))


if __name__ == "__main__":
    main()
