import json
from itertools import pairwise

from tools.calibration import run


def _load(path):
    return json.loads(path.read_text(encoding="utf-8"))


def test_end_to_end_small(tmp_path, capsys):
    code = run.main(
        [
            "--accounts",
            "400",
            "--seed",
            "5",
            "--bootstrap",
            "5",
            "--out",
            str(tmp_path / "out"),
            "--img",
            str(tmp_path / "img"),
        ]
    )
    assert code == 0
    report = _load(tmp_path / "out" / "results.json")
    # NaN не валиден в JSON: панель и любые другие читатели получают null
    assert "NaN" not in (tmp_path / "out" / "results.json").read_text(encoding="utf-8")
    assert set(report["scenarios"]) == {"stream", "calendar"}
    for scenario in report["scenarios"].values():
        assert 0.0 <= scenario["ranking"]["churned"]["model"]["roc_auc"] <= 1.0
        levels = {row["level"]: row for row in scenario["levels"]}
        assert levels["all"]["n"] == scenario["accounts"]
        assert sum(levels[lv]["n"] for lv in ("low", "medium", "high")) == scenario["accounts"]
        assert len(scenario["calibration"]) == 10
        roc = scenario["roc"]
        assert len(roc["fpr"]) == 101
        assert set(roc["tpr"]) == {"model", "autoprolong_off", "prolong_wait", "segment_oracle"}
        curve = roc["tpr"]["model"]
        assert curve[-1] == 1.0
        assert all(a <= b for a, b in pairwise(curve))
        high = scenario["thresholds"]["high"]
        assert high["flagged"] == levels["high"]["n"]
        assert abs(high["churned"]["precision"] - levels["high"]["churn_rate"]) < 1e-9
    labels = [row["label"] for row in report["robustness"]]
    assert labels[0] == "seed 5 (основной)"
    assert len(labels) == 6
    assert set(report["robustness"][3]["scenarios"]) == {"stream"}
    for name in ("roc.png", "levels.png", "calibration.png", "money.png"):
        assert (tmp_path / "img" / name).stat().st_size > 10_000
    assert "## Сценарий stream" in capsys.readouterr().out


def test_same_seed_same_numbers(tmp_path):
    args = ["--accounts", "300", "--seed", "9", "--bootstrap", "0", "--no-plots", "--no-robustness"]
    args += ["--scenarios", "calendar"]
    run.main([*args, "--out", str(tmp_path / "a")])
    run.main([*args, "--out", str(tmp_path / "b")])
    a = _load(tmp_path / "a" / "results.json")
    b = _load(tmp_path / "b" / "results.json")
    a.pop("elapsed_s")
    b.pop("elapsed_s")
    assert a == b
