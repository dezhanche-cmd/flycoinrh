# flylife - a real fruit fly brain roaming the internet with random lifespan
#
# The connectome is downloaded from Google Cloud Storage during build time.
# The raw feather files are ~1.1GB but graph.npz is much smaller (~200MB).

FROM python:3.12-slim

ENV PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    FLY_ALLOW_BROWSER=1 \
    FLY_HOST=0.0.0.0

WORKDIR /app

# Install Python dependencies
COPY requirements-roam.txt ./
RUN pip install -r requirements-roam.txt \
 && python -m playwright install --with-deps chromium

# Download the connectome data from Google Cloud Storage and build graph.npz
RUN apt-get update && apt-get install -y --no-install-recommends wget ca-certificates \
 && mkdir -p /tmp/connectome \
 && wget -q -O /tmp/connectome/connectome-weights.feather "https://storage.googleapis.com/flyem-male-cns/v1.0/connectome-data/connectome-weights.feather" \
 && wget -q -O /tmp/connectome/body-neurotransmitters.feather "https://storage.googleapis.com/flyem-male-cns/v1.0/connectome-data/body-neurotransmitters.feather" \
 && wget -q -O /tmp/connectome/body-annotations.feather "https://storage.googleapis.com/flyem-male-cns/v1.0/connectome-data/body-annotations.feather" \
 && mkdir -p data && cp /tmp/connectome/*.feather data/ \
 && python build_graph.py \
 && rm -rf /tmp/connectome data/*.feather \
 && apt-get remove -y wget ca-certificates \
 && apt-get autoremove -y \
 && rm -rf /var/lib/apt/lists/*

# Build mb_sides.json (required by mushroom body learning)
RUN python mb_sides.py 2>/dev/null || true

# Copy application
COPY *.py ./
COPY web/ web/

# roam.py serves itself, supervised by run_all.py
CMD ["python", "run_all.py"]
