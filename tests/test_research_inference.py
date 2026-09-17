import pandas as pd


def test_chart_keeps_classifier_when_return_model_is_unavailable(monkeypatch):
    from stock_app.research import inference
    def active(ticker, task='binary'):
        if task == 'regression':
            raise ValueError('Required market/sector context is unavailable')
        return ({'id':'classifier'}, None, {'probability':.6, 'origin_time':'2026-09-14',
                'evaluation_kind':'historical_replay', 'forecast_id':'issued-one'})
    monkeypatch.setattr(inference, 'active_prediction', active)
    result = inference.chart_response('AAPL')
    assert result['probabilities'][0]['probability'] == .6
    assert result['forecast_status'] == 'unavailable'
    assert result['forecast'] is None
    assert result['evaluation_kind'] == 'historical_replay'
    assert result['forecast_ids']['classification'] == 'issued-one'


def test_prediction_reports_full_model_age(monkeypatch):
    from stock_app.research import inference
    history = pd.DataFrame({'Close':[100.]}, index=pd.to_datetime(['2026-09-14'], utc=True))
    monkeypatch.setattr(inference, 'active_prediction', lambda ticker: (
        {'id':'model'}, history, {'probability':.6, 'origin_time':str(history.index[0]),
        'trained_until':'2024-01-01T00:00:00Z', 'forecast_id':'record', 'evaluation_kind':'historical_replay'}))
    result = inference.prediction_response('AAPL')
    assert result['model']['stale']
    assert result['model']['age_days'] > 365


def test_prediction_marks_current_session_fallback_inputs_stale(tmp_path, monkeypatch):
    from stock_app.research.runtime import get_runtime
    from stock_app.research.calendar import latest_completed_session
    from stock_app.research import inference
    monkeypatch.setenv('STOCK_MODEL_MANIFEST', str(tmp_path/'absent.json'))
    runtime=get_runtime()
    day=latest_completed_session()
    raw=pd.DataFrame({'Open':[100.], 'High':[102.], 'Low':[99.], 'Close':[101.],
                      'Adj Close':[101.], 'Volume':[100.], 'Dividends':[0.], 'Stock Splits':[0.]},
                     index=pd.DatetimeIndex([day]))
    for symbol in ['AAPL','SPY','QQQ','XLK']:
        assert runtime.data.ingest(symbol,raw)['status']=='valid'
    runtime.store.put('models',{'id':'model','symbol':'AAPL','task':'binary','state':'candidate'})
    runtime.store.set_active('model')
    # Only estimator computation is substituted; acquisition, fallback, ledger and response are real.
    monkeypatch.setattr(inference,'model_payload',lambda model,history,contexts: {
        'probability':.6,'origin_time':str(day),'origin_close':101.,'trained_until':'2024-01-01T00:00:00Z'})
    assert inference.prediction_response('AAPL')['market']['stale'] is False
    runtime.data.ingest('SPY',pd.DataFrame())
    assert runtime.data.latest('SPY')['stale'] is True
    response=inference.prediction_response('AAPL')
    assert response['market']['stale'] is True
