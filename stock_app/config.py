"""Temporal research and feature defaults (windows count observed candles)."""
from dataclasses import dataclass
import math


@dataclass(frozen=True)
class TargetConfig:
    horizon: int = 1
    threshold: float = 0.002
    task: str = "regression"

    def __post_init__(self) -> None:
        if type(self.horizon) is not int or self.horizon < 1:
            raise ValueError("horizon must be a positive integer")
        if not math.isfinite(self.threshold) or self.threshold < 0:
            raise ValueError("threshold must be finite and nonnegative")
        if self.task not in {"regression", "binary", "three_class", "legacy_price"}:
            raise ValueError("Unsupported target task")


@dataclass(frozen=True)
class SplitConfig:
    train_fraction: float = 0.70
    validation_fraction: float = 0.15

    def __post_init__(self) -> None:
        if not (0 < self.train_fraction < 1 and 0 < self.validation_fraction < 1
                and self.train_fraction + self.validation_fraction < 1):
            raise ValueError("Split fractions must leave nonempty train, validation and test blocks")


DEFAULT_SPLIT = SplitConfig()
LEGACY_TARGET = TargetConfig(task="legacy_price")


def _validate_feature_flags(config: "FeatureConfig") -> None:
    for name in ("returns", "trend", "momentum", "volatility", "candle", "volume", "time", "raw"):
        if type(getattr(config, name)) is not bool:
            raise ValueError(f"{name} must be a boolean")


def _validate_feature_window_tuples(config: "FeatureConfig") -> None:
    names = ("return_windows", "log_return_windows", "sma_windows", "sma_ratio_windows",
             "ema_windows", "roc_windows", "volatility_windows", "volume_windows")
    for name in names:
        values = getattr(config, name)
        valid = (isinstance(values, tuple) and bool(values)
                 and len(set(values)) == len(values)
                 and all(type(value) is int and value >= 1 for value in values))
        if not valid:
            raise ValueError(f"{name} must be a nonempty tuple of unique positive integers")


def _validate_feature_windows(config: "FeatureConfig") -> None:
    names = ("rsi_window", "macd_fast", "macd_slow", "macd_signal",
             "atr_window", "volume_zscore_window")
    for name in names:
        value = getattr(config, name)
        if type(value) is not int or value < 1:
            raise ValueError(f"{name} must be a positive integer")
    pair = config.ema_ratio_pair
    if (not isinstance(pair, tuple) or len(pair) != 2
            or any(type(value) is not int or value < 1 for value in pair)):
        raise ValueError("ema_ratio_pair must contain two positive integer windows")
    if config.macd_fast >= config.macd_slow:
        raise ValueError("MACD fast window must be smaller than slow window")
    if min(config.volatility_windows) < 2 or config.volume_zscore_window < 2:
        raise ValueError("Standard deviation windows require at least two observations")


def _validate_feature_inputs(config: "FeatureConfig") -> None:
    intervals = {"1m", "2m", "5m", "15m", "30m", "60m", "90m", "1h",
                 "1d", "5d", "1wk", "1mo", "3mo"}
    if config.interval not in intervals:
        raise ValueError("Explicit supported candle interval required")
    if (not isinstance(config.required_columns, tuple)
            or not set(config.required_columns) <= {"Open", "High", "Low", "Close", "Volume"}):
        raise ValueError("required_columns must contain only OHLCV names")


@dataclass(frozen=True)
class FeatureConfig:
    """Windows count candles; tuple ordering defines the stable feature schema."""
    returns: bool = True
    trend: bool = True
    momentum: bool = True
    volatility: bool = True
    candle: bool = True
    volume: bool = True
    time: bool = True
    raw: bool = False
    interval: str = "1d"
    required_columns: tuple[str, ...] = ("Close",)
    return_windows: tuple[int, ...] = (1, 2, 5, 10, 20)
    log_return_windows: tuple[int, ...] = (1, 5, 20)
    sma_windows: tuple[int, ...] = (5, 10, 20, 50, 200)
    sma_ratio_windows: tuple[int, ...] = (20, 50)
    ema_windows: tuple[int, ...] = (10, 20, 50)
    ema_ratio_pair: tuple[int, int] = (10, 50)
    rsi_window: int = 14
    roc_windows: tuple[int, ...] = (5, 10, 20)
    macd_fast: int = 12
    macd_slow: int = 26
    macd_signal: int = 9
    volatility_windows: tuple[int, ...] = (5, 20, 60)
    atr_window: int = 14
    volume_windows: tuple[int, ...] = (5, 20)
    volume_zscore_window: int = 20

    def __post_init__(self) -> None:
        _validate_feature_flags(self)
        _validate_feature_window_tuples(self)
        _validate_feature_windows(self)
        _validate_feature_inputs(self)


