import json

import pytest

from stock_app.config import TargetConfig
from stock_app.training.train_baselines import feature_preset, run_research
from stock_app.training.feature_dataset import build_feature_dataset
from research_data import synthetic_market


@pytest.mark.parametrize("signal", [True, False])
def test_synthetic_signal_and_random_noise(tmp_path, signal):
    history = synthetic_market(signal)
    report = run_research(history, "SYNTHETIC", tmp_path, target_config=TargetConfig(task="binary"),
                          source=f"synthetic signal={signal}, seed=17")
    print("SYNTHETIC_RESULT", json.dumps({"signal": signal, "test": {m: r["test"]["roc_auc"] for m, r in report["models"].items()}}))
    naive = report["models"]["training_prior"]["test"]["roc_auc"]
    for name in ("logistic", "xgboost"):
        auc = report["models"][name]["test"]["roc_auc"]
        if signal:
            assert auc > naive + .3
        else:
            assert .3 < auc < .7
    assert report["feature_count"] == 49
    assert report["partitions"]["train"]["samples"] == 1900
    assert report["partitions"]["validation"]["samples"] == 449
    assert report["partitions"]["test"]["samples"] == 449
    assert report["xgboost_gain_importance"][0]["gain"] > 0
    assert json.loads(open(report["report_path"]).read())["target"]["task"] == "binary"


def test_presets_reuse_stage2_configuration():
    history = synthetic_market(False, rows=1000)
    for preset in ("all", "without_time", "without_volume", "returns_only", "trend_only"):
        data = build_feature_dataset(history, feature_preset(preset), TargetConfig(task="binary"))
        if preset == "without_time":
            assert not any("_sin" in name for name in data.features.feature_names)
        if preset == "without_volume":
            assert "volume_change" not in data.features.feature_names
        if preset == "returns_only":
            assert len(data.features.feature_names) == 8
            assert data.features.warmup_rows == 20
