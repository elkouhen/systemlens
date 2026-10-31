from __future__ import annotations

import re
from pathlib import Path

import pytest

MODELS = sorted((Path(__file__).parents[1] / "docs" / "models").glob("*.html"))
HOME_DIRECTORY = re.compile(r"/Users/|/home/|[A-Za-z]:\\\\Users\\\\")


@pytest.mark.parametrize("model", MODELS, ids=lambda path: path.name)
def test_checked_in_documentation_models_do_not_disclose_home_directories(model: Path) -> None:
    """The documentation site must not ship a contributor's local paths."""
    match = HOME_DIRECTORY.search(model.read_text(encoding="utf-8"))
    assert match is None, f"{model.name} contains a home directory near offset {match.start()}"


def test_documentation_models_are_present() -> None:
    assert MODELS