@dataclass(frozen=True)
class LogisticConfig:
    C: float = 1.0
    max_iter: int = 1000
    class_weight: str | None = None
    random_state: int = 42

    def __post_init__(self) -> None:
        if not math.isfinite(self.C) or self.C <= 0 or type(self.max_iter) is not int or self.max_iter < 1:
            raise ValueError("Logistic C and max_iter must be positive")
        if self.class_weight not in {None, "balanced"}:
            raise ValueError("class_weight must be None or balanced")


@dataclass(frozen=True)
class XGBoostConfig:
    n_estimators: int = 200
    max_depth: int = 3
    learning_rate: float = 0.05
    subsample: float = 0.8
    colsample_bytree: float = 0.8
    min_child_weight: float = 3.0
    reg_lambda: float = 1.0
    reg_alpha: float = 0.0
    random_state: int = 42
    n_jobs: int = 1
    early_stopping_rounds: int = 20
    balance_classes: bool = False

    def __post_init__(self) -> None:
        for name in ("n_estimators", "max_depth", "n_jobs", "early_stopping_rounds"):
            if type(getattr(self, name)) is not int or getattr(self, name) < 1:
                raise ValueError(f"{name} must be a positive integer")
        for name in ("learning_rate", "subsample", "colsample_bytree"):
            if not 0 < getattr(self, name) <= 1:
                raise ValueError(f"{name} must be in (0, 1]")
        for name in ("min_child_weight", "reg_lambda", "reg_alpha"):
            if not math.isfinite(getattr(self, name)) or getattr(self, name) < 0:
                raise ValueError(f"{name} must be finite and nonnegative")


@dataclass(frozen=True)
class WalkForwardConfig:
    """Raw-candle budgets before feature filtering and train/validation purging."""
    mode: str = "expanding"
    minimum_train_samples: int = 1000
    validation_samples: int = 250
    test_samples: int = 250
    step_samples: int = 250
    rolling_train_samples: int = 1000
    max_folds: int | None = None
    auc_std_warning: float = 0.05
    brier_std_warning: float = 0.03
    f1_std_warning: float = 0.15

    def __post_init__(self) -> None:
        if self.mode not in {"expanding", "rolling"}:
            raise ValueError("mode must be expanding or rolling")
        for name in ("minimum_train_samples", "validation_samples", "test_samples",
                     "step_samples", "rolling_train_samples"):
            if type(getattr(self, name)) is not int or getattr(self, name) < 1:
                raise ValueError(f"{name} must be a positive integer")
        if self.step_samples < self.test_samples:
            raise ValueError("Overlapping test windows are not supported; step must be >= test size")
        if self.rolling_train_samples > self.minimum_train_samples and self.mode == "rolling":
            raise ValueError("Initial training block must cover the requested rolling window")
        if self.max_folds is not None and (type(self.max_folds) is not int or self.max_folds < 1):
            raise ValueError("max_folds must be None or a positive integer")
        for name in ("auc_std_warning", "brier_std_warning", "f1_std_warning"):
            if not math.isfinite(getattr(self, name)) or getattr(self, name) < 0:
                raise ValueError("Stability warning thresholds must be finite and nonnegative")


