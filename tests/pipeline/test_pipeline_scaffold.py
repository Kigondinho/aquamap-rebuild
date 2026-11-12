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


def test_steps_raise_not_implemented(tmp_path: Path) -> None:
    from pipeline import compute_metrics

    # fetch_data (Phase 4), prepare_dem (Phase 5) and load_vectors (Phase 6) are implemented; see their own tests
    with pytest.raises(NotImplementedError):
        compute_metrics.add_length(None)  # type: ignore[arg-type]


def test_entry_point_exits_non_zero_and_changes_nothing() -> None:
    from pipeline.run_pipeline import main

    assert main() == 2
