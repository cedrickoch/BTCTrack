FROM python:3.12-slim

ENV PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    BTCTRACK_DB_PATH=/app/data/btctrack.db

WORKDIR /app

COPY pyproject.toml ./
COPY src ./src
COPY scripts ./scripts

# Build-time price snapshot: download once from Yahoo Finance (no API key)
# and bake the CSV into the source tree so it ships inside the installed
# package. The running container never makes outbound HTTP for prices.
RUN pip install --upgrade pip && pip install httpx \
 && mkdir -p src/btctrack/prices/data \
 && python scripts/build_price_snapshot.py \
        --output src/btctrack/prices/data/btc_prices.csv \
 && pip install .

RUN mkdir -p /app/data
VOLUME ["/app/data"]

EXPOSE 8501

CMD ["streamlit", "run", "src/btctrack/ui/app.py", \
     "--server.address=0.0.0.0", \
     "--server.port=8501", \
     "--server.headless=true", \
     "--browser.gatherUsageStats=false"]
