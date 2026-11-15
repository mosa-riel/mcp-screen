"""HTML → 800x480 1-bit PNG via headless Chromium, and image → 1-bit PNG.

Text and lines are thresholded (crisp edges on e-paper); only photos (show_image) are
dithered. Pages load fonts and icons from this server's own /assets (no internet).
"""

from __future__ import annotations

import io

from PIL import Image

W, H = 800, 480
THRESHOLD = 150  # grey anti-aliasing at or above this becomes white

_browser = None  # lazily launched, reused across renders


def to_png(img: Image.Image, dither: bool) -> bytes:
    grey = img.convert("L").resize((W, H))
    if dither:
        mono = grey.convert("1")  # Floyd-Steinberg
    else:
        mono = grey.point(lambda v: 255 if v >= THRESHOLD else 0, mode="1")
    buf = io.BytesIO()
    mono.save(buf, "PNG", optimize=True)
    return buf.getvalue()


async def html_to_png(html: str, dither: bool = False) -> bytes:
    global _browser
    from playwright.async_api import async_playwright

    if _browser is None:
        pw = await async_playwright().start()
        _browser = await pw.chromium.launch(args=["--no-sandbox", "--disable-dev-shm-usage",
                                                   "--font-render-hinting=full"])
    page = await _browser.new_page(viewport={"width": W, "height": H})
    try:
        await page.set_content(html, wait_until="networkidle")
        await page.evaluate("document.fonts.ready")
        png = await page.screenshot(type="png", clip={"x": 0, "y": 0, "width": W, "height": H})
    finally:
        await page.close()
    return to_png(Image.open(io.BytesIO(png)), dither)


def image_to_png(data: bytes) -> bytes:
    return to_png(Image.open(io.BytesIO(data)), dither=True)


def blank_png() -> bytes:
    buf = io.BytesIO()
    Image.new("1", (W, H), 1).save(buf, "PNG")
    return buf.getvalue()
