# Load PyTorch's OpenMP runtime before sklearn/XGBoost on Apple Silicon.
import torch
import numpy as np
import pandas as pd
import pytest


@pytest.fixture(autouse=True)
def isolated_research_state(tmp_path, monkeypatch):
    """API tests must never import fixture bindings into the user's research DB."""
    monkeypatch.setenv('STOCK_RESEARCH_ROOT', str(tmp_path / 'research'))
    monkeypatch.setenv('STOCK_RESEARCH_SCHEDULE', '0')
    from stock_app.app import app
    monkeypatch.setitem(app.config, 'RESEARCH_BACKGROUND', False)


@pytest.fixture
def prices():
    return pd.Series(100 + np.arange(120, dtype=float),
                     index=pd.bdate_range("2020-01-01", periods=120), name="Close")
