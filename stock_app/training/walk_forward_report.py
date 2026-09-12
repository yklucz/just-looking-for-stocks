"""CSV OOF/market snapshots and small, checksummed JSON report artifacts."""
import hashlib
import json
from pathlib import Path

import pandas as pd

from ..models.registry import fingerprint


def write_artifacts(root: Path, configuration: dict, summary: dict, folds: list[dict],
                    importance: list[dict], oof: pd.DataFrame, market: pd.DataFrame) -> None:
    if not oof.index.is_unique or not oof.index.is_monotonic_increasing:
        raise ValueError("OOF timestamps must be unique and chronological")
    for name, data in (("config.json", configuration), ("summary.json", summary),
                       ("folds.json", folds), ("feature_importance.json", importance)):
        (root / name).write_text(json.dumps(data, indent=2, allow_nan=False))
    oof.to_csv(root / "oof_predictions.csv", index_label="timestamp", float_format="%.17g")
    market.to_csv(root / "market_history.csv", index_label="timestamp", float_format="%.17g")
    files = {str(path.relative_to(root)): hashlib.sha256(path.read_bytes()).hexdigest()
             for path in sorted(root.rglob("*")) if path.is_file()}
    manifest = {"configuration_fingerprint": fingerprint(configuration), "sha256": files,
                "oof_rows": len(oof), "oof_start": oof.index[0].isoformat(), "oof_end": oof.index[-1].isoformat()}
    (root / "manifest.json").write_text(json.dumps(manifest, indent=2, allow_nan=False))


def verify_artifacts(root: Path) -> dict:
    manifest = json.loads((root / "manifest.json").read_text())
    configuration = json.loads((root / "config.json").read_text())
    if fingerprint(configuration) != manifest["configuration_fingerprint"]:
        raise ValueError("Configuration fingerprint mismatch")
    for name, checksum in manifest["sha256"].items():
        relative = Path(name)
        if relative.is_absolute() or ".." in relative.parts:
            raise ValueError("Invalid artifact path")
        if hashlib.sha256((root / relative).read_bytes()).hexdigest() != checksum:
            raise ValueError(f"Artifact checksum mismatch: {name}")
    return manifest
