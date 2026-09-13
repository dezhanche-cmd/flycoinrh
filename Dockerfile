FROM python:3.12-slim

ENV PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    FLY_ALLOW_BROWSER=1 \
    FLY_HOST=0.0.0.0 \
    MALLOC_ARENA_MAX=2

WORKDIR /app

COPY requirements-roam.txt ./
RUN pip install -r requirements-roam.txt \
 && python -m playwright install --with-deps chromium

# Download connectome data and build graph.npz in one step
# Raw data ~1.1GB, output graph.npz ~200MB
RUN apt-get update && apt-get install -y --no-install-recommends \
    wget ca-certificates \
    && mkdir -p /tmp/connectome data \
    && echo "Downloading connectome data..." \
    && wget -q --timeout=120 --tries=3 -O /tmp/connectome/connectome-weights.feather \
       "https://storage.googleapis.com/flyem-male-cns/v1.0/connectome-data/connectome-weights.feather" \
    && wget -q --timeout=120 --tries=3 -O /tmp/connectome/body-neurotransmitters.feather \
       "https://storage.googleapis.com/flyem-male-cns/v1.0/connectome-data/body-neurotransmitters.feather" \
    && wget -q --timeout=120 --tries=3 -O /tmp/connectome/body-annotations.feather \
       "https://storage.googleapis.com/flyem-male-cns/v1.0/connectome-data/body-annotations.feather" \
    && cp /tmp/connectome/*.feather data/ \
    && echo "Building graph..." \
    && python build_graph.py \
    && echo "Cleaning up..." \
    && rm -rf /tmp/connectome data/*.feather \
    && ls -lh build/graph.npz \
    && apt-get remove -y wget ca-certificates \
    && apt-get autoremove -y \
    && rm -rf /var/lib/apt/lists/*

# Build mb_sides.json (required by mushroom body)
RUN python mb_sides.py 2>/dev/null || true

COPY *.py ./
COPY web/ web/

CMD ["python", "run_all.py"]
