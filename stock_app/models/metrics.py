"""Probability metrics and return diagnostics; no trading-performance calculations."""
import numpy as np
from sklearn.metrics import (accuracy_score, average_precision_score, balanced_accuracy_score,
                             brier_score_loss, confusion_matrix, f1_score, log_loss,
                             precision_score, recall_score, roc_auc_score)


def classification_metrics(y, probabilities, decision_threshold: float = 0.5, future_returns=None) -> dict:
    y, p = np.asarray(y), np.asarray(probabilities, dtype=float)
    if (y.ndim != 1 or p.shape != y.shape or not len(y) or not np.isin(y, [0, 1]).all()
            or not np.isfinite(p).all() or (p < 0).any() or (p > 1).any()):
        raise ValueError("Aligned binary labels and finite probabilities in [0, 1] required")
    if not 0 < decision_threshold < 1:
        raise ValueError("Decision probability threshold must be in (0, 1)")
    prediction = p >= decision_threshold
    both = len(np.unique(y)) == 2
    result = {
        "accuracy": float(accuracy_score(y, prediction)),
        "balanced_accuracy": float(balanced_accuracy_score(y, prediction)) if both else None,
        "precision": float(precision_score(y, prediction, zero_division=0)),
        "recall": float(recall_score(y, prediction, zero_division=0)),
        "f1": float(f1_score(y, prediction, zero_division=0)),
        "roc_auc": float(roc_auc_score(y, p)) if both else None,
        "pr_auc": float(average_precision_score(y, p)) if both else None,
        "brier_score": float(brier_score_loss(y, p)),
        "log_loss": float(log_loss(y, p, labels=[0, 1])),
        "confusion_matrix": confusion_matrix(y, prediction, labels=[0, 1]).tolist(),
        "decision_threshold": decision_threshold,
        "pr_auc_definition": "average_precision (non-trapezoidal)",
        "directional_accuracy": None,
    }
    if future_returns is not None:
        returns = np.asarray(future_returns, dtype=float)
        if returns.shape != y.shape or not np.isfinite(returns).all():
            raise ValueError("Future returns must align and be finite")
        result.update(directional_accuracy=float(np.mean(prediction == (returns > 0))),
                      mean_future_log_return_predicted_up=float(returns[prediction].mean()) if prediction.any() else None,
                      mean_future_log_return_predicted_not_up=float(returns[~prediction].mean()) if (~prediction).any() else None)
    return result
