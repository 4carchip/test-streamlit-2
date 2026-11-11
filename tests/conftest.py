import sys
from pathlib import Path

import pytest
import streamlit

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))


@pytest.fixture(autouse=True)
def fake_session_state(monkeypatch):
    monkeypatch.setattr(streamlit, "session_state", {})
