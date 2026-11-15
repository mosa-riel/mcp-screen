"""Cards: free-form screens the agent composes from TRMNL Framework building blocks.

A card is JSON: {"title", "instance", "icon", "align", "body": node | [nodes]}. Nodes are
containers (columns, stack, row, grid) or elements (value, title, label, description, text,
item, table, progress, divider, icon, image, chart, map, compass). This module turns a card
into TRMNL Framework markup (https://github.com/usetrmnl/trmnl-framework, MIT) inside
`.environment.trmnl > .screen.screen--og.screen--1bit`, so the framework's 800x480 1-bit
design does the styling. All text is escaped; the agent never writes HTML here.

Elements can show live Home Assistant values (`entity_id`); `watched()` lists them so the
server can redraw when they change. Charts are Vega-Lite (rendered to SVG in the page),
optionally fed with HA history; maps and images are prepared server-side (maps.py) and
embedded as 1-bit PNGs.

CATALOG is what `screen_components` returns to the agent: keep it in step with the code.
"""

from __future__ import annotations

import base64
import html
import json
import math
from typing import Any, Callable

from widgets import default_icon, fmt_state, name_of

VALUE_SIZES = ["xxsmall", "xsmall", "small", "base", "large", "xlarge", "xxlarge", "xxxlarge", "mega", "giga"]
TEXT_SIZES = ["small", "base", "large", "xlarge", "xxlarge"]
GAPS = ["xsmall", "small", "medium", "large", "xlarge"]
LABEL_STYLES = ["underline", "outline", "inverted", "filled", "gray"]

