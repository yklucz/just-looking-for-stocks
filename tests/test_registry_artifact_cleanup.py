"""Failed publication must not leave temporary registry artifact files behind."""
import pytest
from stock_app.research import registry_execution


@pytest.mark.parametrize('operation', ['fsync', 'link'])
def test_immutable_artifact_failure_cleans_temporary_file(tmp_path, monkeypatch, operation):
    def fail(*args):
        raise OSError('simulated storage failure')
    monkeypatch.setattr(registry_execution.os, operation, fail)
    with pytest.raises(OSError, match='simulated storage failure'):
        registry_execution.immutable_bytes(tmp_path, b'registry fixture', '.json')
    assert list(tmp_path.iterdir()) == []
