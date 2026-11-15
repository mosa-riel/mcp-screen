"""Screen — an MCP server that drives the reTerminal E1001 e-paper.

Two servers in one process:
  1. the **MCP** endpoint (streamable-HTTP, MCP_PORT, default 8788) the agent calls;
  2. an embedded **HTTP host** (SCREEN_PORT, default 8799) the reTerminal fetches from:
     /screen.png (what is on screen), /nav/home|next|prev.png (its buttons), and for the
     headless browser /assets/* (Inter, MDI icons), /trmnl/* (TRMNL Framework CSS + runtime),
     /fonts/* (its fonts), /vendor/* (Vega) and /render/<token>.html (the page being rendered).

Screens are dashboards built from widgets (widgets.py) or cards the agent composes from TRMNL
Framework building blocks (cards.py, maps.py), filled with live Home Assistant data (ha.py),
rendered to 1-bit PNG (render.py). Named pages (default_pages.json plus pages saved
in /data/pages.json) form the rotation the buttons step through. After publishing, the server
calls the device's `refresh_screen` ESPHome service so it fetches and redraws.

Updates: the shown dashboard is re-rendered every `refresh_minutes`, and within seconds when
an entity on it changes (HA websocket), except numeric sensors (power etc.), which change
constantly and wait for the periodic refresh. A screen that is not the home page returns to
it after `home_after_minutes`.

All screen state lives on one worker event loop (its own thread), so the MCP tools (uvicorn's
loop) and the HTTP handler threads hand their work to it.

Env (HA add-on options win, see _load_ha_options): RETERMINAL_HOST, SCREEN_PORT, MCP_PORT,
WEATHER_ENTITY, REFRESH_MINUTES, HOME_AFTER_MINUTES; DATA_DIR (/data); for local development
HA_URL + HA_TOKEN (otherwise the Supervisor API) and SCREEN_NO_DEVICE=1 (never push).
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import os
import re
import threading
import time
from datetime import datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import httpx
from aioesphomeapi import APIClient
from mcp.server.fastmcp import FastMCP
from mcp.server.transport_security import TransportSecuritySettings

import cards
import geo
import maps
import render
import widgets as wg
from ha import HA, ws_endpoint


def _load_ha_options() -> None:
    """When running as a HA add-on, map /data/options.json → env (no-op elsewhere)."""
    try:
        with open("/data/options.json") as f:
            opts = json.load(f)
    except (OSError, ValueError):
        return
    mapping = {"reterminal_host": "RETERMINAL_HOST", "screen_port": "SCREEN_PORT", "mcp_port": "MCP_PORT",
               "weather_entity": "WEATHER_ENTITY", "refresh_minutes": "REFRESH_MINUTES",
               "home_after_minutes": "HOME_AFTER_MINUTES"}
    for key, env in mapping.items():
        if key in opts and opts[key] not in (None, ""):
            os.environ[env] = str(opts[key])  # HA options win over the image's ENV defaults


_load_ha_options()

APP = Path(__file__).parent
DATA = Path(os.getenv("DATA_DIR", "/data"))
RETERMINAL = os.getenv("RETERMINAL_HOST", "reterminal-e1001.local")
PORT = int(os.getenv("SCREEN_PORT", "8799"))
WEATHER = os.getenv("WEATHER_ENTITY", "weather.forecast_home")
REFRESH_S = 60 * float(os.getenv("REFRESH_MINUTES", "15"))
HOME_AFTER_S = 60 * float(os.getenv("HOME_AFTER_MINUTES", "60"))
NO_DEVICE = os.getenv("SCREEN_NO_DEVICE") == "1"
ASSET_BASE = f"http://127.0.0.1:{PORT}"
LIVE_DEBOUNCE_S = 2.0  # gather a burst of changes into one redraw
LIVE_MIN_GAP_S = 8.0  # e-paper needs ~4 s per full refresh; don't queue redraws faster


# ── pages ───────────────────────────────────────────────────────────────────────

def _saved() -> dict:
    try:
        return json.loads((DATA / "pages.json").read_text())
    except (OSError, ValueError):
        return {}


def pages() -> dict[str, dict]:
    """Built-in pages in their order, then saved ones; a saved page overrides a built-in."""
    out = json.loads((APP / "default_pages.json").read_text())
    for name, spec in _saved().items():
        out[name] = spec
    return out


def page_name(name: str) -> str:
    return re.sub(r"[^a-z0-9_-]+", "-", name.strip().lower()).strip("-")


HOME = next(iter(json.loads((APP / "default_pages.json").read_text())))


# ── the screen (all on the worker loop) ─────────────────────────────────────────

class Screen:
    def __init__(self) -> None:
        self.loop = asyncio.new_event_loop()
        self.png = render.blank_png()  # what /screen.png serves
        self.digest = ""
        self.view: dict = {"kind": "page", "name": HOME}
        self.view_since = time.monotonic()
        self.rendered_at = 0.0
        self.pushed_at = 0.0
        self.watched: set[str] = set()
        self.pending: asyncio.Handle | None = None
        self.ha: HA | None = None
        self.lock: asyncio.Lock | None = None
        self.problems: list[str] = []  # card blocks that failed in the last render

    def start(self) -> None:
        threading.Thread(target=self.loop.run_forever, daemon=True, name="screen").start()
        self.call(self._boot())

    def call(self, coro):
        """Run a coroutine on the worker loop; returns a concurrent Future."""
        return asyncio.run_coroutine_threadsafe(coro, self.loop)

    async def _boot(self) -> None:
        self.ha = HA()
        self.lock = asyncio.Lock()
        asyncio.create_task(self._ticker())
        asyncio.create_task(self._follow_changes())
        try:
            await self.show({"kind": "page", "name": HOME})
        except Exception as err:  # noqa: BLE001 - HA may still be starting; the ticker retries
            print(f"[screen] first render failed: {err}", flush=True)

    # rendering

    async def _render(self, view: dict) -> tuple[bytes, list[dict], list[str]]:
        """→ (png, widgets left out, unknown entity ids). Sets what to watch."""
        if view["kind"] == "static":
            self.watched = set()
            return view["png"], [], []
        spec = view["spec"] if view["kind"] in ("spec", "card") else pages()[view["name"]]
        if "card" in spec:
            return await self._render_card(spec["card"])
        items = wg.parse_widgets(spec["widgets"])
        states = await self.ha.states()
        need = wg.needs(items, WEATHER, states)
        ctx = wg.Ctx(now=datetime.now(wg.TZ), states=states, weather_entity=WEATHER)
        for kind in need["forecast"]:
            ctx.forecasts[kind] = await self.ha.forecast(WEATHER, kind)
        for eid in need["todo"]:
            ctx.todos[eid] = await self.ha.todo_items(eid)
        html, left = wg.page_html(spec.get("title", ""), items, ctx, ASSET_BASE)
        png = await render.html_to_png(html)
        self.watched = need["entities"]
        return png, left, sorted(e for e in need["entities"] if e not in states)

    async def _render_card(self, card: dict) -> tuple[bytes, list[dict], list[str]]:
        card = cards.parse_card(card)
        states = await self.ha.states()
        assets: dict = {}
        await self._prepare(card.get("body"), assets, states, narrow=False)
        b = cards.Builder(states, assets, _icon)
        html = b.page(card, ASSET_BASE)
        self.problems = b.problems
        png = await render.page_to_png(_serve_page(html))
        self.watched = cards.watched(card)
        return png, [], sorted(set(b.unknown))

    async def _prepare(self, body, assets: dict, states: dict, narrow: bool) -> None:
        """Server-side parts of a card: maps and photos as 1-bit PNGs, chart history."""
        for n in cards._nodes(body):
            t = n.get("type")
            kids_narrow = narrow or t in ("columns", "grid", "row")
            if n.get("children"):
                await self._prepare(n["children"], assets, states, kids_narrow)
            try:
                height = int(cards._clamp(n.get("height", 300), 60, 440, 300))
                width = int(cards._clamp(n.get("width", 360 if narrow else 740), 100, 760, 740))
                if t == "map":
                    markers, path = n.get("markers"), n.get("path")
                    if isinstance(n.get("route"), dict):
                        markers, path = await _route_points(n["route"])
                    png = await maps.render_map(width, height, n.get("center"), n.get("zoom"), markers, path)
                    assets[id(n)] = cards.data_uri_png(png)
                elif t == "image" and n.get("url"):
                    assets[id(n)] = cards.data_uri_png(await maps.render_photo(n["url"], width, height))
                elif t == "chart" and n.get("entity_ids"):
                    ids = n["entity_ids"] if isinstance(n["entity_ids"], list) else [n["entity_ids"]]
                    raw = await self.ha.history(ids, cards._clamp(n.get("hours", 24), 1, 336, 24))
                    assets[id(n)] = cards.history_rows(raw, states)
            except Exception as err:  # noqa: BLE001 - the block shows as missing, the card still renders
                print(f"[screen] {t} failed: {err}", flush=True)

    async def show(self, view: dict, push: bool = True) -> tuple[list[dict], list[str], str]:
        """Make `view` the current screen. → (left out, unknown entities, device result)."""
        async with self.lock:
            png, left, unknown = await self._render(view)
            if view != self.view:
                self.view, self.view_since = view, time.monotonic()
            result = await self._publish(png, push)
        return left, unknown, result

    async def _publish(self, png: bytes, push: bool) -> str:
        self.rendered_at = time.monotonic()
        digest = hashlib.sha256(png).hexdigest()
        if digest == self.digest:
            return "ongewijzigd"
        self.png, self.digest = png, digest
        if not push:
            return "ok"
        self.pushed_at = time.monotonic()
        return await refresh_device()

    async def navigate(self, step: int | None) -> bytes:
        """A device button: step through the rotation (None = home). The device is already
        fetching, so render and return the PNG without pushing."""
        names = list(pages())
        if step is None:
            name = HOME
        else:
            cur = names.index(self.view["name"]) if self.view.get("kind") == "page" and self.view["name"] in names else -1
            name = names[(cur + step) % len(names)] if cur >= 0 else names[0 if step > 0 else -1]
        await self.show({"kind": "page", "name": name}, push=False)
        return self.png

    # keeping it current

    async def _ticker(self) -> None:
        while True:
            await asyncio.sleep(30)
            try:
                now = time.monotonic()
                if self.view != {"kind": "page", "name": HOME} and HOME_AFTER_S and now - self.view_since > HOME_AFTER_S:
                    await self.show({"kind": "page", "name": HOME})
                elif self.view["kind"] != "static" and now - self.rendered_at > REFRESH_S:
                    await self.show(self.view)
            except Exception as err:  # noqa: BLE001 - keep ticking
                print(f"[screen] refresh failed: {err}", flush=True)

    async def _follow_changes(self) -> None:
        import websockets

        url, token = ws_endpoint()
        while True:
            try:
                async with websockets.connect(url, max_size=None) as ws:
                    await ws.recv()  # auth_required
                    await ws.send(json.dumps({"type": "auth", "access_token": token}))
                    if json.loads(await ws.recv()).get("type") != "auth_ok":
                        raise RuntimeError("websocket auth failed")
                    await ws.send(json.dumps({"id": 1, "type": "subscribe_events", "event_type": "state_changed"}))
                    async for raw in ws:
                        msg = json.loads(raw)
                        if msg.get("type") == "event":
                            self._on_change(msg["event"]["data"])
            except Exception as err:  # noqa: BLE001 - reconnect
                print(f"[screen] websocket: {err}; reconnecting", flush=True)
                await asyncio.sleep(10)

    def _on_change(self, data: dict) -> None:
        eid, old, new = data.get("entity_id"), data.get("old_state"), data.get("new_state")
        if eid not in self.watched or new is None or self.view["kind"] == "static":
            return
        if eid.startswith("sensor.") and wg.is_numeric(new.get("state")):
            return  # measurements change all the time; the periodic refresh shows them
        shown = lambda s: (wg.fmt_state(s), (s or {}).get("attributes", {}).get("temperature"))  # noqa: E731
        if shown(old) == shown(new):
            return
        if self.pending is None:
            self.pending = self.loop.call_later(LIVE_DEBOUNCE_S, lambda: asyncio.ensure_future(self._live_redraw()))

    async def _live_redraw(self) -> None:
        wait = LIVE_MIN_GAP_S - (time.monotonic() - self.pushed_at)
        if wait > 0:
            await asyncio.sleep(wait)
        self.pending = None
        try:
            await self.show(self.view)
        except Exception as err:  # noqa: BLE001
            print(f"[screen] live redraw failed: {err}", flush=True)


async def _route_points(spec: dict) -> tuple[list[dict], list]:
    """A map block's {"van", "naar", "vervoer"} → (markers, path) via the public router (geo.py)."""
    async with httpx.AsyncClient(timeout=12, headers=geo.UA, follow_redirects=True) as cli:
        a_label, alat, alon = await geo.geocode(cli, str(spec.get("van", "thuis")))
        b_label, blat, blon = await geo.geocode(cli, str(spec.get("naar", "")))
        rt = await geo.route(cli, (alat, alon), (blat, blon), str(spec.get("vervoer", "fiets")))
    short = lambda s: s.split(",")[0]  # noqa: E731 - "Sittard, Sittard-Geleen, Limburg" → "Sittard"
    return ([{"lat": alat, "lon": alon, "label": short(a_label)}, {"lat": blat, "lon": blon, "label": short(b_label)}],
            rt["path"])


