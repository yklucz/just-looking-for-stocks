"""Read only frozen, checksummed OOF and its own market snapshot."""
from dataclasses import dataclass
import hashlib
import json
from pathlib import Path

import numpy as np
import pandas as pd

from ..config import FeatureConfig
from ..features.validation import validate_history
from ..models.registry import fingerprint
from ..training.walk_forward_report import verify_artifacts


@dataclass(frozen=True)
class FrozenInputs:
    path: Path
    market: pd.DataFrame
    oof: pd.DataFrame
    configuration: dict
    summary: dict
    source_manifest_sha256: str
    price_convention: dict


def load_frozen_inputs(path: Path, ticker: str | None = None,
                       expected_fingerprint: str | None = None,
                       price_convention: str | None = None) -> FrozenInputs:
    path = Path(path).resolve()
    manifest = verify_artifacts(path)
    required = {"config.json", "summary.json", "folds.json", "oof_predictions.csv", "market_history.csv"}
    if not required.issubset(manifest["sha256"]):
        raise ValueError("Incomplete Stage 4 manifest")
    configuration = json.loads((path / "config.json").read_text())
    summary = json.loads((path / "summary.json").read_text())
    identity = fingerprint(configuration)
    if (summary["configuration_fingerprint"] != identity
            or expected_fingerprint is not None and expected_fingerprint != identity):
        raise ValueError("Stage 4 fingerprint mismatch")
    if configuration["interval"] != "1d" or configuration["target"]["task"] != "binary":
        raise ValueError("Stage 5 requires daily binary-event OOF")
    symbol = configuration["ticker"]
    if summary["ticker"] != symbol or ticker is not None and ticker.upper() != symbol:
        raise ValueError("Ticker mismatch")
    market = pd.read_csv(path / "market_history.csv", float_precision="round_trip")
    oof = pd.read_csv(path / "oof_predictions.csv", float_precision="round_trip")
    for frame in (market, oof):
        frame.index = pd.DatetimeIndex(pd.to_datetime(frame.pop("timestamp"), utc=True))
        if frame.empty or not frame.index.is_unique or not frame.index.is_monotonic_increasing:
            raise ValueError("Frozen timestamps must be nonempty, unique and chronological")
    if not np.array_equal(market.raw_position, np.arange(len(market))):
        raise ValueError("Snapshot raw positions are invalid")
    market = validate_history(market.drop(columns="raw_position"),
                              FeatureConfig(required_columns=("Open", "High", "Low", "Close", "Volume")))
    positions = oof.raw_position.to_numpy()
    h = configuration["target"]["horizon"]
    if (positions.dtype.kind not in "iu" or positions.min() < 0 or positions.max() + h >= len(market)
            or not oof.index.equals(market.index[positions]) or not (oof.ticker == symbol).all()):
        raise ValueError("OOF/snapshot origin alignment mismatch")
    if len(oof) != manifest["oof_rows"] or len(oof) != summary["oof_rows"]:
        raise ValueError("OOF row count mismatch")
    target_time = pd.DatetimeIndex(pd.to_datetime(oof.target_time, utc=True))
    expected_returns = np.log(market.Close.to_numpy()[positions+h]/market.Close.to_numpy()[positions])
    if (not target_time.equals(market.index[positions+h])
            or not np.allclose(oof.future_log_return, expected_returns, rtol=1e-12, atol=1e-14)
            or not np.array_equal(oof.actual_target, (expected_returns > configuration["target"]["threshold"]).astype(int))):
        raise ValueError("Frozen target alignment mismatch")
    folds = {f["fold_id"]: f for f in json.loads((path / "folds.json").read_text())}
    for fold_id, rows in oof.groupby("fold_id"):
        if fold_id not in folds:
            raise ValueError("Unknown OOF fold")
        f = folds[fold_id]
        boundaries = f["raw_boundaries"]
        cutoff = market.index[boundaries["validation_end_exclusive"]-1]
        if (rows.raw_position.min() < boundaries["validation_end_exclusive"]
                or rows.raw_position.max() >= boundaries["test_end_exclusive"]
                or not (pd.to_datetime(rows.model_available_after, utc=True) == cutoff).all()
                or pd.to_datetime(f["partitions"]["validation"]["label_end"], utc=True) >= rows.index[0]
                or pd.to_datetime(f["partitions"]["train"]["label_end"], utc=True) >= pd.to_datetime(f["partitions"]["validation"]["origin_start"], utc=True)):
            raise ValueError("OOF model availability or purging violation")
    for model in configuration["models"]:
        p = oof[f"{model}_probability"].to_numpy()
        if not np.isfinite(p).all() or ((p < 0) | (p > 1)).any():
            raise ValueError("Invalid frozen probabilities")
    source = summary.get("provenance", {}).get("source", "")
    if price_convention is not None:
        if price_convention != "auto_adjusted_ohlc":
            raise ValueError("Only consistent auto-adjusted OHLC is supported in Stage 5")
        convention = {"name": price_convention, "evidence": "caller declaration", "limitation": "not independently vendor-attested"}
    elif source.startswith("Yahoo daily full-history cache"):
        convention = {"name": "auto_adjusted_ohlc", "evidence": "inferred from stock_service._get_full_history and yfinance auto_adjust=True default",
                      "limitation": "Stage 4 did not store adjustment flags or yfinance version; current retrieval code establishes the convention, not vendor attestation"}
    else:
        raise ValueError("Unknown OHLC adjustment convention; explicitly declare consistent auto_adjusted_ohlc")
    return FrozenInputs(path, market, oof, configuration, summary,
                        hashlib.sha256((path / "manifest.json").read_bytes()).hexdigest(), convention)
