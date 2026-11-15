"""Dashboard building blocks for the 800x480 e-paper.

A page is {"title": str, "widgets": [{"type": ..., ...}, ...]}. Widgets are placed in order
on a 4x3 grid (cells 200x140 under a 60 px header), first fit, row by row; what does not
fit is left out and reported. Each widget declares the data it needs (entities, forecasts,
todo lists), so one render does at most one /states call plus the forecasts/todo calls
actually used, and the server knows which entities to watch for live updates.
"""

from __future__ import annotations

import html
import json
from dataclasses import dataclass, field
from datetime import datetime
from zoneinfo import ZoneInfo

TZ = ZoneInfo("Europe/Amsterdam")
COLS, ROWS = 4, 3

ICON = {"sunny": "weather-sunny", "clear-night": "weather-night", "partlycloudy": "weather-partly-cloudy",
        "cloudy": "weather-cloudy", "rainy": "weather-rainy", "pouring": "weather-pouring",
        "snowy": "weather-snowy", "snowy-rainy": "weather-snowy-rainy", "fog": "weather-fog",
        "hail": "weather-hail", "lightning": "weather-lightning", "lightning-rainy": "weather-lightning-rainy",
        "windy": "weather-windy", "windy-variant": "weather-windy-variant", "exceptional": "alert-circle-outline"}
LABEL = {"sunny": "Zonnig", "clear-night": "Helder", "partlycloudy": "Half bewolkt", "cloudy": "Bewolkt",
         "rainy": "Regen", "pouring": "Zware regen", "snowy": "Sneeuw", "snowy-rainy": "Natte sneeuw",
         "fog": "Mist", "hail": "Hagel", "lightning": "Onweer", "lightning-rainy": "Onweer en regen",
         "windy": "Winderig", "windy-variant": "Winderig", "exceptional": "Uitzonderlijk"}
DAYS = ["ma", "di", "wo", "do", "vr", "za", "zo"]
DAYS_LONG = ["Maandag", "Dinsdag", "Woensdag", "Donderdag", "Vrijdag", "Zaterdag", "Zondag"]
MONTHS = ["januari", "februari", "maart", "april", "mei", "juni", "juli", "augustus", "september",
          "oktober", "november", "december"]
WIND = ["N", "NNO", "NO", "ONO", "O", "OZO", "ZO", "ZZO", "Z", "ZZW", "ZW", "WZW", "W", "WNW", "NW", "NNW"]
STATE_NL = {"on": "aan", "off": "uit", "open": "open", "closed": "dicht", "opening": "gaat open",
            "closing": "gaat dicht", "home": "thuis", "not_home": "weg", "unavailable": "n.v.t.",
            "unknown": "?", "locked": "op slot", "unlocked": "open", "heat": "verwarmen", "idle": "rust"}
CLASS_ICON = {"temperature": "thermometer", "humidity": "water-percent", "power": "flash",
              "energy": "lightning-bolt", "gas": "fire", "battery": "battery", "monetary": "currency-eur",
              "illuminance": "brightness-5", "pressure": "gauge", "window": "window-closed-variant",
              "door": "door", "motion": "motion-sensor", "opening": "door"}
DOMAIN_ICON = {"light": "lightbulb", "switch": "power-socket-eu", "cover": "window-shutter",
               "climate": "thermostat", "fan": "fan", "media_player": "speaker", "vacuum": "robot-vacuum",
               "lock": "lock", "person": "account", "todo": "format-list-checks", "weather": "weather-partly-cloudy"}

# type → (cols, rows); some types grow with an option (see _size).
SIZES = {"weather_now": (2, 1), "forecast_hours": (2, 1), "forecast_days": (2, 2), "value": (1, 1),
         "list": (2, 1), "rooms": (2, 2), "todo": (2, 2), "text": (2, 1)}


def esc(s) -> str:
    return html.escape(str(s))


def nl_num(x: float, decimals: int) -> str:
    return f"{x:.{decimals}f}".replace(".", ",")


def local(ts: str) -> datetime:
    return datetime.fromisoformat(ts).astimezone(TZ)


def icon(name: str, cls: str = "") -> str:
    name = (name or "help").removeprefix("mdi:").removeprefix("mdi-")
    return f'<i class="mdi mdi-{esc(name)} {cls}"></i>'


def long_date(now: datetime) -> str:
    return f"{DAYS_LONG[now.weekday()]} {now.day} {MONTHS[now.month - 1]}"


