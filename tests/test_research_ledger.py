"""Issued forecasts are immutable; retrospective records cannot qualify a model."""
import pandas as pd
import pytest


def test_issue_deduplicates_and_labels_late_forecasts(tmp_path):
    from stock_app.research.store import ResearchStore
    from stock_app.research.ledger import issue_forecast
    store = ResearchStore(tmp_path / 'r.db')
    args = dict(symbol='AAPL', model_id='m1', snapshot_id='s1', origin='2026-07-02',
                payload={'probability': .6, 'origin_close': 100, 'training_prior': .5})
    first = issue_forecast(store, **args, issued_at='2026-07-02T21:00:00Z')
    assert first['kind'] == 'prospective'  # July 3 holiday delays next open to July 6
    assert issue_forecast(store, **args, issued_at='2026-07-06T12:00:00Z')['id'] == first['id']
    assert len(store.list('forecasts')) == 1
    revised = issue_forecast(store, **{**args, 'snapshot_id': 's2'}, issued_at='2026-07-07T12:00:00Z')
    assert revised['kind'] == 'historical_replay'
    assert revised['revision_of'] == first['id']


def test_outcomes_use_session_horizon_and_consistent_adjustment(tmp_path):
    from stock_app.research.store import ResearchStore
    from stock_app.research.ledger import issue_forecast, resolve_forecasts
    store = ResearchStore(tmp_path / 'r.db')
    record = issue_forecast(store, symbol='AAPL', model_id='m', snapshot_id='s',
                            origin='2026-07-02', payload={'predicted_return': .1, 'origin_close': 100},
                            issued_at='2026-07-02T21:00:00Z')
    dates = pd.to_datetime(['2026-07-02','2026-07-06','2026-07-07','2026-07-08','2026-07-09','2026-07-10'], utc=True)
    # Updated adjusted history has halved all prices; same-basis return still +10%.
    history = pd.DataFrame({'Close': [50, 51, 52, 53, 54, 55]}, index=dates)
    resolve_forecasts(store, 'AAPL', history, snapshot_id='resolved', now='2026-07-10T19:00:00Z')
    assert store.get('forecasts', record['id'])['state'] == 'pending'
    resolve_forecasts(store, 'AAPL', history, snapshot_id='resolved', now='2026-07-10T21:00:00Z')
    result = store.get('forecasts', record['id'])
    assert result['state'] == 'resolved'
    assert result['outcome']['price_on_issue_basis'] == pytest.approx(110)
    assert result['outcome']['event'] == 1
    resolve_forecasts(store, 'AAPL', history.iloc[:-1], snapshot_id='missing', now='2026-07-11T21:00:00Z')
    assert store.get('forecasts', record['id'])['state'] == 'missing_data'
    resolve_forecasts(store, 'AAPL', history, snapshot_id='recovered', now='2026-07-11T21:00:00Z')
    assert store.get('forecasts', record['id'])['state'] == 'resolved'


def test_replay_cannot_qualify_and_nomination_resets_window(tmp_path):
    from stock_app.research.store import ResearchStore
    from stock_app.research.lifecycle import nominate, qualification
    store = ResearchStore(tmp_path / 'r.db')
    store.put('models', {'id': 'old', 'symbol': 'AAPL', 'task': 'binary', 'state': 'active'})
    store.set_active('old')
    store.put('models', {'id': 'new', 'symbol': 'AAPL', 'task': 'binary', 'state': 'candidate'})
    nominate(store, 'new')
    for i in range(130):
        store.put('forecasts', {'id': str(i), 'symbol': 'AAPL', 'model_id': 'new',
                                'state': 'resolved', 'kind': 'historical_replay'})
    result = qualification(store, 'new')
    assert result['eligible'] is False
    assert result['resolved'] == 0
    assert '126' in ' '.join(result['reasons'])


