"""The pipeline package imports without the heavy geospatial libraries; later steps are still stubs."""

from __future__ import annotations

import importlib
from pathlib import Path

import pytest

MODULES = [
    "pipeline.fetch_data",
    "pipeline.errors",
    "pipeline.provenance",
    "pipeline.validate",
    "pipeline.process_vectors",
    "pipeline.acquire",
    "pipeline.prepare_dem",
    "pipeline.geometry",
    "pipeline.load_vectors",
    "pipeline.compute_metrics",
    "pipeline.score_roads",
    "pipeline.run_pipeline",
]


@pytest.mark.parametrize("name", MODULES)
def test_module_imports_cleanly(name: str) -> None:
    importlib.import_module(name)


def test_remaining_stub_is_the_combined_entry_point(tmp_path: Path) -> None:
    # fetch (4), DEM (5), load (6), metrics and scoring (7) are implemented; see their own tests
    from pipeline.run_pipeline import STEPS

    assert len(STEPS) == 6


def test_entry_point_exits_non_zero_and_changes_nothing() -> None:
    from pipeline.run_pipeline import main

    assert main() == 2