def is_numeric(state: str) -> bool:
    try:
        float(state)
        return True
    except (TypeError, ValueError):
        return False


def fmt_state(st: dict | None) -> tuple[str, str]:
    """(value, unit) as shown on screen for one entity state."""
    if st is None:
        return "?", ""
    s, a = st["state"], st.get("attributes", {})
    domain = st["entity_id"].split(".")[0]
    unit = a.get("unit_of_measurement") or ""
    if domain == "light" and s == "on" and a.get("brightness") is not None:
        return f"{round(a['brightness'] / 2.55)}", "%"
    if domain == "cover" and a.get("current_position") is not None and s not in ("unavailable", "unknown"):
        return f"{a['current_position']}", "%"
    if domain == "weather" and a.get("temperature") is not None:
        return f"{round(a['temperature'])}", "°"
    if domain == "climate" and a.get("current_temperature") is not None:
        return nl_num(float(a["current_temperature"]), 1), "°"
    if is_numeric(s):
        x = float(s)
        if unit in ("EUR", "€"):
            return f"€ {nl_num(x, 2)}", ""
        if "€" in unit:
            return nl_num(x, 2), unit
        dec = 0 if abs(x) >= 100 or x == int(x) else 1
        return nl_num(x, dec), "°" if unit in ("°C", "°F") else unit
    return STATE_NL.get(s, s), ""


def default_icon(st: dict | None) -> str:
    if st is None:
        return "help-circle-outline"
    a = st.get("attributes", {})
    if a.get("icon"):
        return a["icon"]
    return CLASS_ICON.get(a.get("device_class", ""), DOMAIN_ICON.get(st["entity_id"].split(".")[0], "information-outline"))


def name_of(st: dict | None, entity_id: str) -> str:
    return (st or {}).get("attributes", {}).get("friendly_name") or entity_id


@dataclass
class Ctx:
    """Data for one render, fetched up front by the server."""
    now: datetime
    states: dict[str, dict]
    weather_entity: str
    forecasts: dict[str, list] = field(default_factory=dict)  # "daily"/"hourly" → list
    todos: dict[str, list] = field(default_factory=dict)  # entity_id → items


def parse_widgets(widgets) -> list[dict]:
    """Accept a list, or a JSON string of one (some models send lists as strings)."""
    if isinstance(widgets, str):
        widgets = json.loads(widgets)
    if not isinstance(widgets, list):
        raise ValueError("widgets moet een lijst zijn")
    out = []
    for w in widgets:
        if isinstance(w, str):
            w = {"type": w}
        if not isinstance(w, dict) or w.get("type") not in SIZES:
            raise ValueError(f"onbekend blok: {w!r}; kies uit {', '.join(SIZES)}")
        out.append(w)
    return out


def _size(w: dict) -> tuple[int, int]:
    c, r = SIZES[w["type"]]
    t = w["type"]
    if t == "weather_now" and w.get("large"):
        return 2, 2
    if t == "list" and len(_ids(w)) > (2 if w.get("title") else 3):
        return 2, 2
    if t == "forecast_hours" and w.get("wide"):
        return 4, 1
    if t in ("text", "todo") and w.get("large"):
        return 4, 3
    if t == "text" and w.get("wide"):
        return 4, 1
    return c, r


def _ids(w: dict) -> list[str]:
    ids = w.get("entity_ids") or []
    if isinstance(ids, str):
        ids = json.loads(ids) if ids.strip().startswith("[") else [i.strip() for i in ids.split(",")]
    return list(ids)


def needs(widgets: list[dict], weather_entity: str, states: dict | None = None) -> dict:
    """Which data the widgets need, and which entities they show (to watch for changes)."""
    need = {"forecast": set(), "todo": set(), "entities": set()}
    for w in widgets:
        t = w["type"]
        if t in ("weather_now", "forecast_hours", "forecast_days"):
            need["entities"].add(w.get("entity_id") or weather_entity)
            if t == "forecast_hours":
                need["forecast"].add("hourly")
            if t == "forecast_days":
                need["forecast"].add("daily")
        elif t == "value":
            need["entities"].add(w["entity_id"])
        elif t == "list":
            need["entities"].update(_ids(w))
        elif t == "rooms":
            ids = _ids(w) or sorted(e for e in (states or {}) if e.startswith("climate."))
            need["entities"].update(ids)
        elif t == "todo":
            eid = w.get("entity_id") or "todo.shopping_list"
            need["todo"].add(eid)
            need["entities"].add(eid)
        elif t == "text" and w.get("entity_id"):
            need["entities"].add(w["entity_id"])
    return need


