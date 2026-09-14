FROM python:3.12-slim

ENV PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    FLY_ALLOW_BROWSER=1 \
    FLY_HOST=0.0.0.0 \
    MALLOC_ARENA_MAX=2

WORKDIR /app

# Install curl for runtime data download (python:3.12-slim doesn't include it)
RUN apt-get update && apt-get install -y --no-install-recommends curl \
    && rm -rf /var/lib/apt/lists/*

# Install Python dependencies (no data download here - done at runtime)
COPY requirements-roam.txt ./
RUN pip install -r requirements-roam.txt \
 && python -m playwright install --with-deps chromium

# Copy application (no data/ or build/ - fetched at runtime)
COPY *.py ./
COPY web/ web/

# Run the supervisor which handles data fetching
CMD ["python", "run_all.py"]
