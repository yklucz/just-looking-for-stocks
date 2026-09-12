"""Independent accounting examples and frozen-artifact execution guards."""
from dataclasses import replace
import hashlib
import json
from pathlib import Path

import numpy as np
import pandas as pd
import pytest
from pandas.testing import assert_frame_equal

from stock_app.config import BacktestConfig
from stock_app.backtest.benchmarks import benchmark_simulations
from stock_app.backtest.costs import effective_price
from stock_app.backtest.diagnostics import estimated_cost_break_even, probability_buckets
from stock_app.backtest.engine import simulate
from stock_app.backtest.inputs import load_frozen_inputs
from stock_app.backtest.metrics import performance, risk_statistics
from stock_app.backtest.run import run_backtest
from stock_app.backtest.strategy import LongFlatStrategy
from stock_app.models.registry import fingerprint
from stock_app.training.walk_forward_report import verify_artifacts, write_artifacts


ZERO = BacktestConfig(initial_capital=1000, holding_period=2, commission_bps=0, slippage_bps=0)


def market(prices):
    p = np.asarray(prices, dtype=float)
    return pd.DataFrame({'Open': p, 'High': p*1.01, 'Low': p*.99, 'Close': p, 'Volume': 1000.0},
                        index=pd.date_range('2020-01-01', periods=len(p), freq='B', tz='UTC'))


def signals(frame, values, start=0):
    return pd.Series(values, index=frame.index[start:start+len(values)], dtype=float)


@pytest.fixture
def frozen(tmp_path):
    raw = market(100*np.exp(np.arange(24)*.002))
    positions = np.arange(7, 15)
    outcome = np.log(raw.Close.iloc[positions+2].to_numpy()/raw.Close.iloc[positions].to_numpy())
    oof = pd.DataFrame({'ticker': 'SYNTH', 'fold_id': 0, 'raw_position': positions,
                        'actual_target': (outcome>.002).astype(int), 'future_log_return': outcome,
                        'target_time': raw.index[positions+2], 'model_available_after': raw.index[6],
                        'xgboost_probability': .8}, index=raw.index[positions])
    config = {'ticker': 'SYNTH', 'interval': '1d', 'target': {'task': 'binary', 'horizon': 2, 'threshold': .002},
              'models': {'xgboost': {}}, 'features': {}}
    folds = [{'fold_id': 0, 'raw_boundaries': {'validation_end_exclusive': 7, 'test_end_exclusive': 15},
              'partitions': {'train': {'label_end': raw.index[3].isoformat()},
                             'validation': {'origin_start': raw.index[4].isoformat(), 'label_end': raw.index[6].isoformat()}}}]
    summary = {'ticker': 'SYNTH', 'mode': 'expanding', 'oof_rows': len(oof), 'configuration_fingerprint': fingerprint(config),
               'provenance': {'source': 'synthetic OOF fixture; no trained model'}}
    raw.insert(0, 'raw_position', np.arange(len(raw)))
    path = tmp_path/'stage4'
    path.mkdir()
    write_artifacts(path, config, summary, folds, [], oof, raw)
    return path


def read_frozen(path):
    return load_frozen_inputs(path, price_convention='auto_adjusted_ohlc')


def test_prediction_executes_next_bar():
    m = market([90, 100, 105, 110, 115])
    r = simulate(m, signals(m, [1]), ZERO)
    trade = r.trades.iloc[0]
    assert trade.signal_origin == m.index[0] and trade.entry_timestamp == m.index[1]
    assert trade.raw_entry_price == 100


def test_no_same_close_execution():
    m = market([50, 100, 105, 110])
    m.loc[m.index[0], 'Close'] = 999
    r = simulate(m, signals(m, [1]), ZERO)
    assert r.trades.raw_entry_price.iloc[0] == 100


def test_holding_period_uses_candles_not_calendar_days():
    m = market(np.arange(30)+100)
    r = simulate(m, signals(m, [1], start=1), replace(ZERO, holding_period=5))
    t = r.trades.iloc[0]
    assert t.exit_position == t.entry_position+5
    assert t.exit_timestamp == m.index[7]
    assert t.holding_bars == 5


