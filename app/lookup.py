"""Agrimix northern soils — interactive lookup with map.

Run: streamlit run app/lookup.py
"""
from pathlib import Path

import branca.colormap as cm
import duckdb
import folium
import shapely.wkt
import streamlit as st
from shapely.geometry import mapping
from streamlit_folium import st_folium

from colored_layer_control import ColoredLayerControl
from overlay import NLUM_COLOR, render_nlum_overlay, render_overlay

DB = Path(__file__).resolve().parent.parent / "db" / "agrimix.duckdb"

st.set_page_config(
    page_title="Agrimix northern soils",
    page_icon="🌱",
    layout="wide",
)


# --- Agrimix brand styling ---
AGRIMIX_GREEN = "#275D38"
AGRIMIX_LIME = "#BED600"
AGRIMIX_OLIVE = "#869329"
AGRIMIX_BG_TINT = "#F5F7F1"

st.markdown(f"""
<style>
@import url('https://fonts.googleapis.com/css2?family=Inter:wght@400;500;600;700&display=swap');

html, body, [class*="css"] {{
    font-family: 'Inter', Helvetica, Arial, sans-serif !important;
}}

/* Hide the default Streamlit top toolbar (Deploy button, kebab menu) so the
   Agrimix brand bar can sit cleanly at the top. */
[data-testid="stHeader"] {{ display: none !important; }}
#MainMenu {{ display: none !important; }}
footer {{ display: none !important; }}

/* Headings — forest green like the Agrimix site */
h1, h2, h3 {{
    color: {AGRIMIX_GREEN} !important;
    font-weight: 600 !important;
    letter-spacing: -0.01em;
}}
h1 {{ font-size: 2.4rem !important; }}
h2 {{ font-size: 1.6rem !important; }}
h3 {{ font-size: 1.25rem !important; }}

/* Brand bar at the top — span the full page width using viewport units */
.agrimix-bar {{
    background: {AGRIMIX_GREEN};
    padding: 16px clamp(20px, 4vw, 60px);
    margin: -1rem calc(50% - 50vw) 1.5rem calc(50% - 50vw);
    width: 100vw;
    box-sizing: border-box;
    border-bottom: 4px solid {AGRIMIX_LIME};
    color: white;
    display: flex;
    align-items: baseline;
    gap: 16px;
    font-family: 'Inter', Helvetica, sans-serif;
    flex-wrap: wrap;
}}
.agrimix-bar .brand {{
    font-size: 24px;
    font-weight: 700;
    letter-spacing: -0.02em;
    color: white;
}}
.agrimix-bar .tagline {{
    font-size: 13px;
    color: {AGRIMIX_LIME};
    font-style: italic;
    font-weight: 500;
}}
.agrimix-bar .product {{
    margin-left: auto;
    font-size: 13px;
    opacity: 0.85;
    color: white;
}}

/* Multiselect chips — green like Agrimix tags */
[data-baseweb="tag"] {{
    background-color: {AGRIMIX_GREEN} !important;
    color: white !important;
    border-radius: 3px !important;
}}
[data-baseweb="tag"] span {{ color: white !important; }}

/* Primary buttons / focus rings */
button[kind="primary"] {{
    background-color: {AGRIMIX_GREEN} !important;
    color: white !important;
    border-radius: 3px !important;
}}
button[kind="primary"]:hover {{
    background-color: {AGRIMIX_LIME} !important;
    color: {AGRIMIX_GREEN} !important;
}}

/* Dropdown / selectbox borders */
[data-baseweb="select"] > div {{
    border-radius: 3px !important;
}}
[data-baseweb="select"] > div:focus-within {{
    border-color: {AGRIMIX_GREEN} !important;
    box-shadow: 0 0 0 1px {AGRIMIX_GREEN} !important;
}}

/* Expander headers */
.streamlit-expanderHeader, [data-testid="stExpander"] summary {{
    font-weight: 500 !important;
}}

/* Metric (big "Hectares matching" number) */
[data-testid="stMetricValue"] {{
    color: {AGRIMIX_GREEN};
    font-weight: 700;
}}
[data-testid="stMetricLabel"] {{
    color: {AGRIMIX_OLIVE};
    text-transform: uppercase;
    font-size: 0.75rem;
    letter-spacing: 0.06em;
    font-weight: 600;
}}

/* Checkbox accent */
[data-baseweb="checkbox"] [data-checked="true"] {{
    background-color: {AGRIMIX_GREEN} !important;
    border-color: {AGRIMIX_GREEN} !important;
}}

/* Caption / muted text */
.stCaption, [data-testid="stCaptionContainer"] {{
    color: #6a7770 !important;
}}

/* Divider in Agrimix lime */
hr {{
    border-color: {AGRIMIX_LIME} !important;
    border-top-width: 2px !important;
    opacity: 0.4;
}}

/* Tighten the page top padding */
.block-container {{ padding-top: 1rem !important; }}
</style>

<div class="agrimix-bar">
  <span class="brand">Agrimix</span>
  <span class="tagline">Science. Integrity. Results.</span>
  <span class="product">Northern Soils — region × soil × rainfall × pH lookup</span>
</div>
""", unsafe_allow_html=True)


