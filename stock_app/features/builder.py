"""Reusable unscaled features. No target or model dependency enters calculation."""

from dataclasses import asdict, dataclass

import numpy as np
import pandas as pd

from ..config import FeatureConfig
from .candle import build_candle
from .common import FeatureColumns
from .momentum import build_momentum
from .returns import build_returns
from .time import build_time
from .trend import build_trend
from .validation import validate_history
from .volatility import build_volatility
from .volume import build_volume


@dataclass(frozen=True)
class FeatureResult:
    all_features: pd.DataFrame
    valid_mask: pd.Series
    lookbacks: dict[str, int]
    recursive_features: tuple[str, ...]
    skipped_groups: tuple[str, ...]
    config: FeatureConfig

    @property
    def frame(self) -> pd.DataFrame:
        """Model-valid rows only; diagnostic all_features retains every timestamp."""
        return self.all_features.loc[
            self.valid_mask
        ].copy()

    @property
    def feature_names(self) -> tuple[str, ...]:
        return tuple(
            str(column)
            for column in self.all_features.columns
        )

    @property
    def max_lookback(self) -> int:
        """Minimum startup candles, NOT a finite memory bound for recursive indicators."""
        return max(
            self.lookbacks.values()
        )

    @property
    def warmup_rows(self) -> int:
        return min(
            len(self.all_features),
            self.max_lookback - 1,
        )

    @property
    def metadata(self) -> dict:
        return {
            "version": "features-v1",
            "feature_names": list(
                self.feature_names
            ),
            "feature_config": asdict(
                self.config
            ),
            "required_lookback": (
                self.max_lookback
            ),
            "lookbacks": (
                self.lookbacks.copy()
            ),
            "recursive_features": list(
                self.recursive_features
            ),
            "recursive_history": (
                "all supplied history "
                "from a consistent starting point"
            ),
            "skipped_groups": list(
                self.skipped_groups
            ),
            "warmup_rows": (
                self.warmup_rows
            ),
            "invalid_rows_after_warmup": (
                int(
                    (
                        ~self.valid_mask
                    ).sum()
                )
                - self.warmup_rows
            ),
        }


def _add_raw_features(
    columns: FeatureColumns,
    frame: pd.DataFrame,
) -> None:
    """Add the validated raw market columns as lowercase features."""
    for column in frame.columns:
        # Pandas types column labels as Hashable.
        # Validation guarantees these are known string names.
        name = str(column)

        columns[
            name.lower()
        ] = (
            frame[name],
            1,
        )


def _add_price_features(
    columns: FeatureColumns,
    frame: pd.DataFrame,
    config: FeatureConfig,
) -> None:
    """Add return, trend and momentum features."""
    close = frame["Close"]

    if config.returns:
        columns.update(
            build_returns(
                close,
                config,
            )
        )

    if config.trend:
        columns.update(
            build_trend(
                close,
                config,
            )
        )

    if config.momentum:
        columns.update(
            build_momentum(
                close,
                config,
            )
        )


def _add_volatility_features(
    columns: FeatureColumns,
    skipped: list[str],
    frame: pd.DataFrame,
    config: FeatureConfig,
) -> None:
    if not config.volatility:
        return

    columns.update(
        build_volatility(
            frame,
            config,
        )
    )

    if not {
        "High",
        "Low",
    }.issubset(frame.columns):
        skipped.append(
            "true_range_atr"
        )


def _add_candle_features(
    columns: FeatureColumns,
    skipped: list[str],
    frame: pd.DataFrame,
    config: FeatureConfig,
) -> None:
    if not config.candle:
        return

    required = {
        "Open",
        "High",
        "Low",
    }

    if required.issubset(
        frame.columns
    ):
        columns.update(
            build_candle(frame)
        )
        return

    skipped.append(
        "candle"
    )


def _add_volume_features(
    columns: FeatureColumns,
    skipped: list[str],
    frame: pd.DataFrame,
    config: FeatureConfig,
) -> None:
    if not config.volume:
        return

    if "Volume" in frame:
        columns.update(
            build_volume(
                frame["Volume"],
                config,
            )
        )
        return

    skipped.append(
        "volume"
    )


def _add_time_features(
    columns: FeatureColumns,
    frame: pd.DataFrame,
    config: FeatureConfig,
) -> None:
    if not config.time:
        return

    # validate_history guarantees timestamps are chronological,
    # but DataFrame.index remains statically typed as Index[Any].
    index = pd.DatetimeIndex(
        frame.index
    )

    columns.update(
        build_time(
            index,
            config,
        )
    )


def _build_feature_columns(
    frame: pd.DataFrame,
    config: FeatureConfig,
) -> tuple[
    FeatureColumns,
    list[str],
]:
    columns: FeatureColumns = {}
    skipped: list[str] = []

    if config.raw:
        _add_raw_features(
            columns,
            frame,
        )

    _add_price_features(
        columns,
        frame,
        config,
    )

    _add_volatility_features(
        columns,
        skipped,
        frame,
        config,
    )

    _add_candle_features(
        columns,
        skipped,
        frame,
        config,
    )

    _add_volume_features(
        columns,
        skipped,
        frame,
        config,
    )

    _add_time_features(
        columns,
        frame,
        config,
    )

    return (
        columns,
        skipped,
    )


def _feature_frame(
    columns: FeatureColumns,
    index: pd.Index,
) -> pd.DataFrame:
    values = pd.DataFrame(
        {
            name: value
            for name, (
                value,
                _,
            ) in columns.items()
        },
        index=index,
    )

    return (
        values
        .astype(float)
        .replace(
            [
                np.inf,
                -np.inf,
            ],
            np.nan,
        )
    )


def _recursive_features(
    columns: FeatureColumns,
) -> tuple[str, ...]:
    return tuple(
        name
        for name in columns
        if (
            "ema_" in name
            or name.startswith(
                (
                    "rsi_",
                    "macd",
                    "atr_",
                )
            )
        )
    )


def _lookbacks(
    columns: FeatureColumns,
) -> dict[str, int]:
    return {
        name: lookback
        for name, (
            _,
            lookback,
        ) in columns.items()
    }


def build_features(
    history: pd.DataFrame,
    config: FeatureConfig = FeatureConfig(),
) -> FeatureResult:
    """Build reusable features for one chronological asset.

    OHLCV inputs must share a consistent adjustment basis.

    All enabled/emitted columns are required for model validity.
    Absent optional input groups are omitted with metadata.
    Nonfinite calculations are never filled.
    """
    frame = validate_history(
        history,
        config,
    )

    columns, skipped = (
        _build_feature_columns(
            frame,
            config,
        )
    )

    if not columns:
        raise ValueError(
            "Configuration and available inputs "
            "generate no features"
        )

    values = _feature_frame(
        columns,
        frame.index,
    )

    valid = (
        values
        .notna()
        .all(axis=1)
        .rename(
            "model_valid"
        )
    )

    return FeatureResult(
        all_features=values,
        valid_mask=valid,
        lookbacks=_lookbacks(
            columns
        ),
        recursive_features=(
            _recursive_features(
                columns
            )
        ),
        skipped_groups=tuple(
            skipped
        ),
        config=config,
    )