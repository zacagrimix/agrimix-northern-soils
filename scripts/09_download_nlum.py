"""Step 9: Download ABARES Catchment Scale Land Use of Australia (NLUM) raster,
clip to north-of-Dubbo bbox via a windowed + decimated read, save GeoTIFF.

Source: ABARES Catchment Scale Land Use of Australia – Update December 2020
URL:    https://www.agriculture.gov.au/sites/default/files/documents/geotiff_clum_50m1220m.zip
License: CC-BY 4.0

The native GeoTIFF inside the zip is **12.3 GB** at 50 m. Reading the whole
thing then reprojecting takes 10+ minutes. We instead:
  1. Transform the EPSG:4326 north-of-Dubbo bbox into the source CRS (Albers).
  2. Read only the source pixels covering that window, decimated 4× during read.
  3. Reproject just that smaller array to a 0.002° (~220 m) EPSG:4326 grid.

Pixel values use the ALUM Tertiary code (e.g. 110 = Nature conservation,
220 = Grazing native veg, 320 = Cropping). Primary class = value // 100.

Output: data/processed/nlum_primary_north_100m.tif
"""
from __future__ import annotations

import sys
import time
import zipfile
from pathlib import Path

import numpy as np
import rasterio
import requests
from affine import Affine
from rasterio.enums import Resampling
from rasterio.transform import from_origin
from rasterio.warp import reproject, transform_bounds
from rasterio.windows import from_bounds as window_from_bounds

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from scripts.config import PROCESSED, RAW

NLUM_DIR = RAW / "nlum"
ZIP_URL = (
    "https://www.agriculture.gov.au/sites/default/files/documents"
    "/geotiff_clum_50m1220m.zip"
)
ZIP_PATH = NLUM_DIR / "geotiff_clum_50m1220m.zip"
OUT_PATH = PROCESSED / "nlum_primary_north_100m.tif"

# North-of-Dubbo bbox (EPSG:4326)
BBOX = (113.0, -33.0, 154.0, -10.0)
OUT_RES_DEG = 0.002  # ~220 m at 30°S
SRC_DECIMATION = 4   # source is 50 m → effective read resolution ~200 m


def download_zip() -> Path:
    if ZIP_PATH.exists() and ZIP_PATH.stat().st_size > 100_000_000:
        print(f"  exists, skip: {ZIP_PATH.name}", flush=True)
        return ZIP_PATH
    NLUM_DIR.mkdir(parents=True, exist_ok=True)
    print(f"  downloading {ZIP_URL}...", flush=True)
    r = requests.get(ZIP_URL, stream=True, timeout=600)
    r.raise_for_status()
    tmp = ZIP_PATH.with_suffix(".zip.tmp")
    n = 0
    with open(tmp, "wb") as f:
        for chunk in r.iter_content(chunk_size=1 << 20):
            f.write(chunk)
            n += len(chunk)
            if n % (10 << 20) == 0:
                print(f"    {n / 1e6:.0f} MB...", flush=True)
    tmp.rename(ZIP_PATH)
    print(f"    -> {ZIP_PATH.name} ({ZIP_PATH.stat().st_size / 1e6:.1f} MB)",
          flush=True)
    return ZIP_PATH


def find_tif_in_zip(zip_path: Path) -> str:
    with zipfile.ZipFile(zip_path) as z:
        for name in z.namelist():
            if name.lower().endswith(".tif") and "ovr" not in name.lower():
                return name
    raise SystemExit("No .tif found in NLUM zip")


