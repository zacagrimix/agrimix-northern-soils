"""Run the full pipeline end-to-end.

Steps:
  1. Build mean annual precipitation grid from monthly WorldClim files
  2. Select Dubbo-north NRM regions
  3. Extract soil x rainfall cubes (coarse 6-band + fine 50mm-band)
  4. Build region attributes (cattle, land values, mean MAP)
  5. Load everything into DuckDB

Run: python scripts/run_pipeline.py
"""
from __future__ import annotations
import importlib.util
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

STEPS = [
    "01_build_map_grid",
    "02_select_regions",
    "03_soil_rain_extraction",
    "04_region_attributes",
    "05_build_database",
]


def _import_step(name: str):
    """Import a step module by file path (handles digit-prefixed filenames)."""
    path = ROOT / "scripts" / f"{name}.py"
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def main() -> None:
    for step in STEPS:
        print()
        print("=" * 80)
        print(f">>> {step}")
        print("=" * 80)
        mod = _import_step(step)
        mod.main()
    print()
    print("=" * 80)
    print(">>> Pipeline complete")
    print("=" * 80)


if __name__ == "__main__":
    main()
