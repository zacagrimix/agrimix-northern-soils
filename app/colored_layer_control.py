"""Folium MacroElement that adds a Leaflet control with coloured swatches.

Replaces folium.LayerControl when we need a colour swatch next to each
toggleable overlay name. Designed to work inside streamlit-folium, which
renames the map JS variable to `map_div` and strips the `name` option from
ImageOverlays. We therefore (a) locate the map dynamically and (b) match
overlays to labels positionally (in add order, filtered by data: URL).
"""
import json

from branca.element import MacroElement
from jinja2 import Template


class ColoredLayerControl(MacroElement):
    _template = Template("""
        {% macro script(this, kwargs) %}
            (function() {
                function wireControl() {
                    var w = window;
                    var map = w.map_div;
                    if (!map || typeof map.eachLayer !== 'function') {
                        var names = Object.getOwnPropertyNames(w);
                        for (var i = 0; i < names.length; i++) {
                            try {
                                var v = w[names[i]];
                                if (
                                    v && typeof v === 'object'
                                    && typeof v.eachLayer === 'function'
                                    && typeof v.fitBounds === 'function'
                                    && typeof v.removeLayer === 'function'
                                ) { map = v; break; }
                            } catch(e) {}
                        }
                    }
                    if (!map) {
                        return setTimeout(wireControl, 200);
                    }

                    var LABELS = {{ this.labels_json }};
                    var COLORS = {{ this.colors_json }};

                    var overlays = [];
                    map.eachLayer(function(l) {
                        if (
                            l._url && typeof l._url === 'string'
                            && l._url.indexOf('data:image') === 0
                        ) {
                            overlays.push(l);
                        }
                    });

                    if (overlays.length === 0) {
                        return setTimeout(wireControl, 200);
                    }

                    var ctrl = L.control({position: '{{ this.position }}'});
                    ctrl.onAdd = function(m) {
                        var div = L.DomUtil.create(
                            'div', 'leaflet-bar leaflet-control'
                        );
                        div.style.background = 'rgba(255,255,255,0.97)';
                        div.style.color = '#111';
                        div.style.padding = '12px 16px 10px 14px';
                        div.style.fontSize = '16px';
                        div.style.lineHeight = '1.9';
                        div.style.fontFamily =
                            '-apple-system, BlinkMacSystemFont, sans-serif';
                        div.style.border = '1px solid #555';
                        div.style.borderRadius = '6px';
                        div.style.boxShadow = '0 2px 8px rgba(0,0,0,0.3)';
                        div.style.minWidth = '170px';

                        var n = Math.min(overlays.length, LABELS.length);
                        for (var i = 0; i < n; i++) {
                            (function(label, color, layer) {
                                var row = L.DomUtil.create('label', '', div);
                                row.style.cssText =
                                    'display:block; cursor:pointer; ' +
                                    'white-space:nowrap; user-select:none;';
                                var cb = L.DomUtil.create('input', '', row);
                                cb.type = 'checkbox';
                                cb.checked = true;
                                cb.style.cssText =
                                    'margin-right:10px;vertical-align:middle;' +
                                    'width:18px;height:18px;cursor:pointer;';
                                cb.onchange = function() {
                                    if (cb.checked) map.addLayer(layer);
                                    else map.removeLayer(layer);
                                };
                                var sw = L.DomUtil.create('span', '', row);
                                sw.style.cssText =
                                    'display:inline-block;width:18px;' +
                                    'height:18px;background:' + color + ';' +
                                    'border:1px solid #555;' +
                                    'vertical-align:middle;margin-right:8px;';
                                var nm = L.DomUtil.create('span', '', row);
                                nm.textContent = label;
                                nm.style.verticalAlign = 'middle';
                                nm.style.fontSize = '15px';
                                nm.style.color = '#111';
                            })(LABELS[i], COLORS[i], overlays[i]);
                        }

                        L.DomEvent.disableClickPropagation(div);
                        L.DomEvent.disableScrollPropagation(div);
                        return div;
                    };
                    ctrl.addTo(map);
                }
                wireControl();
            })();
        {% endmacro %}
    """)

    def __init__(
        self,
        labels: list[str],
        colors: list[str],
        position: str = "bottomright",
    ):
        super().__init__()
        self._name = "ColoredLayerControl"
        self.labels_json = json.dumps(labels)
        self.colors_json = json.dumps(colors)
        self.position = position
