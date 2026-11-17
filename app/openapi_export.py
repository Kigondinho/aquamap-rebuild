"""Write the OpenAPI description of the API to a file.

    python -m app.openapi_export                 # writes docs/openapi.json
    python -m app.openapi_export --check         # exit 1 if docs/openapi.json is out of date

The running application serves the same document at /openapi.json and an interactive page at /docs
(both switched off when APP_ENV=production).

The document is generated in a separate process with a clean, fixed configuration: no .env, throw-away
secrets, default settings. So it needs no real secrets (it works in CI) and no local setting, such as
a changed cookie name, can make the committed file differ from machine to machine.
"""

from __future__ import annotations

import argparse
import json
import os
import secrets
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_PATH = ROOT / "docs" / "openapi.json"
_GENERATE = "import json; from app.main import create_app; print(json.dumps(create_app().openapi(), indent=2, sort_keys=True))"


def build() -> str:
    env = {
        "PATH": os.environ.get("PATH", ""),
        "PYTHONPATH": str(ROOT),
        "APP_ENV": "development",
        "SECRET_KEY": secrets.token_urlsafe(48),          # required to start; never stored or used
        "POSTGRES_PASSWORD": secrets.token_urlsafe(24),   # no database connection is made
    }
    with tempfile.TemporaryDirectory() as empty:          # a working directory without .env
        result = subprocess.run([sys.executable, "-c", _GENERATE], cwd=empty, env=env, capture_output=True, text=True,
                                check=False)
    if result.returncode != 0:
        raise RuntimeError(f"could not generate the OpenAPI document:\n{result.stderr.strip()[-2000:]}")
    return result.stdout


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
