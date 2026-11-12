"""Command-line entry point: ``python -m pipeline.run_pipeline``."""

from __future__ import annotations

import logging
import sys

logger = logging.getLogger("aquamap.pipeline")

STEPS: tuple[str, ...] = (
    "fetch source data",
    "prepare DEM and slope",
    "clean and load vectors",
    "compute metrics",
    "score roads",
    "persist and verify",
)


def main() -> int:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    logger.info("AquaMap pipeline steps: %s", " -> ".join(STEPS))
    logger.error("The pipeline is not implemented yet (later phase). Nothing was changed.")
    return 2


if __name__ == "__main__":
    sys.exit(main())
