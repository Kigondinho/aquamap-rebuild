"""Offline geospatial processing (GeoPandas, Rasterio, NumPy).

Run by an administrator from the command line, never inside a web request:

    python -m pipeline.run_pipeline

Heavy libraries are imported inside functions (or under ``TYPE_CHECKING``) so that this package
imports cleanly in environments that only have the web-app requirements installed.
Phase 2 scaffold: signatures and docstrings only; the steps are implemented in later phases.
"""