@st.cache_resource
def get_con():
    return duckdb.connect(str(DB), read_only=True)


@st.cache_data
def get_geoms():
    """Region geometries.

    `wkt` is the full-precision polygon (used by overlay.py to mask raster
    pixels — must stay accurate). `geom` is a *simplified* GeoJSON used only
    for drawing on the map: we drop vertices below ~500 m which is invisible
    at continent zoom and shrinks the serialized map HTML by ~10× on Cloud.
    """
    SIMPLIFY_TOL = 0.005  # ~500 m — invisible at zoom <= 7
    rows = get_con().execute(
        "SELECT nrm_region, state, geom_wkt FROM regions"
    ).fetchall()
    out = {}
    for name, state, wkt in rows:
        full = shapely.wkt.loads(wkt)
        simple = full.simplify(SIMPLIFY_TOL, preserve_topology=True)
        out[name] = {
            "state": state,
            "wkt": wkt,                      # full-precision (for raster mask)
            "geom": mapping(simple),         # simplified (for map drawing)
        }
    return out


@st.cache_data
def get_soil_codes():
    return dict(get_con().execute(
        "SELECT soil_name, soil_code FROM asc_soil_orders"
    ).fetchall())


@st.cache_data
def get_band_bounds():
    rows = get_con().execute(
        "SELECT band_label, band_low_mm, band_high_mm FROM rainfall_bands_coarse"
    ).fetchall()
    return {label: (low, high) for label, low, high in rows}


@st.cache_data
def get_ph_bounds():
    rows = get_con().execute(
        "SELECT band_label, band_low, band_high FROM ph_bands_coarse "
        "ORDER BY band_idx"
    ).fetchall()
    return {label: (low, high) for label, low, high in rows}


@st.cache_data
def get_nlum_classes():
    """Return [(code, label, is_managed), ...] from the nlum_classes lookup."""
    try:
        rows = get_con().execute(
            "SELECT code, label, is_managed FROM nlum_classes ORDER BY code"
        ).fetchall()
        return [(int(r[0]), r[1], bool(r[2])) for r in rows]
    except Exception:
        return []  # DB doesn't have NLUM yet (pre-pipeline-rerun)


@st.cache_data
def get_options():
    con = get_con()
    regions = [r[0] for r in con.execute(
        "SELECT nrm_region FROM regions ORDER BY nrm_region"
    ).fetchall()]
    soils = [r[0] for r in con.execute(
        "SELECT soil_name FROM asc_soil_orders ORDER BY soil_name"
    ).fetchall()]
    bands = [r[0] for r in con.execute(
        "SELECT band_label FROM rainfall_bands_coarse ORDER BY band_idx"
    ).fetchall()]
    phs = [r[0] for r in con.execute(
        "SELECT band_label FROM ph_bands_coarse ORDER BY band_idx"
    ).fetchall()]
    return regions, soils, bands, phs


