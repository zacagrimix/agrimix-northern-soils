"""Geodetic pixel area utilities (WGS84 ellipsoid)."""
import math
import numpy as np

# Constants — duplicated here to avoid circular relative-import issues
EARTH_RADIUS_EQ = 6378137.0
EARTH_E2 = 2 * (1 / 298.257223563) - (1 / 298.257223563) ** 2


def geodetic_pixel_area(lat_top_deg: float, lat_bot_deg: float, lon_step_deg: float) -> float:
    """Area (m^2) of a quadrilateral pixel on the WGS84 ellipsoid bounded by
    parallels at lat_top_deg/lat_bot_deg and meridians lon_step_deg apart.
    Uses the closed-form integral on the spheroid.
    """
    e = math.sqrt(EARTH_E2)

    def q(lat_rad: float) -> float:
        sp = math.sin(lat_rad)
        return sp / (1 - EARTH_E2 * sp * sp) + (1.0 / (2 * e)) * math.log(
            (1 + e * sp) / (1 - e * sp)
        )

    phi_top = math.radians(lat_top_deg)
    phi_bot = math.radians(lat_bot_deg)
    lon_rad = math.radians(lon_step_deg)
    a = (
        (EARTH_RADIUS_EQ * EARTH_RADIUS_EQ * (1 - EARTH_E2) / 2.0)
        * lon_rad
        * (q(phi_top) - q(phi_bot))
    )
    return abs(a)


def per_row_pixel_areas(transform, height: int) -> np.ndarray:
    """Compute pixel area (m^2) for each row of a raster.
    `transform` is a rasterio Affine; pixels assumed regular in degrees.
    """
    pixel_lon_step = abs(transform.a)
    row_idx = np.arange(height)
    top_lats = transform.f + row_idx * transform.e          # transform.e is negative
    bot_lats = top_lats + transform.e
    return np.array(
        [geodetic_pixel_area(t, b, pixel_lon_step) for t, b in zip(top_lats, bot_lats)]
    )
