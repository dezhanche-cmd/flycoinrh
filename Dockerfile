FROM python:3.12-slim

ENV PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    FLY_ALLOW_BROWSER=1 \
    FLY_HOST=0.0.0.0

WORKDIR /app

COPY requirements-roam.txt ./
RUN pip install -r requirements-roam.txt \
 && python -m playwright install --with-deps chromium

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

RUN python mb_sides.py 2>/dev/null || true

COPY *.py ./
COPY web/ web/

CMD ["python", "run_all.py"]