SOIL_RGBA = (200, 30, 30, 220)        # red — single-soil pixel highlight
RAIN_RGBA = (30, 90, 200, 200)        # blue — single-band pixel highlight
PH_RGBA = (60, 170, 60, 220)          # green — single-pH pixel highlight

# Characteristic surface→subsoil texture for each ASC Soil Order.
# These are typical tendencies (Isbell 2002 / NSOTL); individual profiles vary.
SOIL_TEXTURE = {
    "Vertosol":    "clay",
    "Sodosol":     "loam over sodic clay",
    "Dermosol":    "clay loam over clay",
    "Chromosol":   "loam over clay",
    "Ferrosol":    "friable clay",
    "Kurosol":     "loam over acid clay",
    "Tenosol":     "sand / sandy loam",
    "Kandosol":    "loam (massive earth)",
    "Hydrosol":    "wet (often clay)",
    "Podosol":     "sand",
    "Rudosol":     "variable / skeletal",
    "Calcarosol":  "calcareous loam",
    "Organosol":   "peat",
    "Anthroposol": "man-modified",
}


def soil_label(name: str) -> str:
    """e.g. 'Vertosol' -> 'Vertosol (clay)'."""
    tex = SOIL_TEXTURE.get(name)
    return f"{name} ({tex})" if tex else name

DISTINCT_RGBA = [
    (228, 26, 28, 220), (55, 126, 184, 220), (77, 175, 74, 220),
    (152, 78, 163, 220), (255, 127, 0, 220), (255, 217, 47, 220),
    (166, 86, 40, 220), (247, 129, 191, 220), (102, 102, 102, 220),
    (0, 200, 200, 220), (102, 51, 0, 220), (200, 50, 100, 220),
    (50, 100, 50, 220), (100, 50, 200, 220),
]

BLUE_SEQ_RGBA = [
    (220, 240, 255, 220), (165, 215, 245, 220), (90, 175, 220, 220),
    (40, 130, 200, 220), (20, 80, 165, 220), (10, 40, 110, 220),
]

GREEN_SEQ_RGBA = [
    (235, 250, 230, 220), (190, 230, 180, 220), (140, 200, 130, 220),
    (90, 170, 80, 220), (50, 130, 50, 220), (15, 80, 25, 220),
]


def _pick_palette(n, kind):
    if kind == "sequential_blue":
        if n == 1:
            return [BLUE_SEQ_RGBA[-1]]
        idxs = [int(i * (len(BLUE_SEQ_RGBA) - 1) / (n - 1)) for i in range(n)]
        return [BLUE_SEQ_RGBA[i] for i in idxs]
    if kind == "sequential_green":
        if n == 1:
            return [GREEN_SEQ_RGBA[-1]]
        idxs = [int(i * (len(GREEN_SEQ_RGBA) - 1) / (n - 1)) for i in range(n)]
        return [GREEN_SEQ_RGBA[i] for i in idxs]
    return DISTINCT_RGBA[:n]


@st.cache_data(max_entries=40, show_spinner="Rendering map overlay…")
def cached_overlay(region_wkts, layers_t, nlum_codes_filter=()):
    return render_overlay(
        region_geom_wkts=region_wkts,
        layers=layers_t,
        nlum_codes_filter=nlum_codes_filter,
    )


@st.cache_data(max_entries=20, show_spinner="Rendering land-use layer…")
def cached_nlum_overlay(region_wkts):
    return render_nlum_overlay(region_geom_wkts=region_wkts)


def fmt_selection(label, selected, total_options):
    if not selected:
        return f"{label}: **All**"
    if len(selected) == 1:
        return f"{label}: **{selected[0]}**"
    if len(selected) <= 3:
        return f"{label}: **{', '.join(selected)}**"
    return f"{label}: **{len(selected)} of {total_options} selected**"


regions, soils, bands, phs = get_options()

c1, c2, c3, c4 = st.columns(4)
region_sel = c1.multiselect("Region(s)", regions, placeholder="All regions")
soil_sel = c2.multiselect(
    "Soil order(s)",
    soils,
    placeholder="All soils",
    format_func=soil_label,
)
band_sel = c3.multiselect("Rainfall band(s)", bands, placeholder="All bands")
ph_sel = c4.multiselect("pH band(s)", phs, placeholder="All pH bands")