CATALOG = """\
KAART (show_card) = JSON-object:
  {"title": "kop in de titelbalk onderaan", "instance": "klein rechts in de titelbalk (bv. 'morgen')",
   "icon": "mdi-icoonnaam zonder mdi- (optioneel)", "align": "top" | "center",
   "body": blok of lijst van blokken}
Scherm: 800x480, 1-bit zwart/wit, leesbaar op 2-3 meter. Eén hoofdboodschap, groot. Max ~40 woorden.

CONTAINERS (children = lijst van blokken):
  {"type":"columns","children":[...]}            naast elkaar, elk kind een kolom (2-3 kolommen)
  {"type":"stack","children":[...],"gap":"small|medium|large","align":"left|center|right"}  onder elkaar
  {"type":"row","children":[...],"gap":"..."}     naast elkaar op één regel
  {"type":"grid","cols":2-6,"children":[...],"boxed":true|false}  raster van gelijke vakken

ELEMENTEN (tekst of live HA-waarde via "entity_id"):
  {"type":"value","text":"57%"|"entity_id":"sensor.x","size":"xxsmall..giga","fit":true}
      groot getal/woord; size: xxsmall 16px, xsmall 20, small 26, base 38, large 58, xlarge 74,
      xxlarge 96, xxxlarge 128, mega 170, giga 220. fit = krimpt tot het past.
  {"type":"title","text":"...","size":"small|base|large|xlarge|xxlarge"}
  {"type":"label","text":"...","size":"small|base|large","style":"underline|outline|inverted|filled|gray"}
  {"type":"description","text":"...","size":"base|large|xlarge|xxlarge"}   lopende uitleg, klein
  {"type":"text","text":"alinea 1\\n\\nalinea 2","align":"left|center"}       langere tekst
  {"type":"item","title":"Wind","value":"WZW 27 km/u"|"entity_id":"...","description":"...",
      "index":1,"icon":"weather-windy","emphasis":1-3}   regel in een lijst (stappen: index)
  {"type":"table","headers":["Tijd","Temp"],"rows":[["9:00","8°"],["12:00","11°"]],"size":"small|base|large"}
  {"type":"progress","label":"Accu","value":0-100|"entity_id":"...","text":"72%"}
  {"type":"divider"}
  {"type":"icon","name":"weather-windy","size":24-200}
  {"type":"image","url":"https://...","height":200-400}     foto, wordt zwart-wit gerasterd
  {"type":"chart","height":120-360, "spec":{Vega-Lite zonder data/width} en/of
      "entity_ids":["sensor.x"],"hours":24}
      Vega-Lite (vega.github.io/vega-lite): mark line|bar|area|point|rule|text|arc, encoding x/y/...
      Met entity_ids levert de add-on HA-geschiedenis als data "ha" met velden t (tijd), v (waarde),
      naam. Zonder spec: een lijngrafiek daarvan. Alles in zwart; onderscheid reeksen met
      strokeDash of shape, niet met kleur.
  {"type":"map","height":200-440,"zoom":8-16,
      "center":{"lat":52.3,"lon":6.75} en/of "markers":[{"lat":..,"lon":..,"label":".."}],
      "path":[[lat,lon],...]}       kaart (Nederland: PDOK, elders: OpenStreetMap); zonder zoom past hij zich aan
  {"type":"map","height":300,"route":{"van":"thuis","naar":"Sittard","vervoer":"fiets|auto|lopen"}}
      kaart met de gereden route (OpenStreetMap-routeplanner) en begin/eind gemarkeerd
  {"type":"compass","height":160-400,
      "arrows":[{"deg":190,"label":"route"},{"deg":67,"label":"wind","dashed":true}]}
      kompasroos met pijlen; deg = richting waarheen de pijl wijst (0 = noord, 90 = oost).
      Wind UIT het westen (270) waait NAAR het oosten: deg 90.

ICONEN: Material Design Icons-namen (pictogrammenset van materialdesignicons.com), bv.
  weather-sunny, weather-rainy, weather-windy, bike, car, home, thermometer, water-percent,
  flash, fire, solar-power-variant, calendar, clock-outline, information-outline, check, alert.

ONTWERPREGELS: begin met het antwoord groot (value of title), daarna hooguit 3-5 feiten (item
of table). Gebruik columns voor antwoord links + feiten rechts. Geen kleur, geen emoji.
Labels kort. Getallen met eenheid. Teksten in het Nederlands.

VOORBEELD (antwoord + feiten):
{"title":"Fietsen naar Sittard","instance":"morgen","icon":"bike","body":{"type":"columns","children":[
 {"type":"stack","children":[{"type":"label","text":"Antwoord","size":"small"},
   {"type":"value","text":"Zijwind","size":"xlarge"},
   {"type":"description","text":"Je fietst naar het zuiden, de wind komt uit het west-zuidwesten.","size":"large"}]},
 {"type":"compass","height":300,"arrows":[{"deg":190,"label":"route"},{"deg":67,"label":"wind","dashed":true}]}]}}

VOORBEELD (getal):
{"title":"Luchtvochtigheid","instance":"huiskamer","align":"center","body":{"type":"stack","align":"center","children":[
 {"type":"value","entity_id":"sensor.huiskamer_huiskamer_e_paperscherm_luchtvochtigheid","size":"giga"},
 {"type":"description","text":"Tussen 40 en 60% is gezond.","size":"large"}]}}
"""


def esc(s: Any) -> str:
    return html.escape(str(s))


def _pick(value: Any, allowed: list[str], default: str) -> str:
    return value if value in allowed else default


def _clamp(v: Any, lo: float, hi: float, default: float) -> float:
    try:
        return max(lo, min(hi, float(v)))
    except (TypeError, ValueError):
        return default


def parse_card(card: Any) -> dict:
    """Accept a dict or a JSON string (some models send objects as strings)."""
    if isinstance(card, str):
        card = json.loads(card)
    if not isinstance(card, dict):
        raise ValueError("card moet een JSON-object zijn")
    if "body" not in card:
        raise ValueError("card heeft geen body")
    return card


def _nodes(body: Any) -> list[dict]:
    if isinstance(body, str):
        try:
            body = json.loads(body)
        except ValueError:
            body = {"type": "text", "text": body}
    if isinstance(body, dict):
        return [body]
    if isinstance(body, list):
        return [n if isinstance(n, dict) else {"type": "text", "text": str(n)} for n in body]
    return []


def walk(card: dict):
    """Every node in the card, depth first."""
    stack = list(_nodes(card.get("body")))
    while stack:
        n = stack.pop(0)
        yield n
        stack[0:0] = _nodes(n.get("children"))