async def refresh_device() -> str:
    """Trigger the reTerminal to re-fetch /screen.png and redraw."""
    if NO_DEVICE:
        return "niet gepusht (SCREEN_NO_DEVICE)"
    cli = APIClient(RETERMINAL, 6053, None)
    try:
        await cli.connect(login=False)
    except Exception as err:  # noqa: BLE001
        return f"scherm niet bereikbaar: {err}"
    try:
        _, services = await cli.list_entities_services()
        svc = next((s for s in services if s.name == "refresh_screen"), None)
        if svc is None:
            return "device heeft geen refresh_screen service"
        res = cli.execute_service(svc, {})
        if asyncio.iscoroutine(res):
            await res
        return "ok"
    finally:
        await cli.disconnect()


screen = Screen()


# ── HTTP host for the device and the browser ────────────────────────────────────

_TYPES = {".css": "text/css", ".woff2": "font/woff2", ".woff": "font/woff", ".ttf": "font/ttf",
          ".png": "image/png", ".js": "text/javascript", ".svg": "image/svg+xml"}
# Static trees for the renderer. TRMNL's CSS asks for its fonts at the root (/fonts/...).
_STATIC = {"/assets/": APP / "assets", "/trmnl/": APP / "assets" / "trmnl", "/fonts/": APP / "assets" / "trmnl-fonts",
           "/vendor/": APP / "assets" / "vendor"}