opt_a, opt_b, opt_c = st.columns([2, 2, 2])
shade_regions = opt_a.checkbox(
    "Shade regions by hectares",
    value=False,
    help="Tint each region polygon by total hectares matching your filters.",
)
nlum_classes = get_nlum_classes()
nlum_available = bool(nlum_classes)
exclude_unmanaged = opt_b.checkbox(
    "Exclude non-grazing land",
    value=False,
    disabled=not nlum_available,
    help="Remove pixels classed by ABARES NLUM as Conservation, Intensive "
         "use, or Water from totals and the breakdown. Source = ABARES "
         "Catchment Scale Land Use of Australia (2020 release).",
)
show_landuse = opt_c.checkbox(
    "Show land-use overlay on map",
    value=False,
    disabled=not nlum_available,
    help="Tint every pixel by its NLUM Primary class. Useful for seeing "
         "where parks / cropping / urban actually sit.",
)

con = get_con()


def in_clause(col, values):
    if not values:
        return None, []
    placeholders = ", ".join(["?"] * len(values))
    return f"{col} IN ({placeholders})", list(values)


# When pH is in the filter, query the 4-D cube; otherwise use the original 3-D.
USE_PH_CUBE = bool(ph_sel)
# When the user wants non-grazing land excluded, the 5-D NLUM cube is the
# source of truth (only it has the nlum_code column to filter on).
USE_NLUM_CUBE = exclude_unmanaged and nlum_available
if USE_NLUM_CUBE:
    fact_table = "soil_rain_ph_nlum_coarse"
    rain_col = "rain_band_label"
    rain_idx_col = "rain_band_idx"
    # 2 = Grazing native veg, 3 = Dryland ag, 4 = Irrigated ag
    fixed_filters: list[str] = ["nlum_code IN (2, 3, 4)"]
elif USE_PH_CUBE:
    fact_table = "soil_rain_ph_coarse"
    rain_col = "rain_band_label"
    rain_idx_col = "rain_band_idx"
    fixed_filters = []
else:
    fact_table = "soil_rain_coarse"
    rain_col = "band_label"
    rain_idx_col = "band_idx"
    fixed_filters = []
# pH filter is only meaningful when querying a cube that has pH dimension.
HAS_PH_DIM = USE_NLUM_CUBE or USE_PH_CUBE

# --- Total hectares matching all filters ---
filters, params = list(fixed_filters), []
for col, vals in [
    ("nrm_region", region_sel),
    ("soil_name", soil_sel),
    (rain_col, band_sel),
    ("ph_band_label", ph_sel) if HAS_PH_DIM else (None, []),
]:
    if col is None:
        continue
    clause, p = in_clause(col, vals)
    if clause:
        filters.append(clause)
        params.extend(p)
where = ("WHERE " + " AND ".join(filters)) if filters else ""

total = con.execute(
    f"SELECT COALESCE(SUM(hectares), 0) FROM {fact_table} {where}", params
).fetchone()[0]

m1, m2 = st.columns([1, 3])
m1.metric("Hectares matching", f"{total:,.0f} ha")
caption_bits = [
    fmt_selection("Region", region_sel, len(regions)),
    fmt_selection("Soil", soil_sel, len(soils)),
    fmt_selection("Rainfall", band_sel, len(bands)),
    fmt_selection("pH", ph_sel, len(phs)),
]
if USE_NLUM_CUBE:
    caption_bits.append("Land use: **grazing/cropping only**")
m2.caption(" · ".join(caption_bits))

# --- Choropleth: ha per region for selected (soil, band, pH) ---
map_filters, map_params = list(fixed_filters), []
for col, vals in [
    ("soil_name", soil_sel),
    (rain_col, band_sel),
    ("ph_band_label", ph_sel) if HAS_PH_DIM else (None, []),
]:
    if col is None:
        continue
    clause, p = in_clause(col, vals)
    if clause:
        map_filters.append(clause)
        map_params.extend(p)
