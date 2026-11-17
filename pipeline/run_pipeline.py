"""Run the whole data pipeline in order: ``python -m pipeline.run_pipeline``.

    1. acquire      download boundary, roads, rivers and SRTM (skipped for files already in data/raw),
                    write cleaned EPSG:32737 copies, validate everything        (pipeline.acquire all)
    2. terrain      reproject the DEM and compute slope in percent             (pipeline.prepare_dem)
    3. load         load roads and rivers into PostGIS and verify them          (pipeline.load_vectors)
    4. score        measure every eligible road, compute the IVI, store it     (pipeline.score_roads)

Each step is the same command an operator can run on its own. The run stops at the first step that
fails and exits with status 1, so later steps never work on incomplete inputs. Re-running is safe:
downloads already on disk are reused, loading and scoring are idempotent.

    python -m pipeline.run_pipeline                    # everything
    python -m pipeline.run_pipeline --from terrain     # start at a later step (inputs already present)
    python -m pipeline.run_pipeline --only score       # one step
"""

from __future__ import annotations

import argparse
import logging
import os
import sys
import time
from collections.abc import Callable
from pathlib import Path

logger = logging.getLogger("aquamap.pipeline")


def _acquire(data_dir: Path) -> int:
    from pipeline import acquire

    return acquire.main(["--data-dir", str(data_dir), "all"])


def _terrain(data_dir: Path) -> int:
    from pipeline import prepare_dem

    return prepare_dem.main(["--data-dir", str(data_dir), "--force"])


def _load(data_dir: Path) -> int:
    from pipeline import load_vectors

    return load_vectors.main(["--data-dir", str(data_dir)])


def _score(data_dir: Path) -> int:
    from pipeline import score_roads

    return score_roads.main(["--data-dir", str(data_dir)])


# name, what it does, function. Order matters.
STEPS: tuple[tuple[str, str, Callable[[Path], int]], ...] = (
    ("acquire", "download, clean and validate source data", _acquire),
    ("terrain", "reproject the DEM and compute slope", _terrain),
    ("load", "load roads and rivers into PostGIS", _load),
    ("score", "measure, score and store every eligible road", _score),
)
STEP_NAMES = tuple(name for name, _, _ in STEPS)


def run(data_dir: Path, *, start: str | None = None, only: str | None = None) -> int:
    """Run the selected steps in order. Returns 0 when all succeed, 1 at the first failure."""
    names = [only] if only else list(STEP_NAMES[STEP_NAMES.index(start):] if start else STEP_NAMES)
    selected = [step for step in STEPS if step[0] in names]
    for number, (name, purpose, func) in enumerate(selected, start=1):
        logger.info("step %d/%d: %s (%s)", number, len(selected), name, purpose)
        began = time.monotonic()
        try:
            status = func(data_dir)
        except SystemExit as exc:  # a step's argument parser may exit
            status = int(exc.code or 0)
        except Exception:  # noqa: BLE001 - report which step broke, then stop
            logger.exception("step %s failed with an unexpected error; stopping", name)
            return 1
        if status != 0:
            logger.error("step %s failed (exit status %s); stopping. Fix the problem above and run again "
                         "(add --from %s to skip the steps that already succeeded).", name, status, name)
            return 1
        logger.info("step %s finished in %.0f s", name, time.monotonic() - began)
    logger.info("pipeline finished: %s", " -> ".join(name for name, _, _ in selected))
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m pipeline.run_pipeline", description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--data-dir", type=Path, default=Path(os.environ.get("DATA_DIR", "data")))
    group = parser.add_mutually_exclusive_group()
    group.add_argument("--from", dest="start", choices=STEP_NAMES, help="start at this step")
    group.add_argument("--only", choices=STEP_NAMES, help="run just this step")
    args = parser.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    return run(args.data_dir, start=args.start, only=args.only)


if __name__ == "__main__":
    sys.exit(main())