_pages: dict[str, str] = {}  # token → HTML being rendered (kept briefly)


def _serve_page(html: str) -> str:
    """Hold `html` for the renderer and return its URL on this add-on (same origin as /fonts)."""
    import secrets

    token = secrets.token_hex(8)
    _pages[token] = html
    while len(_pages) > 8:
        _pages.pop(next(iter(_pages)))
    return f"{ASSET_BASE}/render/{token}.html"


def _icon(name: str, size: int = 32) -> str:
    name = str(name).removeprefix("mdi:").removeprefix("mdi-")
    return f'<i class="mdi mdi-{wg.esc(name)}" style="font-size:{size}px;line-height:1"></i>'


class _Handler(BaseHTTPRequestHandler):
    def log_message(self, *a):  # silence
        pass

    def _send(self, data: bytes, ctype: str) -> None:
        self.send_response(200)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(data)))
        # Rendered pages are about:blank, so fonts from /assets are cross-origin to them.
        self.send_header("Access-Control-Allow-Origin", "*")
        self.end_headers()
        self.wfile.write(data)

    def do_GET(self):  # noqa: N802
        path = self.path.split("?")[0]
        if path.rstrip("/").endswith("screen.png"):
            return self._send(screen.png, "image/png")
        nav = {"/nav/home.png": None, "/nav/next.png": 1, "/nav/prev.png": -1}
        if path in nav:
            try:
                png = screen.call(screen.navigate(nav[path])).result(timeout=25)
            except Exception as err:  # noqa: BLE001
                print(f"[screen] nav failed: {err}", flush=True)
                png = screen.png
            return self._send(png, "image/png")
        if path.startswith("/render/"):
            html = _pages.get(path.rsplit("/", 1)[-1].removesuffix(".html"))
            if html is not None:
                return self._send(html.encode(), "text/html; charset=utf-8")
        for prefix, root in _STATIC.items():
            if path.startswith(prefix):
                f = (root / path[len(prefix):]).resolve()
                if f.is_file() and root.resolve() in f.parents:
                    return self._send(f.read_bytes(), _TYPES.get(f.suffix, "application/octet-stream"))
        self.send_error(404)