map_where = ("WHERE " + " AND ".join(map_filters)) if map_filters else ""

region_ha = dict(con.execute(
    f"""SELECT nrm_region, SUM(hectares)
        FROM {fact_table} {map_where} GROUP BY nrm_region""",
    map_params,
).fetchall())

geoms = get_geoms()
selected_set = set(region_sel)
max_ha = max(region_ha.values()) if region_ha else 1
colormap = cm.linear.YlOrBr_09.scale(0, max(max_ha, 1))

fmap = folium.Map(location=[-22, 138], zoom_start=4, tiles=None)
folium.TileLayer("cartodbpositron", control=False).add_to(fmap)

for name, info in geoms.items():
    ha = region_ha.get(name, 0)
    is_selected = name in selected_set
    if shade_regions:
        fill_color = colormap(ha) if ha > 0 else "#dddddd"
        fill_opacity = 0.35 if ha > 0 else 0.08
    else:
        fill_color = "#ffffff"
        fill_opacity = 0.0
    folium.GeoJson(
        info["geom"],
        style_function=lambda feat, fc=fill_color, fo=fill_opacity, sel=is_selected: {
            "fillColor": fc,
            "color": "#000000",
            "weight": 4 if sel else 2,
            "fillOpacity": fo,
        },
        tooltip=folium.Tooltip(
            f"<b>{name}</b> ({info['state']})<br>{ha:,.0f} ha"
        ),
        control=False,
    ).add_to(fmap)

# --- Pixel-level overlay ---
legend_items: list[tuple[str, tuple[int, int, int, int]]] = []
if soil_sel or band_sel or ph_sel:
    soil_codes_map = get_soil_codes()
    band_bounds_map = get_band_bounds()
    ph_bounds_map = get_ph_bounds()

    region_wkts_t = tuple(geoms[r]["wkt"] for r in region_sel)
    soil_codes_t = tuple(soil_codes_map[s] for s in soil_sel)
    band_ranges_t = tuple(band_bounds_map[b] for b in band_sel)
    ph_ranges_t = tuple(ph_bounds_map[p] for p in ph_sel)

    # Coloring priority: soil > rain > pH (highest-priority dim with 2+ items wins)
    color_by_soil = len(soil_sel) >= 2
    color_by_band = len(band_sel) >= 2 and not color_by_soil
    color_by_ph = (
        len(ph_sel) >= 2 and not color_by_soil and not color_by_band
    )

    if color_by_soil:
        palette = _pick_palette(len(soil_sel), "categorical")
        layers_t = tuple(
            ((soil_codes_map[s],), band_ranges_t, ph_ranges_t, palette[i])
            for i, s in enumerate(soil_sel)
        )
        layer_labels = [soil_label(s) for s in soil_sel]
        legend_items = list(zip(layer_labels, palette))
    elif color_by_band:
        palette = _pick_palette(len(band_sel), "sequential_blue")
        layers_t = tuple(
            (soil_codes_t, (band_bounds_map[b],), ph_ranges_t, palette[i])
            for i, b in enumerate(band_sel)
        )
        layer_labels = list(band_sel)
        legend_items = list(zip(band_sel, palette))
    elif color_by_ph:
        palette = _pick_palette(len(ph_sel), "sequential_green")
        layers_t = tuple(
            (soil_codes_t, band_ranges_t, (ph_bounds_map[p],), palette[i])
            for i, p in enumerate(ph_sel)
        )
        layer_labels = [f"pH {p}" for p in ph_sel]
        legend_items = list(zip(layer_labels, palette))
    else:
        if soil_sel:
            single_color = SOIL_RGBA
            label = (", ".join(soil_label(s) for s in soil_sel)
                     if len(soil_sel) <= 3
                     else f"{len(soil_sel)} soils")
        elif band_sel:
            single_color = RAIN_RGBA
            label = (", ".join(band_sel) if len(band_sel) <= 3
                     else f"{len(band_sel)} bands")
        else:
            single_color = PH_RGBA
            label = (", ".join(f"pH {p}" for p in ph_sel)
                     if len(ph_sel) <= 3 else f"{len(ph_sel)} pH bands")
        layers_t = ((soil_codes_t, band_ranges_t, ph_ranges_t, single_color),)
        layer_labels = [label]

    rgbas, bounds = cached_overlay(region_wkts_t, layers_t)
    for label, layer_rgba in zip(layer_labels, rgbas):
        folium.raster_layers.ImageOverlay(
            image=layer_rgba,
            bounds=bounds,
            opacity=1.0,
            interactive=False,
            cross_origin=False,
            name=label,
            overlay=True,
            control=True,
            show=True,
        ).add_to(fmap)
    if len(rgbas) >= 2:
        ordered_labels = [label for label, _ in legend_items]
        ordered_colors = [
            f"rgba({c[0]},{c[1]},{c[2]},{c[3] / 255:.2f})"
            for _, c in legend_items
        ]
        fmap.add_child(ColoredLayerControl(
            labels=ordered_labels,
            colors=ordered_colors,
            position="bottomright",
        ))
    fmap.fit_bounds(bounds)