@dataclass(frozen=True)
class BacktestConfig:
    """Daily, fractional adjusted-unit, unlevered LONG/FLAT research settings."""
    initial_capital: float = 100_000.0
    decision_threshold: float = 0.5
    holding_period: int = 5
    commission_bps: float = 1.0
    slippage_bps: float = 1.0
    position_size: float = 1.0
    maximum_position: float = 1.0
    execution_field: str = "Open"
    allow_short: bool = False
    annualization: int = 252
    bucket_edges: tuple[float, ...] = (0.0, 0.4, 0.45, 0.5, 0.55, 0.6, 1.0)

    def __post_init__(self) -> None:
        for name in ("initial_capital", "decision_threshold", "commission_bps", "slippage_bps",
                     "position_size", "maximum_position"):
            if not math.isfinite(getattr(self, name)):
                raise ValueError(f"{name} must be finite")
        if self.initial_capital <= 0:
            raise ValueError("Initial capital must be positive")
        if not 0 <= self.decision_threshold <= 1:
            raise ValueError("Probability threshold must be within [0, 1]")
        if not 0 <= self.position_size <= self.maximum_position <= 1:
            raise ValueError("Position sizing must satisfy 0 <= size <= maximum <= 1; no leverage")
        if any(not 0 <= v < 10000 for v in (self.commission_bps, self.slippage_bps)):
            raise ValueError("Execution costs must be within [0, 10000) basis points")
        if type(self.holding_period) is not int or self.holding_period < 1:
            raise ValueError("Holding period must be a positive integer")
        if self.allow_short is not False or self.execution_field != "Open":
            raise ValueError("Stage 5 supports LONG/FLAT with next-Open execution only")
        if type(self.annualization) is not int or self.annualization < 1:
            raise ValueError("Annualization must be a positive integer")
        if (len(self.bucket_edges) < 2 or self.bucket_edges[0] != 0 or self.bucket_edges[-1] != 1
                or any(not math.isfinite(v) for v in self.bucket_edges)
                or any(a >= b for a, b in zip(self.bucket_edges, self.bucket_edges[1:]))):
            raise ValueError("Probability bucket edges must strictly increase from 0 to 1")


BACKTEST_COST_SCENARIOS = {"zero": (0.0, 0.0), "primary": (1.0, 1.0),
                           "higher_slippage": (1.0, 5.0), "stress": (5.0, 10.0)}


@dataclass(frozen=True)
class GRUConfig:
    """One fixed challenger configuration; no test/backtest-driven tuning."""
    sequence_length: int = 64
    projection_size: int = 64
    hidden_size: int = 64
    num_layers: int = 2
    dropout: float = 0.2
    head_size: int = 64
    batch_size: int = 64
    learning_rate: float = 1e-3
    weight_decay: float = 1e-4
    max_epochs: int = 30
    patience: int = 5
    gradient_clip: float = 1.0
    min_delta: float = 1e-5
    scheduler_patience: int = 2
    scheduler_factor: float = 0.5
    random_state: int = 42
    balance_classes: bool = False
    device: str = "auto"
    cpu_threads: int = 2

    def __post_init__(self) -> None:
        for name in ("sequence_length", "projection_size", "hidden_size", "num_layers", "head_size",
                     "batch_size", "max_epochs", "patience", "scheduler_patience", "cpu_threads"):
            if type(getattr(self, name)) is not int or getattr(self, name) < 1:
                raise ValueError(f"{name} must be a positive integer")
        if not 0 <= self.dropout < 1 or not 0 < self.scheduler_factor < 1:
            raise ValueError("Invalid dropout or scheduler factor")
        for name in ("learning_rate", "weight_decay", "gradient_clip", "min_delta"):
            if not math.isfinite(getattr(self, name)) or getattr(self, name) < 0:
                raise ValueError(f"Invalid {name}")
        if self.learning_rate == 0 or self.gradient_clip == 0:
            raise ValueError("Learning rate and gradient clip must be positive")
        if self.device not in {"auto", "cpu", "mps", "cuda"}:
            raise ValueError("Unsupported torch device")
        if type(self.random_state) is not int or self.random_state < 0:
            raise ValueError("Nonnegative integer random seed required")


@dataclass(frozen=True)
class PredictionConfig:
    model: str = "xgboost"
    interval: str = "1d"
    horizon: int = 5
    event_threshold: float = 0.002
    decision_threshold: float = 0.5
    stale_after_days: int = 365
    market_stale_after_days: int = 7
