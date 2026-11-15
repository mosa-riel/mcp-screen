"""Maps and photos for cards, prepared server-side as 1-bit PNGs (no WebGL in the browser).

Maps: PDOK BRT-Achtergrondkaart raster tiles ("grijs", Web Mercator XYZ, Netherlands only;
public, no key, https://www.pdok.nl), stitched and redrawn in a 1-bit map style (outlined
blocks and streets, dotted fills, names with a white halo), with an optional route line and
numbered markers drawn on top. Tiles are cached in memory.
"""

from __future__ import annotations

import asyncio
import io
import math
from pathlib import Path

import httpx
from PIL import Image, ImageChops, ImageDraw, ImageFilter, ImageFont, ImageOps

TILE = 256
PDOK = "https://service.pdok.nl/brt/achtergrondkaart/wmts/v2_0/grijs/EPSG:3857/{z}/{x}/{y}.png"
# Outside the Netherlands PDOK has no tiles: OpenStreetMap's standard tiles (tile usage policy:
# light use with an identifying User-Agent, attribution on the map).
OSM = "https://tile.openstreetmap.org/{z}/{x}/{y}.png"
NL = (50.70, 53.60, 3.30, 7.25)  # lat_min, lat_max, lon_min, lon_max
OSM_THRESHOLD = 190  # OSM colours in grey: land ~240, water/forest ~200-215, roads and names darker
UA = {"User-Agent": "mcp-screen (reTerminal e-paper; https://github.com/mosa-riel/mcp-screen)"}
_tiles: dict[tuple[int, int, int], Image.Image] = {}


def _px(lat: float, lon: float, z: int) -> tuple[float, float]:
    n = TILE * 2 ** z
    x = (lon + 180) / 360 * n
    y = (1 - math.log(math.tan(math.radians(lat)) + 1 / math.cos(math.radians(lat))) / math.pi) / 2 * n
    return x, y


async def _tile(cli: httpx.AsyncClient, z: int, x: int, y: int, src: str = PDOK) -> Image.Image:
    key = (src, z, x, y)
    if key not in _tiles:
        try:
            r = await cli.get(src.format(z=z, x=x, y=y))
            r.raise_for_status()
            tile = Image.open(io.BytesIO(r.content)).convert("RGBA")
            white = Image.new("RGBA", tile.size, (255, 255, 255, 255))
            _tiles[key] = Image.alpha_composite(white, tile).convert("L")  # transparent = white
        except Exception:  # noqa: BLE001 - outside NL or offline: blank tile
            _tiles[key] = Image.new("L", (TILE, TILE), 255)
        if len(_tiles) > 400:
            _tiles.pop(next(iter(_tiles)))
    return _tiles[key]


def _mask(v: Image.Image, lo: int, hi: int) -> Image.Image:
    return v.point(lambda p: 255 if lo <= p < hi else 0)


