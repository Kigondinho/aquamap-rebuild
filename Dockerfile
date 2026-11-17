# AquaMap application image (web app; the pipeline libraries are optional).
#
#   docker compose up -d --build                       # normal use: see README "Quick start"
#   docker build -t aquamap-app .                      # web app only
#   docker build --build-arg INSTALL_PIPELINE=true -t aquamap-pipeline .
#
# The base image is pinned by digest so that a rebuild uses exactly the same Python and Debian.
# To update it, see docs/phase13_deployment.md ("Updating pinned versions").
FROM python:3.12-slim@sha256:5e2bdb1b0d542d5039cffb8181c65930093949194a65c365dfb1b0fe13a4fa5b

ARG INSTALL_PIPELINE=false

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    PIP_ROOT_USER_ACTION=ignore

WORKDIR /app

# Dependencies first, so code changes do not reinstall them. Versions are pinned in the files.
# The pipeline's rasterio wheel bundles GDAL but needs the system's libexpat, which the slim image lacks.
COPY requirements.txt requirements-pipeline.txt ./
RUN pip install -r requirements.txt \
    && if [ "$INSTALL_PIPELINE" = "true" ]; then \
         apt-get update \
         && apt-get install -y --no-install-recommends libexpat1 \
         && rm -rf /var/lib/apt/lists/* \
         && pip install -r requirements-pipeline.txt \
         && python -c "import geopandas, numpy, osmnx, pyproj, rasterio, shapely"; \
       fi

# Run as an unprivileged user. Only what the application needs is copied: no tests, docs, data or secrets.
RUN useradd --create-home --uid 1000 aquamap && mkdir -p /app/data && chown aquamap:aquamap /app/data
COPY --chown=aquamap:aquamap app ./app
COPY --chown=aquamap:aquamap core ./core
COPY --chown=aquamap:aquamap pipeline ./pipeline
COPY --chown=aquamap:aquamap db ./db
COPY --chown=aquamap:aquamap static ./static
COPY --chown=aquamap:aquamap docker/entrypoint.sh ./docker/entrypoint.sh
USER aquamap

EXPOSE 8000
# Healthy = the web server answers AND it can reach the database.
HEALTHCHECK --interval=10s --timeout=5s --start-period=40s --retries=5 \
  CMD python -c "import json,sys,urllib.request; r=json.load(urllib.request.urlopen('http://127.0.0.1:8000/api/v1/health', timeout=4)); sys.exit(0 if r['database'] else 1)"

ENTRYPOINT ["sh", "/app/docker/entrypoint.sh"]
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
