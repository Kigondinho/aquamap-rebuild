# AquaMap application image.
#   Web app only:          docker build -t aquamap .
#   With pipeline libs:    docker build --build-arg INSTALL_PIPELINE=true -t aquamap-pipeline .
FROM python:3.12-slim

ARG INSTALL_PIPELINE=false

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1

WORKDIR /app

COPY requirements.txt requirements-pipeline.txt ./
RUN pip install -r requirements.txt \
    && if [ "$INSTALL_PIPELINE" = "true" ]; then pip install -r requirements-pipeline.txt; fi

RUN useradd --create-home --uid 1000 aquamap
COPY --chown=aquamap:aquamap . .
USER aquamap

EXPOSE 8000
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