def _start_http() -> None:
    threading.Thread(target=ThreadingHTTPServer(("0.0.0.0", PORT), _Handler).serve_forever, daemon=True).start()


# ── MCP tools ────────────────────────────────────────────────────────────────────

async def _on_screen(coro):
    return await asyncio.wrap_future(screen.call(coro))


def _report(what: str, left: list[dict], unknown: list[str], result: str) -> str:
    msg = f"{what} ({result})."
    if left:
        msg += f" Paste niet en weggelaten: {', '.join(w['type'] for w in left)}."
    if unknown:
        msg += f" Onbekende entiteiten (toont '?'): {', '.join(unknown)}."
    return msg


# Server↔server MCP on a trusted internal add-on network — disable the browser-oriented
# DNS-rebinding guard (localhost-only, no glob) so non-localhost Host headers don't 421.
mcp = FastMCP(
    "screen",
    host=os.getenv("MCP_HOST", "0.0.0.0"),
    port=int(os.getenv("MCP_PORT", "8788")),
    transport_security=TransportSecuritySettings(enable_dns_rebinding_protection=False),
)


@mcp.tool()
async def list_pages() -> str:
    """Geef de lijst met schermen (dashboards) die op het e-paper scherm (reTerminal) kunnen,
    met wat erop staat en welke nu getoond wordt. Gebruik dit als de gebruiker vraagt welke
    schermen of dashboards er zijn."""
    cur = screen.view.get("name") if screen.view.get("kind") == "page" else None
    lines = [f"- {n}: {p.get('description') or p.get('title') or n}{' (nu op het scherm)' if n == cur else ''}"
             for n, p in pages().items()]
    if cur is None:
        lines.append("(Nu staat er een los scherm op, geen bewaarde pagina.)")
    return "Schermen:\n" + "\n".join(lines)


