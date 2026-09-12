# Load PyTorch's OpenMP runtime before sklearn/XGBoost on Apple Silicon.
import torch
import numpy as np
import pandas as pd
import pytest


@pytest.fixture
def prices():
    return pd.Series(100 + np.arange(120, dtype=float),
                     index=pd.bdate_range("2020-01-01", periods=120), name="Close")
