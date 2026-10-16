from __future__ import annotations

import re
from pathlib import Path

import aioamocrm

PYPROJECT = Path(__file__).resolve().parent.parent / "pyproject.toml"


def test_the_package_version_matches_pyproject() -> None:
    match = re.search(r'^version = "([^"]+)"$', PYPROJECT.read_text(encoding="utf-8"), re.M)
    assert match is not None
    assert aioamocrm.__version__ == match.group(1) == "0.1.0"


def test_the_public_api_is_exported() -> None:
    for name in aioamocrm.__all__:
        assert hasattr(aioamocrm, name), name
    assert "AmoCRMClient" in aioamocrm.__all__