@mcp.tool()
async def show_page(name: str) -> str:
    """Toon een bewaard scherm (dashboard) op het e-paper scherm, bijv. 'dashboard', 'weer',
    'energie', 'binnen', 'boodschappen' of een zelf bewaard scherm. list_pages geeft de namen.
    Het scherm blijft zichzelf bijwerken met live gegevens."""
    key = page_name(name)
    if key not in pages():
        return f"Geen scherm '{name}'. Beschikbaar: {', '.join(pages())}."
    left, unknown, result = await _on_screen(screen.show({"kind": "page", "name": key}))
    return _report(f"Scherm '{key}' getoond", left, unknown, result)


@mcp.tool()
async def show_dashboard(widgets: list[dict] | str, title: str = "", save_as: str = "", description: str = "") -> str:
    """Stel zelf een dashboard samen op het e-paper scherm uit blokken met live gegevens uit
    Home Assistant. Het scherm is een raster van 4 kolommen x 3 rijen; blokken worden in
    volgorde geplaatst (wat niet past valt weg). Blokken (maat kolommen x rijen):
    - {"type":"weather_now"} weer nu, 2x1; met "large":true 2x2
    - {"type":"forecast_hours","hours":4} komende uren, 2x1; met "wide":true 4x1 tot 8 uur
    - {"type":"forecast_days","days":5} komende dagen, 2x2
    - {"type":"value","entity_id":"sensor.x","label":"kort label","icon":"flash"} één waarde
      groot, 1x1 (lampen: aan/uit of %, rolluiken: positie, thermostaat: temperatuur).
      icon = naam van een Material Design Icon zonder 'mdi-' (optioneel).
    - {"type":"list","entity_ids":["light.a","sensor.b"],"title":"..."} rijtje waarden,
      2x1 (tot 3), 2x2 (tot 6)
    - {"type":"rooms","entity_ids":["climate.a",...]} temperatuur per kamer, 2x2
    - {"type":"todo","entity_id":"todo.shopping_list"} lijst, 2x2; "large":true hele scherm
    - {"type":"text","text":"...","title":"..."} tekst, 2x1; "wide":true 4x1; "large":true
      hele scherm
    - {"type":"price_chart","hours":12} grafiek van de stroomprijs per uur voor de komende
      uren (Zonneplan), 2x1; "wide":true 4x1 tot 24 uur
    title = kop bovenaan (leeg = datum). Gebruik alleen entity_ids die je kent (zoek ze op).
    save_as = naam om het te bewaren als vast scherm (komt dan ook onder de knoppen van het
    scherm en in list_pages; dezelfde naam overschrijft), description = korte omschrijving."""
    try:
        items = wg.parse_widgets(widgets)
    except ValueError as err:
        return f"Fout in widgets: {err}"
    spec = {"title": title, "widgets": items}
    if save_as:
        key = page_name(save_as)
        saved = _saved()
        saved[key] = {**spec, "description": description or title or key}
        DATA.mkdir(parents=True, exist_ok=True)
        (DATA / "pages.json").write_text(json.dumps(saved, ensure_ascii=False, indent=2))
        view, what = {"kind": "page", "name": key}, f"Scherm '{key}' bewaard en getoond"
    else:
        view, what = {"kind": "spec", "spec": spec}, "Dashboard getoond"
    left, unknown, result = await _on_screen(screen.show(view))
    return _report(what, left, unknown, result)


