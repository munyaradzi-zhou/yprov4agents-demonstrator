"""Root-level pytest configuration.

Only command-line options live here. pytest parses arguments before collection, so
``pytest_addoption`` is honoured only in a conftest at the rootdir — the directory holding
pyproject.toml — and is silently ignored in ``tests/conftest.py``. Fixtures and helpers
stay in ``tests/conftest.py`` where they belong.
"""

from __future__ import annotations

import pytest


def pytest_addoption(parser: pytest.Parser) -> None:
    parser.addoption(
        "--prov-dir",
        default="out",
        help="directory holding emitted *_prov.json artifacts (default: out). Point it at "
        "an archive under experiments/ to test a frozen run.",
    )