def main() -> None:
    PROCESSED.mkdir(parents=True, exist_ok=True)
    print("Downloading + processing NLUM 2020 raster for north-of-Dubbo...",
          flush=True)
    t0 = time.time()

    download_zip()

    tif_name = find_tif_in_zip(ZIP_PATH)
    src_uri = f"/vsizip/{ZIP_PATH}/{tif_name}"
    print(f"  source raster (in zip): {tif_name}", flush=True)

    with rasterio.open(src_uri) as src:
        print(f"  native: CRS={src.crs}, shape={src.shape}, "
              f"dtype={src.dtypes[0]}, nodata={src.nodata}", flush=True)
        nodata_val = src.nodata if src.nodata is not None else 0
        src_crs = src.crs

        # 1) Transform bbox into source CRS, derive source window.
        src_bbox = transform_bounds(
            "EPSG:4326", src_crs, *BBOX, densify_pts=21
        )
        print(f"  source bbox: {[round(v) for v in src_bbox]}", flush=True)
        win = window_from_bounds(*src_bbox, transform=src.transform)
        win_h = max(1, int(round(win.height)))
        win_w = max(1, int(round(win.width)))
        out_h_src = max(1, win_h // SRC_DECIMATION)
        out_w_src = max(1, win_w // SRC_DECIMATION)

        # 2) Read just that window, decimated.
        print(
            f"  reading source window {win_w}×{win_h} px "
            f"-> {out_w_src}×{out_h_src} (decimated {SRC_DECIMATION}×)...",
            flush=True,
        )
        t1 = time.time()
        src_arr = src.read(
            1,
            window=win,
            out_shape=(out_h_src, out_w_src),
            resampling=Resampling.nearest,
            boundless=True,
            fill_value=nodata_val,
        )
        print(f"    read in {time.time() - t1:.1f}s "
              f"(shape={src_arr.shape}, dtype={src_arr.dtype})", flush=True)

        # Decimated transform = window_transform scaled by decimation factor.
        win_transform = src.window_transform(win)
        dec_transform = win_transform * Affine.scale(
            SRC_DECIMATION, SRC_DECIMATION
        )

    # 3) Reproject decimated source array → EPSG:4326 at 0.002°.
    out_w = int((BBOX[2] - BBOX[0]) / OUT_RES_DEG)
    out_h = int((BBOX[3] - BBOX[1]) / OUT_RES_DEG)
    dst_transform = from_origin(BBOX[0], BBOX[3], OUT_RES_DEG, OUT_RES_DEG)
    print(f"  reprojecting to EPSG:4326 {out_w}×{out_h} px...", flush=True)
    t1 = time.time()
    dst = np.full((out_h, out_w), nodata_val, dtype=src_arr.dtype)
    reproject(
        source=src_arr,
        destination=dst,
        src_transform=dec_transform,
        src_crs=src_crs,
        dst_transform=dst_transform,
        dst_crs="EPSG:4326",
        resampling=Resampling.nearest,
        src_nodata=nodata_val,
        dst_nodata=nodata_val,
        num_threads=4,
    )
    print(f"    reprojected in {time.time() - t1:.1f}s", flush=True)

    # 4) Collapse Tertiary ALUM codes (e.g. 110, 222, 320) → Primary class 1-6.
    primary = np.where(dst == nodata_val, 0, dst // 100).astype(np.uint8)

    unique, counts = np.unique(primary, return_counts=True)
    total = counts.sum()
    print(f"\n  Primary class distribution ({total:,} pixels):", flush=True)
    for u, c in zip(unique, counts):
        print(f"    code {u}: {c:>12,} pixels ({100 * c / total:5.1f}%)",
              flush=True)

    profile = {
        "driver": "GTiff",
        "height": out_h,
        "width": out_w,
        "count": 1,
        "dtype": "uint8",
        "crs": "EPSG:4326",
        "transform": dst_transform,
        "nodata": 0,
        "compress": "deflate",
        "tiled": True,
    }
    with rasterio.open(OUT_PATH, "w", **profile) as out:
        out.write(primary, 1)
    print(f"\nSaved -> {OUT_PATH} "
          f"({OUT_PATH.stat().st_size / 1e6:.1f} MB) "
          f"in {time.time() - t0:.1f}s", flush=True)


if __name__ == "__main__":
    main()
