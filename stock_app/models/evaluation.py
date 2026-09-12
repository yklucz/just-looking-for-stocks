from .base import validate_y
from .metrics import classification_metrics


def evaluate_classifier(model, X, y, decision_threshold: float = 0.5, future_returns=None) -> dict:
    validate_y(X, y)
    if future_returns is not None and hasattr(future_returns, "index") and not X.index.equals(future_returns.index):
        raise ValueError("Future return timestamps must align with evaluation origins")
    return classification_metrics(y, model.predict_proba(X), decision_threshold, future_returns)


def comparison_table(report: dict, partition: str = "test") -> list[dict]:
    if partition not in {"train", "validation", "test"}:
        raise ValueError("Unknown partition")
    return [{"model": name, **result[partition]} for name, result in report["models"].items()]