def watched(card: dict) -> set[str]:
    out = set()
    for n in walk(card):
        if n.get("entity_id"):
            out.add(n["entity_id"])
        for e in n.get("entity_ids") or []:
            out.add(e)
    return out


def needs_history(card: dict) -> list[tuple[list[str], float]]:
    return [(list(n["entity_ids"]), _clamp(n.get("hours", 24), 1, 24 * 14, 24))
            for n in walk(card) if n.get("type") == "chart" and n.get("entity_ids")]


class Builder:
    """Card → HTML. `states` are HA states; `assets` resolves prepared images/maps/history."""

    def __init__(self, states: dict[str, dict], assets: dict[str, Any], icon_svg: Callable[[str], str]) -> None:
        self.states = states
        self.assets = assets  # id(node) → data URI (image/map) or list of rows (chart history)
        self.icon_svg = icon_svg
        self.charts: list[tuple[str, dict]] = []
        self.unknown: list[str] = []
        self.problems: list[str] = []

    # values

    def _val(self, n: dict, key: str = "text") -> str:
        if n.get("entity_id"):
            st = self.states.get(n["entity_id"])
            if st is None:
                self.unknown.append(n["entity_id"])
            v, unit = fmt_state(st)
            if not unit:
                return v
            return f"{v}{unit}" if unit in ("°", "%") else f"{v} {unit}"
        return str(n.get(key, n.get("value", "")))

    # nodes

    def node(self, n: dict) -> str:
        t = n.get("type")
        fn = getattr(self, f"n_{t}", None)
        if fn is None:
            self.problems.append(f"onbekend blok '{t}'")
            return ""
        try:
            return fn(n)
        except Exception as err:  # noqa: BLE001 - one bad block must not kill the card
            self.problems.append(f"{t}: {err}")
            return ""

    def kids(self, n: dict) -> list[str]:
        return [self.node(c) for c in _nodes(n.get("children"))]

    def n_columns(self, n: dict) -> str:
        cols = "".join(f'<div class="column">{k}</div>' for k in self.kids(n))
        return f'<div class="columns">{cols}</div>'

    def n_stack(self, n: dict) -> str:
        gap = _pick(n.get("gap"), GAPS, "small")
        align = {"center": " flex--center-x text--center", "right": " flex--right"}.get(n.get("align"), "")
        return f'<div class="flex flex--col gap--{gap}{align}">{"".join(self.kids(n))}</div>'

    def n_row(self, n: dict) -> str:
        gap = _pick(n.get("gap"), GAPS, "medium")
        return f'<div class="flex flex--row flex--center-y gap--{gap}">{"".join(self.kids(n))}</div>'

    def n_grid(self, n: dict) -> str:
        cols = int(_clamp(n.get("cols", 2), 1, 6, 2))
        box = " p--2 rounded--small outline" if n.get("boxed") else ""
        cells = "".join(f'<div class="col{box}">{k}</div>' for k in self.kids(n))
        return f'<div class="grid grid--cols-{cols} gap--medium">{cells}</div>'

    def n_value(self, n: dict) -> str:
        size = _pick(n.get("size"), VALUE_SIZES, "large")
        fit = ' data-fit-value="true"' if n.get("fit") else ""
        return f'<span class="value value--{size} value--tnums"{fit}>{esc(self._val(n))}</span>'

    def n_title(self, n: dict) -> str:
        return f'<span class="title title--{_pick(n.get("size"), TEXT_SIZES, "base")}">{esc(self._val(n))}</span>'

    def n_label(self, n: dict) -> str:
        style = n.get("style")
        extra = f" label--{style}" if style in LABEL_STYLES else ""
        size = _pick(n.get("size"), ["small", "base", "large", "xlarge"], "base")
        return f'<span class="label label--{size}{extra}">{esc(self._val(n))}</span>'

    def n_description(self, n: dict) -> str:
        return f'<span class="description description--{_pick(n.get("size"), TEXT_SIZES, "base")}">{esc(self._val(n))}</span>'

    def n_text(self, n: dict) -> str:
        align = "center" if n.get("align") == "center" else "left"
        paras = "".join(f"<p>{esc(p.strip())}</p>" for p in str(n.get("text", "")).split("\n\n") if p.strip())
        return (f'<div class="richtext richtext--{align}"><div class="content content--{align} gap text--{align}">'
                f"{paras}</div></div>")

    def n_item(self, n: dict) -> str:
        meta = f'<div class="meta"><span class="index">{esc(n["index"])}</span></div>' if n.get("index") is not None else '<div class="meta"></div>'
        icon = ""
        if n.get("icon"):
            icon = f'<div class="icon">{self.icon_svg(n["icon"], 34)}</div>'
        parts = []
        if n.get("title"):
            parts.append(f'<span class="title title--small">{esc(n["title"])}</span>')
        if n.get("value") is not None or n.get("entity_id"):
            parts.append(f'<span class="value value--small">{esc(self._val(n, "value"))}</span>')
        if n.get("description"):
            parts.append(f'<span class="description">{esc(n["description"])}</span>')
        emph = int(_clamp(n.get("emphasis", 1), 1, 3, 1))
        cls = f" item--emphasis-{emph}" if emph > 1 else ""
        return f'<div class="item{cls}">{meta}{icon}<div class="content">{"".join(parts)}</div></div>'

    def n_table(self, n: dict) -> str:
        size = {"small": " table--small", "large": " table--large"}.get(n.get("size"), "")
        head = "".join(f'<th><span class="title title--small">{esc(h)}</span></th>' for h in n.get("headers") or [])
        rows = "".join("<tr>" + "".join(f'<td><span class="label">{esc(c)}</span></td>' for c in r) + "</tr>"
                       for r in (n.get("rows") or []) if isinstance(r, list))
        return f'<table class="table{size}">{"<thead><tr>" + head + "</tr></thead>" if head else ""}<tbody>{rows}</tbody></table>'

    def n_progress(self, n: dict) -> str:
        if n.get("entity_id"):
            st = self.states.get(n["entity_id"]) or {}
            try:
                pct = float(st.get("state"))
            except (TypeError, ValueError):
                pct = 0.0
        else:
            pct = _clamp(n.get("value", 0), 0, 100, 0)
        pct = max(0.0, min(100.0, pct))
        text = n.get("text") or f"{round(pct)}%"
        return (f'<div class="progress-bar progress-bar--base w--full" style="min-width:300px"><div class="content">'
                f'<span class="label">{esc(n.get("label", ""))}</span><span class="label">{esc(text)}</span></div>'
                f'<div class="track"><div class="fill" style="width:{pct:.0f}%"></div></div></div>')

    def n_divider(self, n: dict) -> str:
        return '<div class="divider"></div>'

    def n_icon(self, n: dict) -> str:
        return self.icon_svg(n.get("name", "information-outline"), int(_clamp(n.get("size", 64), 16, 240, 64)))

    def _img(self, n: dict, what: str) -> str:
        uri = self.assets.get(id(n))
        if not uri:
            self.problems.append(f"{what} niet beschikbaar")
            return ""
        h = int(_clamp(n.get("height", 300), 60, 440, 300))
        return f'<img src="{uri}" style="height:{h}px;max-width:100%;display:block;image-rendering:pixelated">'

    def n_image(self, n: dict) -> str:
        return self._img(n, "afbeelding")

    def n_map(self, n: dict) -> str:
        return self._img(n, "kaart")

    def n_chart(self, n: dict) -> str:
        h = int(_clamp(n.get("height", 220), 80, 420, 220))
        spec = n.get("spec")
        if isinstance(spec, str):
            spec = json.loads(spec)
        rows = self.assets.get(id(n))
        if not isinstance(spec, dict):
            if rows is None:
                self.problems.append("grafiek zonder spec en zonder entity_ids")
                return ""
            spec = {"mark": {"type": "line", "interpolate": "step-after"},
                    "encoding": {"x": {"field": "t", "type": "temporal", "title": None, "axis": {"format": "%H:%M"}},
                                 "y": {"field": "v", "type": "quantitative", "title": None, "scale": {"zero": False}},
                                 "strokeDash": {"field": "naam", "type": "nominal", "title": None}}}
        spec = dict(spec)
        if rows is not None:
            spec["datasets"] = {**spec.get("datasets", {}), "ha": rows}
            if "data" not in spec or spec.get("data", {}).get("name") == "ha":
                spec["data"] = {"name": "ha"}
        spec.update({"$schema": "https://vega.github.io/schema/vega-lite/v5.json", "width": "container", "height": h - 10,
                     "autosize": {"type": "fit", "contains": "padding"}, "background": None,
                     "config": _VEGA_CONFIG | spec.get("config", {})})
        cid = f"chart{len(self.charts)}"
        self.charts.append((cid, spec))
        return f'<div id="{cid}" style="width:100%;height:{h}px"></div>'

    def n_compass(self, n: dict) -> str:
        h = int(_clamp(n.get("height", 260), 120, 420, 260))
        return compass_svg(n.get("arrows") or [], h)

    # page

    def page(self, card: dict, base: str) -> str:
        body = "".join(self.node(n) for n in _nodes(card.get("body")))
        align = "layout--center" if card.get("align") == "center" else "layout--top layout--left layout--stretch-x"
        bar = ""
        if card.get("title") or card.get("instance"):
            icon = self.icon_svg(card["icon"], 26) if card.get("icon") else ""
            bar = (f'<div class="title_bar">{icon}<span class="title">{esc(card.get("title", ""))}</span>'
                   + (f'<span class="instance">{esc(card["instance"])}</span>' if card.get("instance") else "") + "</div>")
        charts = ""
        if self.charts:
            specs = json.dumps({cid: spec for cid, spec in self.charts})
            charts = (f'<script src="{base}/vendor/vega.min.js"></script><script src="{base}/vendor/vega-lite.min.js"></script>'
                      f'<script src="{base}/vendor/vega-embed.min.js"></script><script>window.__cardDone=false;'
                      f'const S={specs};Promise.all(Object.entries(S).map(([id,s])=>vegaEmbed("#"+id,s,'
                      f'{{renderer:"svg",actions:false}}))).catch(e=>console.error(e)).finally(()=>{{window.__cardDone=true}});</script>')
        return (f'<!doctype html><html><head><meta charset="utf-8">{framework_head(base)}'
                f'<style>.trmnl .icon-svg{{display:inline-block;vertical-align:middle}}</style></head>'
                f'<body class="environment trmnl" style="margin:0"><div class="screen screen--og screen--1bit">'
                f'<div class="view view--full"><div class="layout layout--col {align} gap--medium">{body}</div>{bar}</div>'
                f'</div>{charts}</body></html>')


