# Plain Python base. Needs DejaVu fonts for e-paper text rendering (Pillow).
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
COPY server.py /app/server.py
RUN pip install "mcp>=1.27.2" "httpx>=0.27" "pillow>=10.0" "aioesphomeapi>=24.0"

EXPOSE 8788 8799
CMD ["python", "server.py"]
