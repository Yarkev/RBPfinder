import pathlib

import pytest

from rbpfinder.config import Rules

ROOT = pathlib.Path(__file__).resolve().parent.parent


@pytest.fixture(scope="session")
def rules():
    return Rules(ROOT / "rules" / "decision_rules.yaml")
