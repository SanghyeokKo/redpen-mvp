import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

DATA = ROOT / "data"
MU = "MU-0000723125-26-000023"
AMD = "AMD-0000002488-26-000191"
NVDA = "NVDA-0001045810-26-000075"


@pytest.fixture(scope="session")
def ctx():
    from redpen.workflow import load_context
    return load_context(DATA)


@pytest.fixture(scope="session")
def mu_result(ctx):
    from redpen.workflow import run
    return run(ctx, MU, record=False)
