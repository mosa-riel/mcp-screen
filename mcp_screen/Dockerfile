# Plain Python base + headless Chromium. Fonts (Inter) and icons (Material Design Icons)
# are fetched once at build time into assets/, so rendering needs no internet.
FROM python:3.13-slim

ENV MCP_HOST=0.0.0.0 \
    MCP_PORT=8788 \
    SCREEN_PORT=8799 \
    RETERMINAL_HOST=reterminal-e1001.local \
    PIP_NO_CACHE_DIR=1 \
    PYTHONUNBUFFERED=1

# libatomic1: the node binary bundled with playwright needs it (not in the slim base image).
RUN apt-get update \
    && apt-get install -y --no-install-recommends fonts-dejavu-core libatomic1 \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app
# Pinned: an unpinned install pulled mcp 2.x (FastMCP renamed) and a playwright whose node
# needs libatomic. Bump deliberately and test the image (README, Local development).
RUN pip install "mcp==1.30.0" "httpx==0.28.1" "pillow==13.0.0" "aioesphomeapi==46.30.0" "playwright==1.65.2" "websockets==17.4"
# Headless Chromium for rendering (HTML/CSS → 1-bit screen). --with-deps apt-installs the
# browser's system libraries. Big layer (~400MB) — that's the cost of full HTML rendering.
RUN playwright install --with-deps chromium

ADD https://cdn.jsdelivr.net/npm/@fontsource/inter@5.1.0/files/inter-latin-500-normal.woff2 /app/assets/fonts/
ADD https://cdn.jsdelivr.net/npm/@fontsource/inter@5.1.0/files/inter-latin-700-normal.woff2 /app/assets/fonts/
ADD https://cdn.jsdelivr.net/npm/@fontsource/inter@5.1.0/files/inter-latin-800-normal.woff2 /app/assets/fonts/
ADD https://cdn.jsdelivr.net/npm/@mdi/font@7.4.47/css/materialdesignicons.min.css /app/assets/mdi/css/
ADD https://cdn.jsdelivr.net/npm/@mdi/font@7.4.47/fonts/materialdesignicons-webfont.woff2 /app/assets/mdi/fonts/

# TRMNL Framework 3.4.6 (MIT, github.com/usetrmnl/trmnl-framework, pinned commit) and its fonts
# (SIL OFL 1.1; BlockKie CC BY 3.0), served to the renderer at /trmnl/ and /fonts/.
ADD https://raw.githubusercontent.com/usetrmnl/trmnl-framework/07a0b39e41c9a702e3f21834853d68720f96c170/public/css/3.4.6/plugins.min.css /app/assets/trmnl/
ADD https://raw.githubusercontent.com/usetrmnl/trmnl-framework/07a0b39e41c9a702e3f21834853d68720f96c170/public/js/3.4.6/plugins.min.js /app/assets/trmnl/
ADD https://raw.githubusercontent.com/usetrmnl/trmnl-framework/07a0b39e41c9a702e3f21834853d68720f96c170/public/fonts/TRMNL12-Regular.woff2 /app/assets/trmnl-fonts/
ADD https://raw.githubusercontent.com/usetrmnl/trmnl-framework/07a0b39e41c9a702e3f21834853d68720f96c170/public/fonts/TRMNL12-Bold.woff2 /app/assets/trmnl-fonts/
ADD https://raw.githubusercontent.com/usetrmnl/trmnl-framework/07a0b39e41c9a702e3f21834853d68720f96c170/public/fonts/TRMNL16-Regular.woff2 /app/assets/trmnl-fonts/
ADD https://raw.githubusercontent.com/usetrmnl/trmnl-framework/07a0b39e41c9a702e3f21834853d68720f96c170/public/fonts/TRMNL16-Bold.woff2 /app/assets/trmnl-fonts/
ADD https://raw.githubusercontent.com/usetrmnl/trmnl-framework/07a0b39e41c9a702e3f21834853d68720f96c170/public/fonts/TRMNL21-Regular.woff2 /app/assets/trmnl-fonts/
ADD https://raw.githubusercontent.com/usetrmnl/trmnl-framework/07a0b39e41c9a702e3f21834853d68720f96c170/public/fonts/TRMNL21-Bold.woff2 /app/assets/trmnl-fonts/
ADD https://raw.githubusercontent.com/usetrmnl/trmnl-framework/07a0b39e41c9a702e3f21834853d68720f96c170/public/fonts/Inter.ttf /app/assets/trmnl-fonts/
ADD https://raw.githubusercontent.com/usetrmnl/trmnl-framework/07a0b39e41c9a702e3f21834853d68720f96c170/public/fonts/Inter-Italic.ttf /app/assets/trmnl-fonts/
ADD https://raw.githubusercontent.com/usetrmnl/trmnl-framework/07a0b39e41c9a702e3f21834853d68720f96c170/public/fonts/NicoClean-Regular.ttf /app/assets/trmnl-fonts/
ADD https://raw.githubusercontent.com/usetrmnl/trmnl-framework/07a0b39e41c9a702e3f21834853d68720f96c170/public/fonts/NicoPups-Regular.ttf /app/assets/trmnl-fonts/
ADD https://raw.githubusercontent.com/usetrmnl/trmnl-framework/07a0b39e41c9a702e3f21834853d68720f96c170/public/fonts/BlockKie.ttf /app/assets/trmnl-fonts/
# Vega-Lite for chart blocks (BSD-3-Clause), served at /vendor/.
ADD https://cdn.jsdelivr.net/npm/vega@5.30.0/build/vega.min.js /app/assets/vendor/
ADD https://cdn.jsdelivr.net/npm/vega-lite@5.21.0/build/vega-lite.min.js /app/assets/vendor/
ADD https://cdn.jsdelivr.net/npm/vega-embed@6.26.0/build/vega-embed.min.js /app/assets/vendor/

COPY *.py default_pages.json /app/
COPY assets/*.css /app/assets/

EXPOSE 8788 8799
CMD ["python", "server.py"]
