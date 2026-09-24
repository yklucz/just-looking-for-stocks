"""Explicit opt-in only: PIT_LIVE_SMOKE=1 plus valid provider configuration.

Evidence lives in pytest's reported temporary directory. Default suite is offline.
"""
import os
import pytest
from stock_app.research.store import ResearchStore
from stock_app.research.pit.store import PITStore, list_rows
from stock_app.research.pit.transport import Config, ProviderError
from stock_app.research.pit.collection import Collector, Scope, verify_raw


@pytest.mark.parametrize('provider', ['sec', 'alfred'])
def test_explicit_live_smoke(tmp_path, provider):
    if os.environ.get('PIT_LIVE_SMOKE') != '1':
        pytest.skip('Opt-in provider smoke disabled')
    config = Config.environment()
    try:
        config.validate(provider)
    except ProviderError:
        pytest.skip('Required provider configuration absent/invalid')
    scope = Scope(provider, '2025-01-01', '2025-03-31', series=config.series,
                  observation_start='2025-01-01', observation_end='2025-03-31', max_pages=8)
    c = Collector(PITStore(ResearchStore(tmp_path / 'research.sqlite3')), config)
    first = c.collect(scope, execution_key='live-one')
    assert first['status'] == 'completed', first
    before = list_rows(c.store.path, 'revisions')
    assert before
    assert c.collect(scope, execution_key='live-two')['status'] == 'completed'
    assert list_rows(c.store.path, 'revisions') == before
    for raw in list_rows(c.store.path, 'raw'):
        assert verify_raw(c.store.path, raw['id'])['status'] == 'verified'
        assert c.reparse(raw['id'])['status'] == 'completed'
    if provider == 'alfred':
        from stock_app.research.pit.query import get_as_of
        assert get_as_of(c.store.path, as_of='2025-02-15T12:00:00Z', strictness='allow_proxy')['revisions']
    print('Retained live evidence:', c.store.path)