def test_non_overlapping_positions():
    m = market(np.arange(20)+100)
    r = simulate(m, signals(m, [1]*13), ZERO)
    assert (r.trades.entry_position.iloc[1:].to_numpy() > r.trades.exit_position.iloc[:-1].to_numpy()).all()
    assert r.trades.entry_position.tolist() == [1, 4, 7, 10, 13]
    assert r.diagnostics['ignored_while_invested'] > 0
    assert r.equity.exposure.max() <= 1


def test_no_short_for_binary_target():
    assert LongFlatStrategy().generate_signal(.01) == 'FLAT'
    with pytest.raises(ValueError, match='LONG/FLAT'):
        BacktestConfig(allow_short=True)
    m = market([100, 90, 80, 70])
    r = simulate(m, signals(m, [.01]), ZERO)
    assert r.trades.empty and (r.equity.quantity == 0).all()


@pytest.mark.parametrize('invalid', [{'position_size': 1.1}, {'execution_field': 'Close'}, {'holding_period': 0},
                                    {'commission_bps': -1}, {'slippage_bps': 10000}, {'initial_capital': 0},
                                    {'decision_threshold': float('nan')}])
def test_invalid_backtest_configuration(invalid):
    with pytest.raises(ValueError):
        BacktestConfig(**invalid)


def test_entry_slippage():
    assert effective_price(100, 'entry', replace(ZERO, slippage_bps=20)) == pytest.approx(100.2)


def test_exit_slippage():
    assert effective_price(110, 'exit', replace(ZERO, slippage_bps=20)) == pytest.approx(109.78)


def test_commission_both_sides_and_exact_trade_pnl():
    m = market([99, 100, 101, 110, 111])
    config = replace(ZERO, commission_bps=10, slippage_bps=20)
    r = simulate(m, signals(m, [1]), config)
    t = r.trades.iloc[0]
    quantity = 1000/(100.2*1.001)
    entry_fee = quantity*100.2*.001
    exit_fee = quantity*109.78*.001
    gross = quantity*10
    slippage = quantity*.42
    net = quantity*109.78*.999-1000
    assert t.quantity == pytest.approx(quantity, rel=1e-12)
    assert t.entry_commission == pytest.approx(entry_fee)
    assert t.exit_commission == pytest.approx(exit_fee)
    assert t.commission == pytest.approx(entry_fee+exit_fee)
    assert t.gross_pnl == pytest.approx(gross)
    assert t.slippage_cost == pytest.approx(slippage)
    assert t.net_pnl == pytest.approx(net)
    assert t.net_pnl == pytest.approx(gross-slippage-entry_fee-exit_fee)
    assert t.gross_return == pytest.approx(.1)
    assert r.equity.equity.iloc[-1] == pytest.approx(1000+net)


def test_portfolio_accounting_and_equity_curve():
    m = market([99, 100, 80, 110, 100, 105, 90, 99, 110])
    cfg = replace(ZERO, position_size=.5, commission_bps=5, slippage_bps=10)
    r = simulate(m, signals(m, [1]*6), cfg)
    c = r.equity
    assert c.index.is_unique and c.index.is_monotonic_increasing
    assert (c.cash >= 0).all() and (c.quantity >= 0).all()
    np.testing.assert_allclose(c.equity, c.cash+c.position_value)
    np.testing.assert_allclose(c.equity-1000, c.realized_pnl+c.unrealized_pnl, atol=1e-10)
    assert c.equity.iloc[-1]-1000 == pytest.approx(r.trades.net_pnl.sum())
    assert np.prod(1+c.period_return.iloc[1:]) == pytest.approx(c.equity.iloc[-1]/1000)


def test_drawdown():
    m = market([100, 100, 80, 120])
    r = simulate(m, signals(m, [1]), ZERO)
    assert r.equity.drawdown.min() == pytest.approx(-.2)
    assert performance(r, ZERO)['max_drawdown'] == pytest.approx(-.2)


def test_sharpe():
    r = np.array([.01, -.02, .03, -.01])
    expected = r.mean()/r.std(ddof=1)*np.sqrt(252)
    assert risk_statistics(r)['sharpe'] == pytest.approx(expected)


def test_sortino():
    r = np.array([.01, -.02, .03, -.01])
    downside = np.sqrt((.02**2+.01**2)/4)
    assert risk_statistics(r)['sortino'] == pytest.approx(r.mean()/downside*np.sqrt(252))
    assert risk_statistics(np.array([.01, .02]))['sortino'] is None


