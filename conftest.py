"""Command-line options and the end-of-run report for the test suite.

They live here, at the project root, so that pytest knows them however it is started. Fixtures stay in
``tests/conftest.py``.

Write the report option as ONE word, ``--report=FILE``. pytest reads the command line before it knows
this option; given as two words, an existing FILE is mistaken for a test path, which can stop pytest
from finding the project.
"""

from __future__ import annotations

import pytest

from tests import report


def pytest_addoption(parser: pytest.Parser) -> None:
    parser.addoption("--test-db", action="store_true", default=False,
                     help="run the database tests against the separate test database <POSTGRES_DB>_test from .env "
                          "(created if missing); never the development database")
    report.add_option(parser)
    parser.addoption("--require-all", action="store_true", default=False,
                     help="fail the run if any test was skipped (unverified); used in CI so that nothing is "
                          "quietly left out")


@pytest.hookimpl(tryfirst=True)
def pytest_sessionfinish(session: pytest.Session, exitstatus: int) -> None:
    if not session.config.getoption("--require-all") or exitstatus != 0:
        return
    reporter = session.config.pluginmanager.get_plugin("terminalreporter")
    if reporter is not None and reporter.stats.get("skipped"):
        session.exitstatus = pytest.ExitCode.TESTS_FAILED


def pytest_terminal_summary(terminalreporter: pytest.TerminalReporter, exitstatus: int, config: pytest.Config) -> None:
    report.terminal_summary(terminalreporter, config)