elif region_sel:
    coords = []

    def _walk(g):
        if g["type"] == "Polygon":
            for ring in g["coordinates"]:
                coords.extend(ring)
        elif g["type"] == "MultiPolygon":
            for poly in g["coordinates"]:
                for ring in poly:
                    coords.extend(ring)

    for r in region_sel:
        _walk(geoms[r]["geom"])
    if coords:
        xs = [c[0] for c in coords]
        ys = [c[1] for c in coords]
        fmap.fit_bounds([[min(ys), min(xs)], [max(ys), max(xs)]])

# --- Land-use overlay (toggleable, on top of choropleth, under filter pixels) ---
if show_landuse and nlum_available:
    region_wkts_for_nlum = tuple(geoms[r]["wkt"] for r in region_sel)
    nlum_rgba, nlum_bounds = cached_nlum_overlay(region_wkts_for_nlum)
    folium.raster_layers.ImageOverlay(
        image=nlum_rgba,
        bounds=nlum_bounds,
        opacity=0.65,
        interactive=False,
        cross_origin=False,
        name="Land use (NLUM)",
        overlay=True,
        control=False,
    ).add_to(fmap)

st_folium(fmap, height=620, use_container_width=True, returned_objects=[])

# Static legend for the land-use overlay (when shown).
if show_landuse and nlum_available:
    def _nlum_rgba_css(code):
        c = NLUM_COLOR.get(code, (160, 160, 160, 180))
        return f"rgba({c[0]},{c[1]},{c[2]},{c[3] / 255:.2f})"

    chips = "&nbsp;&nbsp;".join(
        f'<span style="display:inline-block;width:16px;height:16px;'
        f'background:{_nlum_rgba_css(code)};border:1px solid #555;'
        f'vertical-align:middle;margin-right:6px;"></span>'
        f'<span style="vertical-align:middle;margin-right:14px;">{lbl}</span>'
        for code, lbl, _ in nlum_classes
    )
    st.markdown(
        "**Land-use overlay legend:** " + chips,
        unsafe_allow_html=True,
    )

# --- CSV download ---
# Always source from the 5-D NLUM cube when available (it has all dimensions).
# Filter to managed land if the user ticked "Exclude non-grazing land".
if nlum_available:
    csv_table = "soil_rain_ph_nlum_coarse"
    csv_extra_cols = ", nlum_label AS \"Land use\""
    csv_extra_filters = ["nlum_code IN (2, 3, 4)"] if exclude_unmanaged else []
else:
    csv_table = "soil_rain_ph_coarse"
    csv_extra_cols = ""
    csv_extra_filters = []
csv_filters, csv_params = list(csv_extra_filters), []
for col, vals in [
    ("nrm_region", region_sel),
    ("soil_name", soil_sel),
    ("rain_band_label", band_sel),
    ("ph_band_label", ph_sel),
]:
    clause, p = in_clause(col, vals)
    if clause:
        csv_filters.append(clause)
        csv_params.extend(p)
