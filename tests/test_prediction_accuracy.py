"""Checks scores against hand-calculated outcomes; no fitting or network access."""
import numpy as np
import pandas as pd
import pytest
from stock_app.prediction.evaluation import evaluate_predictions


def test_scores_use_only_matured_origins_after_validation():
    dates = pd.date_range('2025-01-01', periods=8, tz='UTC')
    close = pd.Series([100., 100., 100., 110., 100., 121., 110., 121.], index=dates)
    # h=2: origins 3,4,5 have returns +10%,+10%,0%; 6,7 are unresolved.
    predicted = pd.Series([.9, .9, .9, .8, .4, .3, .99, .99], index=dates)
    report = evaluate_predictions(close, predicted, horizon=2, threshold=.002,
                                  available_after=dates[2], task='binary')
    assert report['samples'] == 3
    assert report['pending_samples'] == 2
    assert report['origin_start'] == dates[3].isoformat()
    assert report['origin_end'] == dates[5].isoformat()
    assert report['metrics']['accuracy'] == pytest.approx(2/3)
    assert report['metrics']['brier_score'] == pytest.approx((.04+.36+.09)/3)
    assert report['baseline']['accuracy'] == pytest.approx(2/3)


def test_price_errors_compare_to_unchanged_close():
    dates = pd.date_range('2025-01-01', periods=5, tz='UTC')
    close = pd.Series([100., 100., 110., 100., 121.], index=dates)
    predictions = pd.Series(np.log([1., 1.1, 1., 1.]), index=dates[1:])
    report = evaluate_predictions(close, predictions, horizon=1, threshold=.002,
                                  available_after=dates[0], task='regression')
    assert report['samples'] == 3
    assert report['metrics']['price_mae'] == pytest.approx((10+21+21)/3)
    assert report['baseline']['price_mae'] == pytest.approx((10+10+21)/3)
    assert report['beats_baseline_mae'] is False


def test_no_resolved_predictions_is_not_zero_error():
    dates = pd.date_range('2025-01-01', periods=4, tz='UTC')
    report = evaluate_predictions(pd.Series([100.]*4,index=dates), pd.Series([.5],index=dates[-1:]),
                                  horizon=2,threshold=.002,available_after=dates[1],task='binary')
    assert report['status'] == 'insufficient_data'
    assert report['metrics'] is None


def test_nonfinite_outputs_are_rejected():
    dates = pd.date_range('2025-01-01', periods=4, tz='UTC')
    with pytest.raises(ValueError, match='finite'):
        evaluate_predictions(pd.Series([100.]*4,index=dates),pd.Series([np.nan],index=dates[1:2]),
                             horizon=1,threshold=.002,available_after=dates[0],task='binary')