@mcp.tool()
async def screen_components() -> str:
    """De bouwdoos voor show_card: alle blokken (getallen, titels, lijsten, tabellen, grafieken,
    kaarten, kompas, iconen, afbeeldingen), hun velden, ontwerpregels en voorbeelden. Vraag dit op
    voordat je een kaart maakt."""
    return cards.CATALOG


@mcp.tool()
async def show_card(card: dict | str, save_as: str = "", description: str = "") -> str:
    """Toon een zelf samengestelde kaart op het e-paper scherm (TRMNL-stijl, 800x480 zwart-wit),
    als verlengstuk van je antwoord: getallen, lijstjes, stappen, vergelijkingen, grafieken,
    kaarten, richtingen. card = JSON volgens screen_components (vraag die eerst op).
    Blokken met entity_id blijven live bijwerken. save_as = bewaar als vast scherm (komt onder
    de knoppen en in list_pages), description = korte omschrijving."""
    try:
        parsed = cards.parse_card(card)
    except ValueError as err:
        return f"Fout in card: {err}. Vraag screen_components op voor het formaat."
    if save_as:
        key = page_name(save_as)
        saved = _saved()
        saved[key] = {"title": parsed.get("title", key), "card": parsed,
                      "description": description or parsed.get("title") or key}
        DATA.mkdir(parents=True, exist_ok=True)
        (DATA / "pages.json").write_text(json.dumps(saved, ensure_ascii=False, indent=2))
        view, what = {"kind": "page", "name": key}, f"Kaart '{key}' bewaard en getoond"
    else:
        view, what = {"kind": "card", "spec": {"card": parsed}}, "Kaart getoond"
    try:
        _, unknown, result = await _on_screen(screen.show(view))
    except Exception as err:  # noqa: BLE001
        return f"Kaart niet gelukt: {str(err)[:200]}"
    msg = _report(what, [], unknown, result)
    if screen.problems:
        msg += f" Problemen: {'; '.join(screen.problems)[:300]}."
    return msg


