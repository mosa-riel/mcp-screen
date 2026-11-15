# Plain Python base + headless Chromium. Fonts (Inter) and icons (Material Design Icons)
# are fetched once at build time into assets/, so rendering needs no internet.
FROM python:3.13-slim

ENV MCP_HOST=0.0.0.0 \
    MCP_PORT=8788 \
    SCREEN_PORT=8799 \
    RETERMINAL_HOST=reterminal-e1001.local \
    PIP_NO_CACHE_DIR=1 \
    PYTHONUNBUFFERED=1

RUN apt-get update \
    && apt-get install -y --no-install-recommends fonts-dejavu-core \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app
RUN pip install "mcp>=1.27.2" "httpx>=0.27" "pillow>=10.0" "aioesphomeapi>=24.0" "playwright>=1.40" "websockets>=13"
# Headless Chromium for rendering (HTML/CSS → 1-bit screen). --with-deps apt-installs the
# browser's system libraries. Big layer (~400MB) — that's the cost of full HTML rendering.
RUN playwright install --with-deps chromium

ADD https://cdn.jsdelivr.net/npm/@fontsource/inter@5.1.0/files/inter-latin-500-normal.woff2 /app/assets/fonts/
ADD https://cdn.jsdelivr.net/npm/@fontsource/inter@5.1.0/files/inter-latin-700-normal.woff2 /app/assets/fonts/
ADD https://cdn.jsdelivr.net/npm/@fontsource/inter@5.1.0/files/inter-latin-800-normal.woff2 /app/assets/fonts/
ADD https://cdn.jsdelivr.net/npm/@mdi/font@7.4.47/css/materialdesignicons.min.css /app/assets/mdi/css/
ADD https://cdn.jsdelivr.net/npm/@mdi/font@7.4.47/fonts/materialdesignicons-webfont.woff2 /app/assets/mdi/fonts/

COPY *.py default_pages.json /app/
COPY assets/*.css /app/assets/

EXPOSE 8788 8799
CMD ["python", "server.py"]
