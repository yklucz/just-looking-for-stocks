import numpy as np
import pytest
from stock_app.models.metrics import classification_metrics


def test_metrics_known_example_brier_pr_auc_roc_auc():
    y = [0, 0, 1, 1]
    p = [.1, .4, .35, .8]
    m = classification_metrics(y, p, future_returns=[-.1, -.05, .01, .2])
    assert m["accuracy"] == .75
    assert m["balanced_accuracy"] == .75
    assert m["precision"] == 1
    assert m["recall"] == .5
    assert m["f1"] == pytest.approx(2/3)
    assert m["roc_auc"] == .75
    assert m["pr_auc"] == pytest.approx(5/6)
    assert m["brier_score"] == pytest.approx((.01 + .16 + .4225 + .04)/4)
    assert m["log_loss"] == pytest.approx(-np.mean(np.log([.9, .6, .35, .8])))
    assert m["confusion_matrix"] == [[2, 0], [1, 1]]
    assert m["directional_accuracy"] == .75
    assert m["mean_future_log_return_predicted_up"] == .2


def test_target_threshold_is_not_direction_threshold():
    # The first return is positive but below the target threshold.
    m = classification_metrics([0, 1], [.1, .8], future_returns=[.001, .01])
    assert m["accuracy"] == 1
    assert m["directional_accuracy"] == .5


def test_single_class_metrics_are_explicit_and_finite():
    m = classification_metrics([1, 1], [0, 0])
    assert m["roc_auc"] is None and m["pr_auc"] is None
    assert m["balanced_accuracy"] is None
    assert m["confusion_matrix"] == [[0, 0], [2, 0]]
    assert np.isfinite(m["log_loss"])


@pytest.mark.parametrize("p", [[np.nan, .5], [1.1, .5], [-.1, .5], [[.2], [.4]], [.4]])
def test_metrics_invalid_probabilities(p):
    with pytest.raises(ValueError):
        classification_metrics([0, 1], p)