def place(widgets: list[dict]) -> tuple[list[tuple[dict, int, int, int, int]], list[dict]]:
    """First-fit placement on the grid → ([(widget, col, row, w, h)], left_out)."""
    used = [[False] * COLS for _ in range(ROWS)]
    placed, left = [], []
    for wd in widgets:
        w, h = _size(wd)
        spot = next(((r, c) for r in range(ROWS - h + 1) for c in range(COLS - w + 1)
                     if all(not used[r + i][c + j] for i in range(h) for j in range(w))), None)
        if spot is None:
            left.append(wd)
            continue
        r, c = spot
        for i in range(h):
            for j in range(w):
                used[r + i][c + j] = True
        placed.append((wd, c, r, w, h))
    return placed, left


# ── widget bodies ────────────────────────────────────────────────────────────────

def _weather_now(w: dict, ctx: Ctx, cols: int, rows: int) -> str:
    st = ctx.states.get(w.get("entity_id") or ctx.weather_entity)
    if st is None:
        return '<div class="msg">Geen weerdata</div>'
    a, cond = st["attributes"], st["state"]
    parts = []
    if a.get("wind_speed") is not None:
        wind = WIND[round(a.get("wind_bearing", 0) / 22.5) % 16] if a.get("wind_bearing") is not None else ""
        parts.append(f'{icon("weather-windy")} {round(a["wind_speed"])} km/u {wind}')
    if a.get("humidity") is not None:
        parts.append(f'{icon("water-percent")}{round(a["humidity"])}%')
    sun = ctx.states.get("sun.sun", {}).get("attributes", {})
    if sun.get("next_setting") and sun.get("next_rising"):
        set_, rise = local(sun["next_setting"]), local(sun["next_rising"])
        parts.append(f'{icon("weather-sunset-down")} {set_:%H:%M}' if set_ < rise else f'{icon("weather-sunset-up")} {rise:%H:%M}')
    big = rows > 1
    return (f'<div class="wnow{" big" if big else ""}"><div class="row">{icon(ICON.get(cond, "help"), "wi")}'
            f'<div><div class="temp">{round(a.get("temperature", 0))}°</div>'
            f'<div class="cond">{esc(LABEL.get(cond, cond))}</div></div></div>'
            f'<div class="det">{" &nbsp;".join(parts)}</div></div>')


def _forecast_hours(w: dict, ctx: Ctx, cols: int, rows: int) -> str:
    n = max(1, min(int(w.get("hours", 4)), 2 * cols))
    hours = [h for h in ctx.forecasts.get("hourly", []) if local(h["datetime"]) > ctx.now][:n]
    cells = "".join(
        f'<div class="h"><div class="t">{local(h["datetime"]):%H:%M}</div>{icon(ICON.get(h["condition"], "help"), "hi")}'
        f'<div class="v">{round(h["temperature"])}°</div><div class="s">{nl_num(h.get("precipitation") or 0, 1)} mm</div></div>'
        for h in hours)
    return f'<div class="hours">{cells or "<div class=msg>Geen verwachting</div>"}</div>'