def test_profit_factor():
    m = market([100, 100, 105, 110, 100, 95, 90])
    r = simulate(m, signals(m, [1]*4), ZERO)
    metrics = performance(r, ZERO)
    assert r.trades.net_pnl.to_list() == pytest.approx([100, -110])
    assert metrics['profit_factor'] == pytest.approx(100/110)
    assert metrics['win_rate'] == metrics['loss_rate'] == .5
    assert metrics['expectancy'] == pytest.approx(-5)


def test_zero_trade_case():
    m = market(np.arange(20)+100)
    r = simulate(m, signals(m, [0]*10), ZERO)
    stats = performance(r, ZERO)
    assert stats['trade_count'] == 0 and stats['total_return'] == 0
    assert stats['sharpe'] is None and stats['profit_factor'] is None
    assert stats['exposure'] == stats['turnover'] == 0


def test_incomplete_final_trade():
    m = market([100]*6)
    # Origin 3 has a known Close[5] target, but its required exit Open[6] is absent.
    r = simulate(m, signals(m, [0, 0, 0, 1]), ZERO)
    assert r.trades.empty
    assert r.diagnostics['incomplete_long_signals_excluded'] == 1
    assert r.diagnostics['unsupported_exit_origins'] == 1


def test_buy_hold_same_period_even_zero_strategy_trades():
    m = market(np.arange(40)+100)
    p = signals(m, [0]*10, start=8)
    strategy = simulate(m, p, ZERO)
    b = benchmark_simulations(m, p, ZERO)
    for s in b.values():
        assert s.equity.index.equals(strategy.equity.index)
    assert b['buy_hold'].trades.entry_position.iloc[0] == 9
    assert b['buy_hold'].trades.exit_position.iloc[0] == 20
    assert performance(b['cash'], ZERO)['total_return'] == 0


def test_no_future_execution_information():
    m = market(100+np.arange(30))
    p = signals(m, [.8]*20)
    a = simulate(m, p, ZERO)
    future = m.copy()
    future.iloc[15:] *= 100
    b = simulate(future, p, ZERO)
    assert_frame_equal(a.equity.iloc[:14], b.equity.iloc[:14])
    assert a.trades.entry_position.tolist() == b.trades.entry_position.tolist()
    assert a.trades.exit_position.tolist() == b.trades.exit_position.tolist()


def test_oof_snapshot_alignment(frozen):
    inputs = read_frozen(frozen)
    assert inputs.oof.index.equals(inputs.market.index[inputs.oof.raw_position])
    with pytest.raises(ValueError, match='Ticker'):
        load_frozen_inputs(frozen, ticker='WRONG', price_convention='auto_adjusted_ohlc')
    with pytest.raises(ValueError, match='fingerprint'):
        load_frozen_inputs(frozen, expected_fingerprint='wrong', price_convention='auto_adjusted_ohlc')


def test_snapshot_checksum_rejection(frozen):
    with (frozen/'market_history.csv').open('a') as handle:
        handle.write('\n')
    with pytest.raises(ValueError, match='checksum'):
        read_frozen(frozen)


def test_unknown_adjustment_convention_rejected(frozen):
    with pytest.raises(ValueError, match='adjustment convention'):
        load_frozen_inputs(frozen)


def test_rehashed_misaligned_snapshot_rejected(frozen):
    oof = pd.read_csv(frozen/'oof_predictions.csv')
    oof.loc[0, 'raw_position'] += 1
    oof.to_csv(frozen/'oof_predictions.csv', index=False)
    manifest = json.loads((frozen/'manifest.json').read_text())
    manifest['sha256']['oof_predictions.csv'] = hashlib.sha256((frozen/'oof_predictions.csv').read_bytes()).hexdigest()
    (frozen/'manifest.json').write_text(json.dumps(manifest))
    with pytest.raises(ValueError, match='origin alignment'):
        read_frozen(frozen)


def test_synthetic_profitable_signal_and_cost_drag():
    # Predetermined high signals cause gains after next-open entry; intervening bars fall.
    probability = np.where(np.arange(100) % 6 == 0, .8, .2)
    prices = [100.0]
    for i in range(1, 105):
        planted = any(0 <= origin < len(probability) and probability[origin] > .5
                      for origin in (i-2, i-3))
        prices.append(prices[-1] * (1.02 if planted else .995))
    m = market(prices)
    p = signals(m, probability)
    free = simulate(m, p, ZERO)
    costly = simulate(m, p, replace(ZERO, commission_bps=1, slippage_bps=5))
    assert free.equity.equity.iloc[-1] > costly.equity.equity.iloc[-1] > 1000
    assert estimated_cost_break_even(free.trades) > 0


