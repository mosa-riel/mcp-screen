"""Screen — an MCP server that drives the reTerminal E1001 e-paper.

Two servers in one process:
  1. the **MCP** endpoint (streamable-HTTP, MCP_PORT, default 8788) the agent calls;
  2. an embedded **PNG host** (SCREEN_PORT, default 8799) the reTerminal fetches from.

A tool renders/accepts content, this server HOSTS the resulting PNG on the LAN, and
triggers the device's `refresh_screen` ESPHome service so it fetches + redraws. The
agent just calls a tool (show_text / show_image / clear_screen).

Networking: runs as a **host_network** HA add-on so (a) the reTerminal can fetch the
PNG over the LAN and (b) this server can reach the device's ESPHome API (:6053) by
mDNS. The reTerminal firmware's online_image URL must point at this host:
  http://<ha-host-ip>:<SCREEN_PORT>/screen.png   (default :8799)

Env: RETERMINAL_HOST (default reterminal-e1001.local), SCREEN_PORT (8799),
     MCP_HOST (0.0.0.0), MCP_PORT (8788).
"""

from __future__ import annotations

import asyncio
import glob
import io
import os
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import httpx
from aioesphomeapi import APIClient
from mcp.server.fastmcp import FastMCP
from mcp.server.transport_security import TransportSecuritySettings
from PIL import Image, ImageDraw, ImageFont


def _load_ha_options() -> None:
    """When running as a HA add-on, map /data/options.json → env (no-op elsewhere)."""
    import json

    path = "/data/options.json"
    try:
        with open(path) as f:
            opts = json.load(f)
    except (OSError, ValueError):
        return
    mapping = {"reterminal_host": "RETERMINAL_HOST", "screen_port": "SCREEN_PORT", "mcp_port": "MCP_PORT"}
    for key, env in mapping.items():
        if key in opts and opts[key] not in (None, ""):
            os.environ[env] = str(opts[key])  # HA options win over the image's ENV defaults


_load_ha_options()

RETERMINAL = os.getenv("RETERMINAL_HOST", "reterminal-e1001.local")
PORT = int(os.getenv("SCREEN_PORT", "8799"))
W, H = 800, 480

_png: bytes | None = None
_lock = threading.Lock()


def _font(size: int, bold: bool = False):
    for p in glob.glob(f"/usr/share/fonts/**/DejaVuSans{'-Bold' if bold else ''}.ttf", recursive=True):
        return ImageFont.truetype(p, size)
    return ImageFont.load_default()


def _wrap(text: str, n: int) -> list[str]:
    out, cur = [], ""
    for w in text.split():
        if len(cur) + len(w) + 1 > n:
            out.append(cur)
            cur = w
        else:
            cur = (cur + " " + w).strip()
    if cur:
        out.append(cur)
    return out or [""]


def _render_text(title: str, body: str) -> bytes:
    img = Image.new("1", (W, H), 1)  # 1-bit, white
    d = ImageDraw.Draw(img)
    d.rectangle([6, 6, W - 7, H - 7], outline=0, width=3)
    d.text((40, 36), title[:42], font=_font(60, True), fill=0)
    y = 140
    for line in _wrap(body, 40)[:8]:
        d.text((40, y), line, font=_font(34), fill=0)
        y += 46
    buf = io.BytesIO()
    img.save(buf, "PNG")
    return buf.getvalue()


def _publish(data: bytes) -> None:
    global _png
    with _lock:
        _png = data


class _Handler(BaseHTTPRequestHandler):
    def log_message(self, *a):  # silence
        pass

    def do_GET(self):  # noqa: N802
        with _lock:
            data = _png
        if not self.path.rstrip("/").endswith("screen.png") or data is None:
            self.send_error(404)
            return
        self.send_response(200)
        self.send_header("Content-Type", "image/png")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)


def _start_server() -> None:
    threading.Thread(target=ThreadingHTTPServer(("0.0.0.0", PORT), _Handler).serve_forever, daemon=True).start()


async def _refresh_device() -> str:
    """Trigger the reTerminal to re-fetch + redraw."""
    cli = APIClient(RETERMINAL, 6053, None)
    await cli.connect(login=False)
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


_browser = None  # lazily-launched headless Chromium (Playwright), reused across calls


async def _render_html(html: str) -> bytes:
    """Render an HTML/CSS page to an 800x480 1-bit PNG via headless Chromium."""
    global _browser
    from playwright.async_api import async_playwright

    if _browser is None:
        pw = await async_playwright().start()
        _browser = await pw.chromium.launch(args=["--no-sandbox", "--disable-dev-shm-usage"])
    page = await _browser.new_page(viewport={"width": W, "height": H})
    try:
        await page.set_content(html, wait_until="networkidle")
        png = await page.screenshot(type="png", clip={"x": 0, "y": 0, "width": W, "height": H})
    finally:
        await page.close()
    img = Image.open(io.BytesIO(png)).convert("L").resize((W, H)).convert("1")  # Floyd–Steinberg
    buf = io.BytesIO()
    img.save(buf, "PNG")
    return buf.getvalue()


# Server↔server MCP on a trusted internal add-on network — disable the browser-oriented
# DNS-rebinding guard (localhost-only, no glob) so non-localhost Host headers don't 421.
mcp = FastMCP(
    "screen",
    host=os.getenv("MCP_HOST", "0.0.0.0"),
    port=int(os.getenv("MCP_PORT", "8788")),
    transport_security=TransportSecuritySettings(enable_dns_rebinding_protection=False),
)


@mcp.tool()
async def show_text(title: str, body: str = "") -> str:
    """Toon tekst op het e-paper scherm (reTerminal). title = grote kop, body = tekst
    eronder. Gebruik dit om iets visueel te tonen aan de gebruiker."""
    _publish(_render_text(title, body))
    return f"Scherm bijgewerkt ({await _refresh_device()}): {title}"


@mcp.tool()
async def show_image(url: str) -> str:
    """Toon een afbeelding van een URL op het scherm (wordt naar 800x480 1-bit
    geschaald)."""
    async with httpx.AsyncClient(timeout=15) as c:
        r = await c.get(url)
        r.raise_for_status()
    img = Image.open(io.BytesIO(r.content)).convert("L").resize((W, H)).convert("1")
    buf = io.BytesIO()
    img.save(buf, "PNG")
    _publish(buf.getvalue())
    return f"Afbeelding getoond ({await _refresh_device()})"


@mcp.tool()
async def show_html(html: str) -> str:
    """Toon een mooi opgemaakt scherm via HTML+CSS (800x480) op het e-paper. Gebruik dit
    voor presentatie-achtige schermen: kaarten, kolommen, roosters, iconen. Regels voor dit
    scherm: het is 1-bit ZWART/WIT (geen kleur/grijs die belangrijk is — gebruik hoog
    contrast, zwarte tekst op wit). Gebruik voor iconen Material Design Icons (mdi
    webfont via CDN) of inline SVG — GEEN emoji (worden niet weergegeven). Houd het strak
    en groot (leesbaar op afstand). Geef volledige HTML met inline <style>."""
    _publish(await _render_html(html))
    return f"Scherm bijgewerkt ({await _refresh_device()})"


@mcp.tool()
async def clear_screen() -> str:
    """Maak het scherm leeg (wit)."""
    buf = io.BytesIO()
    Image.new("1", (W, H), 1).save(buf, "PNG")
    _publish(buf.getvalue())
    return f"Scherm leeg ({await _refresh_device()})"


if __name__ == "__main__":
    _start_server()
    mcp.run(transport="streamable-http")