csv_where = "WHERE " + " AND ".join(["hectares > 0"] + csv_filters)
csv_df = con.execute(
    f"""SELECT
            nrm_region   AS "Region",
            soil_name    AS "Soil order",
            rain_band_label AS "Rainfall band",
            ph_band_label AS "pH band"{csv_extra_cols},
            ROUND(hectares)::BIGINT AS "Hectares"
        FROM {csv_table}
        {csv_where}
        ORDER BY hectares DESC""",
    csv_params,
).fetchdf()
csv_df.insert(
    2, "Texture", csv_df["Soil order"].map(SOIL_TEXTURE).fillna("")
)

st.subheader("Breakdown")
dl_col1, dl_col2 = st.columns([3, 1])
dl_col1.caption(
    f"{len(csv_df):,} non-zero rows matching your filters · "
    f"{int(csv_df['Hectares'].sum()):,} ha total"
)
dl_col2.download_button(
    label="Download CSV",
    data=csv_df.to_csv(index=False).encode("utf-8"),
    file_name="agrimix-northern-soils-lookup.csv",
    mime="text/csv",
    use_container_width=True,
)

group_cols = []
dims = [
    ("nrm_region", region_sel),
    ("soil_name", soil_sel),
    (rain_col, band_sel),
]
if HAS_PH_DIM:
    dims.append(("ph_band_label", ph_sel))
for col, sel in dims:
    if len(sel) != 1:
        group_cols.append(col)

PRETTY_COL = {
    "nrm_region": "Region",
    "soil_name": "Soil order",
    "band_label": "Rainfall band",
    "rain_band_label": "Rainfall band",
    "ph_band_label": "pH band",
}


def _render_nested(df, group_cols, parent_total):
    """Recursively render expandable groups; leaf level shows a flat table."""
    col = group_cols[0]
    rest = group_cols[1:]

    totals = (
        df.groupby(col, sort=False)["hectares"]
        .sum()
        .sort_values(ascending=False)
    )
    totals = totals[totals > 0]

    if not rest:
        # Leaf — render as a styled table
        out = totals.reset_index()
        out["pct"] = out["hectares"] / parent_total * 100
        out.columns = [PRETTY_COL.get(col, col), "Hectares", "% of parent"]
        if col == "soil_name":
            out.iloc[:, 0] = out.iloc[:, 0].apply(soil_label)
        out["Hectares"] = out["Hectares"].apply(lambda x: f"{int(round(x)):,}")
        out["% of parent"] = out["% of parent"].apply(lambda x: f"{x:.1f}%")
        st.dataframe(out, use_container_width=True, hide_index=True)
        return

    for value, sub_total in totals.items():
        pct = sub_total / parent_total * 100 if parent_total > 0 else 0
        display = soil_label(value) if col == "soil_name" else value
        label = (
            f"**{display}** — {int(round(sub_total)):,} ha ({pct:.1f}%)"
        )
        with st.expander(label):
            sub = df[df[col] == value]
            _render_nested(sub, rest, sub_total)


if not group_cols:
    st.write("Single combination selected — see total above.")
else:
    extras = []
    if rain_col in group_cols:
        extras.append(rain_idx_col)
    if "ph_band_label" in group_cols and HAS_PH_DIM:
        extras.append("ph_band_idx")
    extra_sel = (", " + ", ".join(extras)) if extras else ""

    df = con.execute(
        f"""SELECT {", ".join(group_cols)}{extra_sel},
                   ROUND(SUM(hectares))::BIGINT AS hectares
            FROM {fact_table} {where}
            GROUP BY {", ".join(group_cols)}{extra_sel}
            HAVING SUM(hectares) > 0
            ORDER BY hectares DESC""",
        params,
    ).fetchdf()
    for c in extras:
        if c in df.columns:
            df = df.drop(columns=[c])

    if len(group_cols) == 1:
        out = df.copy()
        out["pct"] = out["hectares"] / total * 100
        out.columns = [PRETTY_COL.get(c, c) for c in out.columns[:-2]] + \
            ["Hectares", "% of total"]
        if group_cols[0] == "soil_name":
            out.iloc[:, 0] = out.iloc[:, 0].apply(soil_label)
        out["Hectares"] = out["Hectares"].apply(lambda x: f"{int(round(x)):,}")
        out["% of total"] = out["% of total"].apply(lambda x: f"{x:.1f}%")
        st.dataframe(out, use_container_width=True, hide_index=True)
    else:
        st.caption(
            "Click a row to drill down. % shown is share of the parent group."
        )
        _render_nested(df, group_cols, total)