def test_no_losses_and_insufficient_risk_observations():
    m = market([100, 100, 105, 110])
    result = performance(simulate(m, signals(m, [1]), ZERO), ZERO)
    assert result['profit_factor'] is None
    assert result['average_loss'] is None and result['loss_rate'] == 0
    assert risk_statistics(np.array([.1]))['annualized_volatility'] is None


def test_synthetic_no_signal():
    rng = np.random.default_rng(782)
    m = market(100*np.exp(np.cumsum(rng.normal(0, .01, 1000))))
    p = signals(m, rng.random(950))
    r = simulate(m, p, ZERO)
    assert np.isfinite(r.equity.equity).all()
    assert r.equity.equity.iloc[-1]-1000 == pytest.approx(r.trades.net_pnl.sum())
    assert r.trades.entry_position.iloc[0] > 0


def test_probability_buckets_boundaries_and_no_calibration():
    frame = pd.DataFrame({'xgboost_probability': [.4, .5, .6, 1], 'actual_target': [0, 1, 0, 1], 'future_log_return': [0, .01, -.02, .03]})
    before = frame.copy()
    b = probability_buckets(frame, 'xgboost', ZERO.bucket_edges)
    assert b.observations.sum() == 4
    assert b.loc[b.lower == .6, 'observations'].iloc[0] == 2
    assert b.loc[b.lower == .6, 'event_frequency'].iloc[0] == .5
    assert_frame_equal(frame, before)


def test_artifacts_frozen_source_and_future_annotations_not_used(frozen):
    inputs = read_frozen(frozen)
    before = (frozen/'manifest.json').read_bytes()
    result = run_backtest(inputs, config=ZERO)
    root = Path(result['artifact_path'])
    assert (root/'report.md').exists() and (root/'equity.csv').exists()
    assert result['net']['total_costs'] == 0
    changed = inputs.oof.copy()
    changed['future_log_return'] = 99
    changed['actual_target'] = 0
    other = run_backtest(replace(inputs, oof=changed), config=ZERO)
    assert result['net'] == other['net']
    assert (frozen/'manifest.json').read_bytes() == before
    verify_artifacts(frozen)


def test_cost_scenarios_reduce_terminal_wealth():
    from stock_app.config import BACKTEST_COST_SCENARIOS
    m = market(100*1.005**np.arange(100))
    p = signals(m, [.8]*90)
    wealth = []
    for c, s in BACKTEST_COST_SCENARIOS.values():
        cfg = replace(ZERO, commission_bps=c, slippage_bps=s)
        result = simulate(m, p, cfg)
        stats = performance(result, cfg)
        assert stats['gross_pnl']-stats['total_costs'] == pytest.approx(stats['net_pnl'])
        wealth.append(stats['final_equity'])
    assert all(a > b for a, b in zip(wealth, wealth[1:]))


def test_buy_hold_entry_exit_fees_and_exposure():
    m = market([100, 100, 105, 110])
    cfg = replace(ZERO, commission_bps=10, slippage_bps=20)
    benchmark = benchmark_simulations(m, signals(m, [0]), cfg)['buy_hold']
    expected = 1000/(100.2*1.001)*109.78*.999
    assert benchmark.equity.equity.iloc[-1] == pytest.approx(expected)
    assert performance(benchmark, cfg)['exposure'] == 1


def test_oof_gaps_preserve_real_market_sessions():
    m = market(100+np.arange(20))
    p = pd.Series([1.0, 1.0], index=m.index[[0, 10]])
    r = simulate(m, p, ZERO)
    assert r.trades.entry_position.tolist() == [1, 11]
    assert r.trades.exit_position.tolist() == [3, 13]
    assert len(r.equity) == 13


def test_backtest_does_not_fit_models(frozen, monkeypatch):
    from stock_app.models import LogisticClassifier, XGBoostClassifier
    def forbidden(*args, **kwargs):
        raise AssertionError('Backtest must never fit a model')
    monkeypatch.setattr(LogisticClassifier, 'fit', forbidden)
    monkeypatch.setattr(XGBoostClassifier, 'fit', forbidden)
    result = run_backtest(read_frozen(frozen), config=ZERO)
    assert result['net']['trade_count'] > 0
