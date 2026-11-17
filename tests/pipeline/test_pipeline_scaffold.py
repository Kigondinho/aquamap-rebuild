"""The pipeline package imports without the heavy geospatial libraries; the combined entry point runs the steps in order."""

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


def test_steps_run_in_order_and_stop_at_the_first_failure(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    from pipeline import run_pipeline

    calls: list[str] = []

    def fake(name: str, status: int):
        def step(data_dir: Path) -> int:
            assert data_dir == tmp_path
            calls.append(name)
            return status
        return step

    steps = [(name, purpose, fake(name, 1 if name == "load" else 0)) for name, purpose, _ in run_pipeline.STEPS]
    monkeypatch.setattr(run_pipeline, "STEPS", tuple(steps))
    assert run_pipeline.STEP_NAMES == ("acquire", "terrain", "load", "score")
    assert run_pipeline.run(tmp_path) == 1
    assert calls == ["acquire", "terrain", "load"]            # "score" never ran on incomplete inputs
    calls.clear()
    assert run_pipeline.run(tmp_path, start="score") == 0 and calls == ["score"]
    calls.clear()
    assert run_pipeline.run(tmp_path, only="terrain") == 0 and calls == ["terrain"]


def test_an_unexpected_error_stops_the_run(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    from pipeline import run_pipeline

    def broken(data_dir: Path) -> int:
        raise RuntimeError("disk full")

    monkeypatch.setattr(run_pipeline, "STEPS", (("acquire", "x", broken),))
    assert run_pipeline.run(tmp_path) == 1


def test_command_line_options() -> None:
    from pipeline import run_pipeline

    with pytest.raises(SystemExit):
        run_pipeline.main(["--from", "terrain", "--only", "score"])   # mutually exclusive
    with pytest.raises(SystemExit):
        run_pipeline.main(["--only", "nonsense"])
