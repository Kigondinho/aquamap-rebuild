"""Write the OpenAPI description of the API to a file.

    python -m app.openapi_export                 # writes docs/openapi.json
    python -m app.openapi_export --check         # exit 1 if docs/openapi.json is out of date

The running application serves the same document at /openapi.json and an interactive page at /docs
(both switched off when APP_ENV=production).
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

DEFAULT_PATH = Path(__file__).resolve().parents[1] / "docs" / "openapi.json"


def build() -> str:
    from app.main import create_app

    return json.dumps(create_app().openapi(), indent=2, sort_keys=True) + "\n"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m app.openapi_export", description=__doc__.split("\n")[0])
    parser.add_argument("--out", type=Path, default=DEFAULT_PATH)
    parser.add_argument("--check", action="store_true", help="compare with the file instead of writing it")
    args = parser.parse_args(argv)
    document = build()
    if args.check:
        if not args.out.exists() or args.out.read_text(encoding="utf-8") != document:
            print(f"{args.out} is out of date; run python -m app.openapi_export", file=sys.stderr)
            return 1
        print(f"{args.out} is up to date")
        return 0
    args.out.write_text(document, encoding="utf-8")
    print(f"wrote {args.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