def test_qualification_uses_matched_prospective_errors_and_rejects_weak_candidate(tmp_path):
    from stock_app.research.store import ResearchStore
    from stock_app.research.lifecycle import qualification
    store = ResearchStore(tmp_path / 'r.db')
    store.put('models', {'id': 'old', 'symbol': 'AAPL', 'task': 'binary', 'state': 'active'})
    store.set_active('old')
    store.put('models', {'id': 'new', 'symbol': 'AAPL', 'task': 'binary', 'state': 'shadow',
                         'nominated_at': '2020-01-01T00:00:00Z', 'comparison_model_id': 'old'})
    for i, date in enumerate(pd.bdate_range('2020-01-02', periods=126)):
        actual = i % 2
        for identity, probability in [('old', .5), ('new', .9 if actual else .1)]:
            store.put('forecasts', {'id': f'{identity}{i}', 'symbol': 'AAPL', 'model_id': identity,
                       'origin': date.date().isoformat(), 'issued_at': date.tz_localize('UTC').isoformat(),
                       'kind': 'prospective', 'state': 'resolved',
                       'payload': {'probability': probability, 'training_prior': .5},
                       'outcome': {'event': actual, 'log_return': .02 if actual else -.02}})
    assert qualification(store, 'new')['eligible'] is True
    for row in store.list('forecasts', model_id='new'):
        store.update('forecasts', row['id'], payload={'probability': .5, 'training_prior': .5})
    assert qualification(store, 'new')['eligible'] is False


def test_early_close_issuance_and_missing_target_session(tmp_path):
    from stock_app.research.store import ResearchStore
    from stock_app.research.ledger import issue_forecast, resolve_forecasts
    store = ResearchStore(tmp_path / 'r.db')
    row = issue_forecast(store, symbol='AAPL', model_id='m', snapshot_id='s', origin='2026-11-27',
                         payload={'origin_close': 100}, issued_at='2026-11-27T18:30:00Z')
    assert row['kind'] == 'prospective'
    assert row['target'] == '2026-12-04'
    history = pd.DataFrame({'Close': [100, 110]}, index=pd.to_datetime(['2026-11-27','2026-12-04'], utc=True))
    resolve_forecasts(store, 'AAPL', history, snapshot_id='r', now='2026-12-04T22:00:00Z')
    assert store.get('forecasts', row['id'])['state'] == 'missing_data'


def test_qualification_does_not_replace_missing_first_issue_with_revision(tmp_path):
    from stock_app.research.store import ResearchStore
    from stock_app.research.lifecycle import qualification
    store = ResearchStore(tmp_path / 'r.db')
    store.put('models', {'id':'candidate', 'symbol':'AAPL', 'task':'regression', 'state':'shadow',
                         'nominated_at':'2020-01-01T00:00:00Z', 'comparison_model_id':None})
    for identity, state, hour in [('original','missing_data','21'), ('revision','resolved','22')]:
        timestamp = f'2020-01-02T{hour}:00:00Z'
        store.put('forecasts', {'id':identity, 'symbol':'AAPL', 'model_id':'candidate',
                  'created_at':timestamp, 'issued_at':timestamp, 'origin':'2020-01-02',
                  'kind':'prospective', 'state':state, 'payload':{'predicted_return':.1},
                  'outcome':{'log_return':.1}})
    assert qualification(store, 'candidate')['resolved'] == 0
    store.put('models', {'id':'new-active', 'symbol':'AAPL', 'task':'regression', 'state':'candidate'})
    store.set_active('new-active')
    assert 'comparator changed' in ' '.join(qualification(store, 'candidate')['reasons'])


@pytest.mark.parametrize('state', ['candidate', 'rejected', 'retired'])
def test_closed_or_unstarted_shadow_window_cannot_qualify(tmp_path, state):
    from stock_app.research.store import ResearchStore
    from stock_app.research.lifecycle import qualification
    store=ResearchStore(tmp_path/'r.db')
    store.put('models', {'id':'challenger','symbol':'AAPL','task':'regression','state':state,
                         'nominated_at':'2020-01-01T00:00:00Z','comparison_model_id':None})
    for i,day in enumerate(pd.bdate_range('2020-01-02',periods=126)):
        store.put('forecasts', {'id':str(i),'symbol':'AAPL','model_id':'challenger',
            'origin':day.date().isoformat(),'issued_at':day.tz_localize('UTC').isoformat(),
            'kind':'prospective','state':'resolved','payload':{'predicted_return':.02},
            'outcome':{'log_return':.02}})
    result=qualification(store,'challenger')
    assert result['eligible'] is False
    assert result['reasons']