def _dots(w: int, h: int, step: int) -> Image.Image:
    im = Image.new("L", (w, h), 0)
    px = im.load()
    for y in range(0, h, step):
        for x in range((y // step * 2) % step, w, step):
            px[x, y] = 255
    return im


def _pdok_1bit(v: Image.Image, z: int) -> Image.Image:
    """'grijs' (white streets ~255, blocks ~235, open land ~242, water ~225, names dark grey) as
    a 1-bit map. A plain threshold breaks the thin grey road casings into dashes, so: zoomed in,
    outline every block and dot its fill; zoomed out, keep the roads and lines and dot the built-
    up areas sparsely. Names are drawn solid with a white halo so they stay readable."""
    w, h = v.size
    text = _mask(v, 0, 160)
    if z >= 16:
        area = _mask(v, 0, 248)
        lines = ImageChops.subtract(area, area.filter(ImageFilter.MinFilter(3)))
        fill = ImageChops.multiply(_mask(v, 200, 239), _dots(w, h, 4))
    else:
        lines = _mask(v, 0, 222)
        fill = ImageChops.multiply(_mask(v, 228, 239), _dots(w, h, 5))
    black = ImageChops.subtract(ImageChops.lighter(lines, fill), text.filter(ImageFilter.MaxFilter(5)))
    return ImageChops.invert(ImageChops.lighter(black, text))


_INTER = Path(__file__).parent / "assets" / "trmnl-fonts" / "Inter.ttf"


def _font(size: int):
    try:
        f = ImageFont.truetype(str(_INTER), size)
        f.set_variation_by_axes([700])  # variable font: bold
        return f
    except (OSError, ValueError, AttributeError):
        pass
    try:
        return ImageFont.truetype("DejaVuSans-Bold.ttf", size)
    except OSError:
        return ImageFont.load_default()


async def render_map(width: int, height: int, center: dict | None = None, zoom: float | None = None,
                     markers: list[dict] | None = None, path: list | None = None) -> bytes:
    markers = [m for m in (markers or []) if isinstance(m, dict) and "lat" in m and "lon" in m]
    pts = [(float(p[0]), float(p[1])) for p in (path or []) if isinstance(p, (list, tuple)) and len(p) >= 2]
    pts += [(float(m["lat"]), float(m["lon"])) for m in markers]
    if zoom is None:
        if len(pts) >= 2:
            zoom = 16
            while zoom > 5:  # largest zoom at which all points fit with a margin
                xs, ys = zip(*(_px(la, lo, zoom) for la, lo in pts))
                if max(xs) - min(xs) < width * 0.8 and max(ys) - min(ys) < height * 0.8:
                    break
                zoom -= 1
        else:
            zoom = 13
    z = int(max(5, min(17, zoom)))
    if center and "lat" in center and "lon" in center:
        cx, cy = _px(float(center["lat"]), float(center["lon"]), z)
    elif pts:
        xs, ys = zip(*(_px(la, lo, z) for la, lo in pts))
        cx, cy = (min(xs) + max(xs)) / 2, (min(ys) + max(ys)) / 2
    else:
        cx, cy = _px(52.3, 6.75, z)
    x0, y0 = cx - width / 2, cy - height / 2
    tx0, ty0, tx1, ty1 = int(x0 // TILE), int(y0 // TILE), int((x0 + width) // TILE), int((y0 + height) // TILE)
    lat_c = math.degrees(math.atan(math.sinh(math.pi * (1 - 2 * cy / (TILE * 2 ** z)))))
    lon_c = cx / (TILE * 2 ** z) * 360 - 180
    in_nl = NL[0] <= lat_c <= NL[1] and NL[2] <= lon_c <= NL[3]
    src, credit = (PDOK, "PDOK / Kadaster") if in_nl else (OSM, "© OpenStreetMap")
    async with httpx.AsyncClient(timeout=10, headers=UA) as cli:
        coords = [(tx, ty) for ty in range(ty0, ty1 + 1) for tx in range(tx0, tx1 + 1)]
        tiles = await asyncio.gather(*(_tile(cli, z, tx, ty, src) for tx, ty in coords))
    canvas = Image.new("L", ((tx1 - tx0 + 1) * TILE, (ty1 - ty0 + 1) * TILE), 255)
    for (tx, ty), im in zip(coords, tiles):
        canvas.paste(im, ((tx - tx0) * TILE, (ty - ty0) * TILE))
    ox, oy = int(x0 - tx0 * TILE), int(y0 - ty0 * TILE)
    img = canvas.crop((ox, oy, ox + width, oy + height))
    img = _pdok_1bit(img, z) if in_nl else img.point(lambda v: 255 if v > OSM_THRESHOLD else 0)
    img = img.convert("RGB")
    d = ImageDraw.Draw(img)
    to = lambda la, lo: (_px(la, lo, z)[0] - x0, _px(la, lo, z)[1] - y0)  # noqa: E731
    line = [to(la, lo) for la, lo in ((float(p[0]), float(p[1])) for p in (path or []) if len(p) >= 2)]
    if len(line) >= 2:
        d.line(line, fill="white", width=11, joint="curve")
        d.line(line, fill="black", width=6, joint="curve")
    f = _font(18)
    for i, m in enumerate(markers, 1):
        x, y = to(float(m["lat"]), float(m["lon"]))
        d.ellipse((x - 13, y - 13, x + 13, y + 13), fill="black", outline="white", width=3)
        d.text((x, y), str(i), fill="white", font=f, anchor="mm")
        if m.get("label"):
            tw = d.textlength(str(m["label"]), font=f)
            d.rectangle((x + 16, y - 13, x + 24 + tw, y + 13), fill="white", outline="black", width=2)
            d.text((x + 20, y), str(m["label"]), fill="black", font=f, anchor="lm")
    d.rectangle((0, 0, width - 1, height - 1), outline="black", width=2)
    small = _font(11)
    d.rectangle((width - 12 - d.textlength(credit, font=small), height - 18, width - 3, height - 3), fill="white")
    d.text((width - 6, height - 4), credit, fill="black", font=small, anchor="rd")
    out = io.BytesIO()
    img.convert("1", dither=Image.Dither.NONE).save(out, "PNG", optimize=True)
    return out.getvalue()


async def render_photo(url: str, width: int, height: int) -> bytes:
    async with httpx.AsyncClient(timeout=15, follow_redirects=True, headers=UA) as cli:
        r = await cli.get(url)
        r.raise_for_status()
    img = Image.open(io.BytesIO(r.content)).convert("L")
    img.thumbnail((width, height))
    img = ImageOps.autocontrast(img, cutoff=1)
    out = io.BytesIO()
    img.convert("1").save(out, "PNG", optimize=True)  # Floyd-Steinberg for photos
    return out.getvalue()
