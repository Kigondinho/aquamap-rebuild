"""Create the local configuration file ``.env`` from ``.env.example`` with fresh random secrets.

    python3 scripts/init_env.py                  # first time after cloning
    python3 scripts/init_env.py --app-port 8080  # if port 8000 is taken on this machine
    python3 scripts/init_env.py --force          # replace an existing .env (see the warning below)

Only the Python standard library is used, so this runs before anything is installed.

* SECRET_KEY and POSTGRES_PASSWORD get new random values; every other setting keeps the example's default.
* HOST_UID / HOST_GID are set to the current user, so the pipeline container writes data/ as you.
* The file is created readable by its owner only, and .env is git-ignored: it never reaches the repository.

Warning: the database keeps the password it was created with. After replacing .env with --force on a
machine that already has the database volume, either reset the database (``docker compose down -v``,
which DELETES ITS DATA) or put the old POSTGRES_PASSWORD back.
"""

from __future__ import annotations

import argparse
import os
import re
import secrets
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def render(template: str, values: dict[str, str]) -> str:
    """Replace ``KEY=...`` lines (commented or not) with the given values; append keys the template lacks."""
    done: set[str] = set()
    lines = []
    for line in template.splitlines():
        match = re.match(r"^#?\s*([A-Z][A-Z0-9_]*)=", line)
        if match and match.group(1) in values and match.group(1) not in done:
            key = match.group(1)
            lines.append(f"{key}={values[key]}")
            done.add(key)
        else:
            lines.append(line)
    missing = [key for key in values if key not in done]
    if missing:
        lines += ["", "# Added by scripts/init_env.py"] + [f"{key}={values[key]}" for key in missing]
    return "\n".join(lines) + "\n"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python3 scripts/init_env.py", description=__doc__.split("\n")[0])
    parser.add_argument("--force", action="store_true", help="replace an existing .env")
    parser.add_argument("--app-port", type=int, help="host port for the web app (default 8000)")
    parser.add_argument("--db-port", type=int, help="host port for PostgreSQL (default 5433)")
    parser.add_argument("--env-file", type=Path, default=ROOT / ".env", help=argparse.SUPPRESS)
    args = parser.parse_args(argv)

    target: Path = args.env_file
    if target.exists() and not args.force:
        print(f"{target} already exists; nothing changed. Use --force to replace it (read the warning in --help first).",
              file=sys.stderr)
        return 1
    template = (ROOT / ".env.example").read_text(encoding="utf-8")
    values = {
        "SECRET_KEY": secrets.token_urlsafe(48),
        "POSTGRES_PASSWORD": secrets.token_urlsafe(24),
        "HOST_UID": str(os.getuid()) if hasattr(os, "getuid") else "1000",
        "HOST_GID": str(os.getgid()) if hasattr(os, "getgid") else "1000",
    }
    if args.app_port:
        values["APP_PORT"] = str(args.app_port)
    if args.db_port:
        values["POSTGRES_PORT"] = str(args.db_port)
    content = render(template, values)

    # Create the file readable by the owner only, before any secret is written into it.
    flags = os.O_WRONLY | os.O_CREAT | os.O_TRUNC
    descriptor = os.open(target, flags, 0o600)
    with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
        handle.write(content)
    os.chmod(target, 0o600)
    print(f"Wrote {target} with new random SECRET_KEY and POSTGRES_PASSWORD (readable by you only).")
    print("Next: docker compose up -d --build --wait")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