_VEGA_CONFIG = {
    "font": "Inter Variable", "background": None,
    "view": {"stroke": None},
    "axis": {"labelFontSize": 15, "titleFontSize": 15, "labelColor": "#000", "titleColor": "#000",
             "domainColor": "#000", "tickColor": "#000", "gridColor": "#000", "gridDash": [2, 5], "gridOpacity": 1,
             "labelFontWeight": 600},
    "legend": {"labelFontSize": 15, "titleFontSize": 15, "labelColor": "#000", "titleColor": "#000",
               "symbolStrokeColor": "#000", "symbolFillColor": "#000", "orient": "top"},
    "mark": {"color": "#000"},
    "line": {"strokeWidth": 3, "color": "#000"},
    "bar": {"color": "#000"},
    "area": {"color": "#000", "opacity": 1},
    "point": {"color": "#000", "filled": True, "size": 60},
    "rule": {"color": "#000", "strokeWidth": 2},
    "text": {"color": "#000", "fontSize": 15, "fontWeight": 600},
    "range": {"category": ["#000"], "ordinal": ["#000"], "ramp": ["#fff", "#000"]},
    "title": {"fontSize": 18, "color": "#000"},
}


def framework_head(base: str) -> str:
    """Links for the TRMNL Framework (CSS + runtime) and MDI icons, served by this add-on."""
    return (f'<link rel="stylesheet" href="{base}/trmnl/plugins.min.css">'
            f'<link rel="stylesheet" href="{base}/assets/mdi/css/materialdesignicons.min.css">'
            f'<script src="{base}/trmnl/plugins.min.js"></script>')