@mcp.tool()
async def delete_page(name: str) -> str:
    """Verwijder een zelf bewaard scherm. Bij een ingebouwd scherm met dezelfde naam komt de
    standaardversie terug."""
    key, saved = page_name(name), _saved()
    if key not in saved:
        return f"'{name}' is geen zelf bewaard scherm."
    del saved[key]
    (DATA / "pages.json").write_text(json.dumps(saved, ensure_ascii=False, indent=2))
    if screen.view == {"kind": "page", "name": key} and key not in pages():
        await _on_screen(screen.show({"kind": "page", "name": HOME}))
    return f"Scherm '{key}' verwijderd."


@mcp.tool()
async def show_text(title: str, body: str = "") -> str:
    """Toon tekst op het e-paper scherm (reTerminal). title = kop, body = tekst eronder.
    Gebruik dit om iets visueel te tonen aan de gebruiker."""
    spec = {"title": title, "widgets": [{"type": "text", "text": body, "large": True}]}
    _, _, result = await _on_screen(screen.show({"kind": "spec", "spec": spec}))
    return f"Scherm bijgewerkt ({result}): {title}"


@mcp.tool()
async def show_image(url: str) -> str:
    """Toon een afbeelding van een URL op het scherm (wordt naar 800x480 zwart-wit
    geschaald)."""
    # Wikimedia and others answer 403 to clients without a User-Agent.
    headers = {"User-Agent": "mcp-screen/0.3 (reTerminal e-paper; https://github.com/mosa-riel/mcp-screen)"}
    async with httpx.AsyncClient(timeout=15, follow_redirects=True, headers=headers) as c:
        r = await c.get(url)
        r.raise_for_status()
    png = render.image_to_png(r.content)
    _, _, result = await _on_screen(screen.show({"kind": "static", "png": png}))
    return f"Afbeelding getoond ({result})"


@mcp.tool()
async def show_html(html: str) -> str:
    """Toon een vrij opgemaakt scherm via HTML+CSS (800x480) op het e-paper, voor tekeningen
    en ontwerpen die show_card niet kan. Het scherm is 1-bit ZWART/WIT: hoog contrast, geen
    grijs, GEEN emoji. Geladen (geen CDN nodig): het TRMNL Framework (zet je inhoud in
    <div class="screen screen--og screen--1bit"><div class="view view--full"><div class="layout">
    ...</div></div></div>; klassen als value value--xlarge, title, label, item, columns, table),
    Inter en Material Design Icons (<i class="mdi mdi-weather-sunny"></i>). Houd het groot en
    strak. Geef volledige HTML met inline <style> of svg."""
    head = f'<link rel="stylesheet" href="{ASSET_BASE}/assets/base.css">' + cards.framework_head(ASSET_BASE)
    html = re.sub(r"(<head[^>]*>)", r"\1" + head, html, count=1, flags=re.I) if re.search(r"<head", html, re.I) else head + html
    if re.search(r'class="[^"]*\bscreen\b', html) and not re.search(r'<body[^>]*\btrmnl\b', html):
        html = re.sub(r"<body([^>]*)>", r'<body class="environment trmnl"\1>', html, count=1, flags=re.I) \
            if re.search(r"<body", html, re.I) else f'<div class="environment trmnl">{html}</div>'
    png = await _on_screen(render.page_to_png(_serve_page(html)))
    _, _, result = await _on_screen(screen.show({"kind": "static", "png": png}))
    return f"Scherm bijgewerkt ({result})"


@mcp.tool()
async def clear_screen() -> str:
    """Maak het scherm leeg (wit)."""
    _, _, result = await _on_screen(screen.show({"kind": "static", "png": render.blank_png()}))
    return f"Scherm leeg ({result})"


if __name__ == "__main__":
    _start_http()
    screen.start()
    mcp.run(transport="streamable-http")
