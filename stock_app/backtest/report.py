"""New Stage 5 artifacts reference immutable Stage 4 source hashes."""
import hashlib
import json
from pathlib import Path

from ..models.registry import fingerprint


def write_report(root: Path, configuration: dict, summary: dict, tables: dict) -> None:
    root.mkdir(parents=True, exist_ok=False)
    for name, payload in (("config.json", configuration), ("summary.json", summary)):
        (root / name).write_text(json.dumps(payload, indent=2, allow_nan=False))
    for name, frame in tables.items():
        frame.to_csv(root / f"{name}.csv", index=name.endswith("equity"),
                     index_label="timestamp" if name.endswith("equity") else None, float_format="%.17g")
    lines = [f"# Frozen OOF backtest: {summary['ticker']} {summary['model']}",
             f"Source: {configuration['source']['artifact']}",
             f"Target: {configuration['target']}",
             f"Execution: {configuration['execution']}",
             f"Settings: {configuration['backtest']}",
             f"Period: {summary['net']['evaluation_start']} to {summary['net']['evaluation_end']}",
             "| Metric | Net strategy | Zero-cost strategy | Buy & Hold | Cash |",
             "| --- | --- | --- | --- | --- |"]
    for metric in summary['net']:
        values = [summary['net'][metric], summary['gross'][metric], summary['benchmarks']['buy_hold'][metric], summary['benchmarks']['cash'][metric]]
        lines.append('| '+metric+' | '+' | '.join('undefined' if v is None else f'{v:.6g}' if isinstance(v, float) else str(v) for v in values)+' |')
    lines.extend(["", "Gross strategy is a separate zero-cost replay. Gross PnL inside net metrics uses actual cost-sized quantities.",
                  "Profit factor uses net trade PnL. Sharpe uses open-to-open equity returns and 252 sessions/year, risk-free rate zero.",
                  "Sortino uses RMS downside over all sessions with minimum acceptable return zero. Initial entry costs are included in the first interval.",
                  "Adjusted-unit prices are a research proxy, not a broker cash-dividend ledger. Low event probability is not a bearish forecast.",
                  "No model was fitted, no probabilities calibrated, and no thresholds selected from these results.",
                  "Backtest performance does not guarantee future performance."])
    (root / "report.md").write_text('\n\n'.join(lines[:6])+'\n\n'+'\n'.join(lines[6:])+'\n')
    files = {p.name: hashlib.sha256(p.read_bytes()).hexdigest() for p in root.iterdir() if p.is_file()}
    (root / "manifest.json").write_text(json.dumps({"configuration_fingerprint": fingerprint(configuration), "sha256": files}, indent=2))