def compass_svg(arrows: list[dict], h: int) -> str:
    """A compass rose (N/O/Z/W) with arrows from the centre; deg = direction it points to."""
    w = h
    c, r = w / 2, h / 2 - 26
    parts = [f'<circle cx="{c}" cy="{c}" r="{r}" fill="none" stroke="#000" stroke-width="3"/>']
    for deg, lab in ((0, "N"), (90, "O"), (180, "Z"), (270, "W")):
        a = math.radians(deg)
        x, y = c + (r + 14) * math.sin(a), c - (r + 14) * math.cos(a)
        parts.append(f'<text x="{x:.1f}" y="{y + 6:.1f}" text-anchor="middle" font-size="18" font-weight="700" '
                     f'font-family="Inter Variable, Inter">{lab}</text>')
        x1, y1 = c + (r - 10) * math.sin(a), c - (r - 10) * math.cos(a)
        x2, y2 = c + r * math.sin(a), c - r * math.cos(a)
        parts.append(f'<line x1="{x1:.1f}" y1="{y1:.1f}" x2="{x2:.1f}" y2="{y2:.1f}" stroke="#000" stroke-width="3"/>')
    for i, arrow in enumerate(arrows[:3]):
        deg = _clamp(arrow.get("deg", 0), -720, 720, 0)
        a = math.radians(deg)
        tip = r - 18
        tx, ty = c + tip * math.sin(a), c - tip * math.cos(a)
        bx, by = c - tip * 0.55 * math.sin(a), c + tip * 0.55 * math.cos(a)
        dash = ' stroke-dasharray="10 7"' if arrow.get("dashed") else ""
        width = 7 if not arrow.get("dashed") else 5
        parts.append(f'<line x1="{bx:.1f}" y1="{by:.1f}" x2="{tx:.1f}" y2="{ty:.1f}" stroke="#000" stroke-width="{width}"{dash} '
                     f'stroke-linecap="round"/>')
        head = 18
        for side in (-1, 1):
            ha = a + math.pi + side * 0.45
            parts.append(f'<line x1="{tx:.1f}" y1="{ty:.1f}" x2="{tx + head * math.sin(ha):.1f}" '
                         f'y2="{ty - head * math.cos(ha):.1f}" stroke="#000" stroke-width="{width}" stroke-linecap="round"/>')
        if arrow.get("label"):
            lx, ly = c + (tip * 0.62) * math.sin(a) + 14 * math.cos(a), c - (tip * 0.62) * math.cos(a) + 14 * math.sin(a)
            parts.append(f'<rect x="{lx - 4 - 5 * len(arrow["label"]):.1f}" y="{ly - 15:.1f}" width="{10 * len(arrow["label"]) + 8}" '
                         f'height="22" fill="#fff"/><text x="{lx:.1f}" y="{ly + 2:.1f}" text-anchor="middle" font-size="16" '
                         f'font-weight="700" font-family="Inter Variable, Inter">{esc(arrow["label"])}</text>')
    return f'<svg width="{w}" height="{h}" viewBox="0 0 {w} {h}">{"".join(parts)}</svg>'


def data_uri_png(png: bytes) -> str:
    return "data:image/png;base64," + base64.b64encode(png).decode()


def history_rows(raw: list[list[dict]], states: dict[str, dict]) -> list[dict]:
    """HA /history response → Vega rows {t, v, naam} (numeric states only)."""
    rows = []
    for series in raw:
        if not series:
            continue
        eid = series[0].get("entity_id")
        naam = name_of(states.get(eid), eid)
        for p in series:
            try:
                v = float(p.get("state"))
            except (TypeError, ValueError):
                continue
            rows.append({"t": p.get("last_changed") or p.get("last_updated"), "v": v, "naam": naam})
    return rows


__all__ = ["CATALOG", "Builder", "parse_card", "walk", "watched", "needs_history", "framework_head",
           "data_uri_png", "history_rows", "default_icon"]
