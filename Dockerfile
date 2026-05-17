FROM python:3.12-slim AS builder

ENV PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    PIP_NO_COMPILE=1

WORKDIR /app

RUN python -m venv /opt/venv
ENV PATH="/opt/venv/bin:$PATH"

COPY pyproject.toml ./
COPY src ./src
COPY scripts ./scripts

RUN pip install httpx \
 && mkdir -p src/btctrack/prices/data \
 && python scripts/build_price_snapshot.py \
        --output src/btctrack/prices/data/btc_prices.csv \
 && pip install . \
 && find /opt/venv -type d -name __pycache__ -exec rm -rf {} + \
 && find /opt/venv -type f -name '*.pyc' -delete


FROM python:3.12-slim AS runtime

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    BTCTRACK_DB_PATH=/app/data/btctrack.db \
    PATH="/opt/venv/bin:$PATH"

WORKDIR /app

COPY --from=builder /opt/venv /opt/venv
COPY .streamlit ./.streamlit

RUN mkdir -p /app/data
VOLUME ["/app/data"]

EXPOSE 8501

CMD ["streamlit", "run", "/opt/venv/lib/python3.12/site-packages/btctrack/ui/app.py", \
     "--server.address=0.0.0.0", \
     "--server.port=8501", \
     "--server.headless=true", \
     "--browser.gatherUsageStats=false"]
