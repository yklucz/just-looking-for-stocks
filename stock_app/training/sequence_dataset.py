"""Raw-candle adjacency is retained; targets never enter sequence features."""
from dataclasses import dataclass

import numpy as np
import pandas as pd
import torch
from torch.utils.data import Dataset

from ..models.base import validate_x


@dataclass(frozen=True)
class SequenceInput:
    history: pd.DataFrame
    origins: pd.DatetimeIndex
    sequence_length: int
    scaler_rows: pd.DataFrame | None = None

    def __post_init__(self) -> None:
        if type(self.sequence_length) is not int or self.sequence_length < 1:
            raise ValueError("Positive sequence length required")
        if (not isinstance(self.history.index, pd.DatetimeIndex) or self.history.index.hasnans
                or not self.history.index.is_unique or not self.history.index.is_monotonic_increasing
                or not self.origins.is_unique or not self.origins.is_monotonic_increasing or not len(self.origins)):
            raise ValueError("Chronological unique sequence timestamps required")
        positions = self.history.index.get_indexer(self.origins)
        valid = valid_sequence_origins(self.history, self.sequence_length)
        if (positions < 0).any() or not valid.iloc[positions].all():
            raise ValueError("Sequence contains insufficient history, invalid rows, or missing origin")
        validate_x(self.history.loc[self.origins])
        if self.scaler_rows is not None:
            validate_x(self.scaler_rows)
            if (tuple(self.scaler_rows.columns) != tuple(self.history.columns)
                    or self.scaler_rows.index[-1] > self.origins[-1]
                    or not self.scaler_rows.index.isin(self.history.index).all()):
                raise ValueError("Invalid training-only scaler rows")

    @property
    def index(self):
        return self.origins

    @property
    def columns(self):
        return self.history.columns

    def __len__(self):
        return len(self.origins)

    @property
    def positions(self):
        return self.history.index.get_indexer(self.origins)


def valid_sequence_origins(history: pd.DataFrame, length: int) -> pd.Series:
    finite = pd.Series(np.isfinite(history.to_numpy(dtype=float)).all(axis=1), index=history.index)
    return finite.rolling(length, min_periods=length).sum().eq(length)


class SequenceDataset(Dataset):
    def __init__(self, inputs: SequenceInput, scaler, targets: pd.Series | None = None):
        self.origins = inputs.origins
        self.positions = inputs.positions
        self.sequence_length = inputs.sequence_length
        # Transform only past/context rows needed by this partition, never a later tail.
        history = inputs.history.iloc[:self.positions[-1]+1]
        finite = np.isfinite(history.to_numpy()).all(axis=1)
        values = np.full(history.shape, np.nan, dtype=np.float32)
        values[finite] = scaler.transform(history.iloc[np.flatnonzero(finite)]).astype(np.float32)
        self.values = torch.from_numpy(values)
        if targets is not None and not targets.index.equals(inputs.index):
            raise ValueError("Sequence targets must align exactly with origins")
        self.targets = None if targets is None else torch.tensor(targets.to_numpy(), dtype=torch.float32)

    def __len__(self):
        return len(self.positions)

    def __getitem__(self, index):
        end = self.positions[index]+1
        sample = self.values[end-self.sequence_length:end]
        return sample if self.targets is None else (sample, self.targets[index])
