"""Command-line options and the end-of-run report for the test suite.

They live here, at the project root, because pytest always loads this file first. Declared in
``tests/conftest.py`` they would be unknown whenever pytest is pointed at a path outside ``tests/``
(for example the value of ``--report docs/...``). Fixtures stay in ``tests/conftest.py``.
"""

from __future__ import annotations

import pytest

from tests import report


def pytest_addoption(parser: pytest.Parser) -> None:
    parser.addoption("--test-db", action="store_true", default=False,
                     help="run the database tests against the separate test database <POSTGRES_DB>_test from .env "
                          "(created if missing); never the development database")
    report.add_option(parser)


def pytest_terminal_summary(terminalreporter: pytest.TerminalReporter, exitstatus: int, config: pytest.Config) -> None:
    report.terminal_summary(terminalreporter, config)
