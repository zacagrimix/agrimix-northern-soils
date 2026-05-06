"""Download SoilGrids 2.0 pH (water) for north-of-Dubbo bbox at ~500 m resolution.

Pulls 3 depth layers (0-5, 5-15, 15-30 cm), then computes a depth-weighted
0-30 cm aggregate as data/processed/pH_water_0-30cm_north_500m.tif.

Source: ISRIC SoilGrids 2.0 (CC-BY 4.0).
"""
from pathlib import Path

import numpy as np
import rasterio
import requests
from rasterio.transform import from_bounds

PROJECT_ROOT = Path(__file__).resolve().parent.parent
RAW_DIR = PROJECT_ROOT / "data" / "raw" / "soilgrids"
PROC_DIR = PROJECT_ROOT / "data" / "processed"
RAW_DIR.mkdir(parents=True, exist_ok=True)

# North-of-Dubbo bbox (slightly padded). Matches the soil/rain pipeline scope.
BBOX = (113.0, -33.0, 154.0, -10.0)  # west, south, east, north
WIDTH = 8200    # ~500 m at this latitude
HEIGHT = 4600

DEPTHS = [(0, 5), (5, 15), (15, 30)]


def wcs_url(coverage_id: str) -> str:
    return (
        "https://maps.isric.org/mapserv?map=/map/phh2o.map"
        "&SERVICE=WCS&VERSION=2.0.1&REQUEST=GetCoverage"
        f"&COVERAGEID={coverage_id}"
        "&FORMAT=image/tiff"
        f"&SUBSET=long({BBOX[0]},{BBOX[2]})"
        f"&SUBSET=lat({BBOX[1]},{BBOX[3]})"
        "&SUBSETTINGCRS=http://www.opengis.net/def/crs/EPSG/0/4326"
        "&OUTPUTCRS=http://www.opengis.net/def/crs/EPSG/0/4326"
        f"&SCALESIZE=long({WIDTH}),lat({HEIGHT})"
    )


def download_layer(top: int, bot: int) -> Path:
    cov = f"phh2o_{top}-{bot}cm_mean"
    out = RAW_DIR / f"{cov}_north_500m.tif"
    if out.exists():
        print(f"  exists, skip: {out.name}")
        return out
    print(f"  downloading {cov}...")
    url = wcs_url(cov)
    r = requests.get(url, stream=True, timeout=300)
    r.raise_for_status()
    with open(out, "wb") as f:
        for chunk in r.iter_content(chunk_size=1 << 20):
            f.write(chunk)
    print(f"    -> {out.name} ({out.stat().st_size / 1e6:.1f} MB)")
    return out


def build_aggregate(layer_paths: list[Path]) -> Path:
    out = PROC_DIR / "pH_water_0-30cm_north_500m.tif"
    print(f"\nBuilding 0-30 cm depth-weighted aggregate -> {out.name}")

    weights = np.array([5, 10, 15], dtype=np.float32)  # 0-5, 5-15, 15-30
    weights /= weights.sum()

    arrays = []
    profile = None
    for p in layer_paths:
        with rasterio.open(p) as src:
            if profile is None:
                profile = src.profile.copy()
            arrays.append(src.read(1).astype(np.float32))

    stack = np.stack(arrays)  # (3, H, W)
    valid = (stack > 0)        # SoilGrids uses 0 for nodata at sea
    nvalid = valid.sum(axis=0)

    # Weighted mean over valid layers; if no valid layers at a pixel -> nodata
    weighted = np.where(valid, stack * weights[:, None, None], 0).sum(axis=0)
    weight_used = np.where(valid, weights[:, None, None], 0).sum(axis=0)
    agg = np.where(weight_used > 0, weighted / weight_used, -32768).astype(
        np.int16
    )

    profile.update(dtype="int16", nodata=-32768, compress="lzw", tiled=True)
    with rasterio.open(out, "w", **profile) as dst:
        dst.write(agg, 1)

    valid_pct = (agg != -32768).mean() * 100
    valid_vals = agg[agg != -32768]
    print(
        f"  pH range: {valid_vals.min() / 10:.1f} - "
        f"{valid_vals.max() / 10:.1f}, mean {valid_vals.mean() / 10:.2f}"
    )
    print(f"  valid pixels: {valid_pct:.0f}%")
    print(f"  output size: {out.stat().st_size / 1e6:.1f} MB")
    return out


def main():
    print("Downloading SoilGrids 2.0 pH (water) layers, north-of-Dubbo bbox...")
    layers = [download_layer(t, b) for t, b in DEPTHS]
    build_aggregate(layers)
    print("\nDone.")


if __name__ == "__main__":
    main()