def _forecast_days(w: dict, ctx: Ctx, cols: int, rows: int) -> str:
    n = max(1, min(int(w.get("days", 5)), 5 * rows // 2))
    out = []
    for i, d in enumerate(ctx.forecasts.get("daily", [])[:n]):
        day = "vandaag" if local(d["datetime"]).date() == ctx.now.date() else DAYS[local(d["datetime"]).weekday()]
        low = f' {round(d["templow"])}°' if d.get("templow") is not None else ""
        out.append(f'<div class="d"><span class="dn">{day}</span>{icon(ICON.get(d["condition"], "help"), "di")}'
                   f'<span class="dt"><b>{round(d["temperature"])}°</b>{low}</span>'
                   f'<span class="dr">{nl_num(d.get("precipitation") or 0, 0)} mm</span></div>')
    title = w["title"] if "title" in w else "Komende dagen"
    return (f'<h2>{esc(title)}</h2>' if title else "") + ("".join(out) or '<div class="msg">Geen verwachting</div>')


def _value(w: dict, ctx: Ctx, cols: int, rows: int) -> str:
    st = ctx.states.get(w["entity_id"])
    val, unit = fmt_state(st)
    label = w.get("label") or name_of(st, w["entity_id"])
    unit_html = esc(unit) if unit == "°" else f"<small>{esc(unit)}</small>"
    return (f'<div class="val">{icon(w.get("icon") or default_icon(st), "vi")}<div>'
            f'<b>{esc(val)}{unit_html}</b><span>{esc(label)}</span></div></div>')


def _rows(title: str, rows_html: list[str], max_rows: int) -> str:
    extra = len(rows_html) - max_rows
    if extra > 0:
        rows_html = rows_html[:max_rows - 1] + [f'<div class="li more">+{extra + 1} meer</div>']
    head = f"<h2>{esc(title)}</h2>" if title else ""
    return head + "".join(rows_html)


def _list(w: dict, ctx: Ctx, cols: int, rows: int) -> str:
    items = []
    for eid in _ids(w):
        st = ctx.states.get(eid)
        val, unit = fmt_state(st)
        items.append(f'<div class="li">{icon(default_icon(st), "li-i")}<span class="ln">{esc(name_of(st, eid))}</span>'
                     f'<span class="lv">{esc(val)}{esc(unit)}</span></div>')
    title = w.get("title", "")
    return _rows(title, items, (2 if title else 3) if rows == 1 else (5 if title else 6))


def _rooms(w: dict, ctx: Ctx, cols: int, rows: int) -> str:
    ids = _ids(w) or sorted(e for e in ctx.states if e.startswith("climate."))
    items = []
    for eid in ids:
        st = ctx.states.get(eid)
        a = (st or {}).get("attributes", {})
        cur = a.get("current_temperature")
        if cur is None:
            continue
        sp = a.get("temperature")
        target = f'<small> → {nl_num(float(sp), 1)}°</small>' if sp is not None and st["state"] != "off" else ""
        items.append(f'<div class="li"><span class="ln">{esc(name_of(st, eid))}</span>'
                     f'<span class="lv">{nl_num(float(cur), 1)}°{target}</span></div>')
    return _rows(w["title"] if "title" in w else "Binnen", items, 5 if "title" not in w or w["title"] else 6)


def _todo(w: dict, ctx: Ctx, cols: int, rows: int) -> str:
    eid = w.get("entity_id") or "todo.shopping_list"
    items = [f'<div class="li">{icon("checkbox-blank-outline", "li-i")}<span class="ln">{esc(i.get("summary", ""))}</span></div>'
             for i in ctx.todos.get(eid, [])]
    title = w["title"] if "title" in w else name_of(ctx.states.get(eid), eid)
    if title == "Shopping list":
        title = "Boodschappen"
    return _rows(title, items or ['<div class="msg">Lijst is leeg</div>'], 9 if rows == 3 else 5)


def _text(w: dict, ctx: Ctx, cols: int, rows: int) -> str:
    title = f'<h2>{esc(w["title"])}</h2>' if w.get("title") else ""
    text = w.get("text", "")
    if w.get("entity_id"):  # show an entity's (text) state, e.g. a status sensor
        text = fmt_state(ctx.states.get(w["entity_id"]))[0]
    return f'{title}<div class="txt{" big" if rows == 3 else ""}">{esc(text)}</div>'


BODY = {"weather_now": _weather_now, "forecast_hours": _forecast_hours, "forecast_days": _forecast_days,
        "value": _value, "list": _list, "rooms": _rooms, "todo": _todo, "text": _text}


def page_html(title: str, widgets: list[dict], ctx: Ctx, asset_base: str) -> tuple[str, list[dict]]:
    """Full HTML for a page → (html, widgets left out)."""
    placed, left = place(widgets)
    tiles = "".join(
        f'<div class="tile t-{wd["type"]}" style="grid-column:{c + 1}/span {w};grid-row:{r + 1}/span {h}">'
        f'{BODY[wd["type"]](wd, ctx, w, h)}</div>'
        for wd, c, r, w, h in placed)
    left_title = esc(title) if title else long_date(ctx.now)
    right = (long_date(ctx.now) + " · " if title else "") + f"{ctx.now:%H:%M}"
    doc = (f'<!doctype html><html><head><meta charset="utf-8">'
           f'<link rel="stylesheet" href="{asset_base}/assets/base.css">'
           f'<link rel="stylesheet" href="{asset_base}/assets/dashboard.css"></head><body>'
           f'<div class="top"><span>{left_title}</span><span class="u">{right}</span></div>'
           f'<div class="grid">{tiles}</div></body></html>')
    return doc, left
