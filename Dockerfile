FROM python:3.13-slim AS runtime

RUN apt-get update && apt-get install -y --no-install-recommends \
    curl && \
    rm -rf /var/lib/apt/lists/*

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY main.py .

HEALTHCHECK --interval=5m --timeout=5s --retries=2 \
    CMD python -c "import sys; sys.exit(0)"

# RUN_ONCE und CHECK_INTERVAL werden per ENV gesetzt
ENV RUN_ONCE=false CHECK_INTERVAL=60

CMD ["python", "main.py"]