# --- Sources & acknowledgements ---
st.divider()
with st.expander("Sources & acknowledgements"):
    st.markdown("""
**Scope**

21 NRM cattle regions north of Dubbo, NSW (~32.25°S) — all 14 Queensland NRMs,
the entire Northern Territory, the WA Rangelands region (caveat: extends well
south of Dubbo), and 5 northern NSW NRMs (North Coast, Northern Tablelands,
North West NSW, Western, Central West) whose centroid is at or north of Dubbo.

**Spatial data layers**

| Layer | Source | Resolution | Vintage | Licence |
|---|---|---|---|---|
| Soil order (ASC) | [TERN/CSIRO SLGA v1 — Australian Soil Classification Map (Searle 2021)](https://doi.org/10.25919/vkjn-3013) | 90 m (3 arc-sec) | Modelled, multi-vintage | CC-BY 4.0 |
| Mean annual rainfall | [SILO — Queensland Government / BoM gridded climate data](https://www.longpaddock.qld.gov.au/silo/) | 0.05° (~5.5 km) | 1991–2020 climatology | CC-BY 4.0 |
| Soil pH (water, 0–30 cm) | [ISRIC SoilGrids 2.0 — `phh2o`, depth-weighted aggregate](https://www.isric.org/explore/soilgrids) | 250 m → resampled ~500 m | 2020 release | CC-BY 4.0 |
| Land use (NLUM Primary) | [ABARES — Catchment Scale Land Use of Australia (Update Dec 2020)](https://www.agriculture.gov.au/abares/aclump/catchment-scale-land-use-of-australia-update-december-2020) | 50 m → resampled ~220 m | Dec 2020 release | CC-BY 4.0 |
| NRM region polygons | [Australian Government DCCEEW — NRM Regions 2020](https://data.gov.au/dataset/ds-dga-nrm-regions-2020) | Vector | 2020 | CC-BY 4.0 |

**Software & libraries**

[DuckDB](https://duckdb.org/) (analytical SQL engine) · [Streamlit](https://streamlit.io/) (web UI) · [Folium](https://python-visualization.github.io/folium/) / [Leaflet](https://leafletjs.com/) (interactive map) · [rasterio](https://rasterio.readthedocs.io/) + [shapely](https://shapely.readthedocs.io/) + [pandas](https://pandas.pydata.org/) (geospatial processing) · [CartoDB Positron](https://carto.com/basemaps) basemap tiles · OpenStreetMap contributors.

**Acknowledgements**

Thanks to:
- **TERN** (Terrestrial Ecosystem Research Network) and **CSIRO** for hosting and curating the SLGA national soil products
- **ISRIC — World Soil Information** for the SoilGrids 2.0 global product
- **Queensland Government — Long Paddock / SILO** and the **Australian Bureau of Meteorology** for the gridded rainfall climatology
- **DCCEEW** (Dept of Climate Change, Energy, the Environment and Water) for the NRM Regions 2020 boundaries
- **ABARES** (Australian Bureau of Agricultural and Resource Economics and Sciences) for the NLUM land-use raster
- **Open-source geospatial community** — DuckDB, Streamlit, Folium/Leaflet, rasterio, shapely, pandas, GDAL

**Caveats** — see [README](https://github.com/) for full list. Most relevant: SLGA pixel-level accuracy ~61% (highest in mapped cropping/grazing zones); SoilGrids pH is a global model with limited Australian station density; SILO 1991–2020 has thinner station coverage in the WA/NT pastoral interior; NLUM is mapped at catchment scale (50 m source, resampled to ~220 m for the app) so park edges are approximate, and the 2020 release pre-dates some recent IPA declarations.
    """)
