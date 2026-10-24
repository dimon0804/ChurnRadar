import math

import numpy as np
import pytest

from tools.calibration import metrics


def test_roc_auc_perfect_inverse_and_ties():
    y = np.array([0, 0, 1, 1])
    assert metrics.roc_auc(y, np.array([0.1, 0.2, 0.8, 0.9])) == 1.0
    assert metrics.roc_auc(y, np.array([0.9, 0.8, 0.2, 0.1])) == 0.0
    assert metrics.roc_auc(y, np.array([0.5, 0.5, 0.5, 0.5])) == 0.5


def test_roc_auc_hand_example():
    # Пары позитив-негатив: 0.8>0.1, 0.8>0.4, 0.35>0.1, 0.35<0.4, итого 3 из 4.
    y = np.array([0, 0, 1, 1])
    assert metrics.roc_auc(y, np.array([0.1, 0.4, 0.35, 0.8])) == pytest.approx(0.75)


def test_roc_auc_single_class_is_nan():
    assert math.isnan(metrics.roc_auc(np.array([1, 1]), np.array([0.1, 0.2])))


def test_average_precision_hand_example():
    # Порядок 1, 0, 1: AP = 0.5 * 1 + 0.5 * 2/3.
    y = np.array([1, 0, 1])
    assert metrics.average_precision(y, np.array([0.9, 0.5, 0.1])) == pytest.approx(0.5 + 1 / 3)


def test_roc_curve_ends():
    fpr, tpr = metrics.roc_curve(np.array([0, 1, 0, 1]), np.array([0.2, 0.7, 0.4, 0.9]))
    assert (fpr[0], tpr[0]) == (0.0, 0.0)
    assert (fpr[-1], tpr[-1]) == (1.0, 1.0)
    assert np.all(np.diff(fpr) >= 0)
    assert np.all(np.diff(tpr) >= 0)


def test_matches_sklearn_on_random_data():
    sk = pytest.importorskip("sklearn.metrics")
    rng = np.random.default_rng(0)
    score = np.round(rng.random(2000), 2)  # с ничьими
    y = rng.random(2000) < score * 0.6
    assert metrics.roc_auc(y, score) == pytest.approx(sk.roc_auc_score(y, score))
    assert metrics.average_precision(y, score) == pytest.approx(sk.average_precision_score(y, score))


def test_precision_and_recall_at_top_share():
    y = np.array([1, 1, 0, 0, 0, 0, 0, 0, 0, 0])
    score = np.arange(10, 0, -1, dtype=float)
    rng = np.random.default_rng(1)
    assert metrics.precision_at(y, score, 0.2, rng) == 1.0
    assert metrics.recall_at(y, score, 0.1, rng) == 0.5


def test_calibration_table_weights_by_revenue():
    score = np.array([10.0, 20.0, 80.0, 90.0])
    predicted = np.array([1.0, 2.0, 80.0, 9.0])
    revenue = np.array([10.0, 10.0, 100.0, 10.0])
    actual = np.array([0.0, 10.0, 50.0, 10.0])
    rows = metrics.calibration_table(score, predicted, revenue, actual, bins=2)
    assert [r["n"] for r in rows] == [2, 2]
    assert rows[0]["predicted_share"] == pytest.approx(3 / 20)
    assert rows[0]["actual_share"] == pytest.approx(10 / 20)
    assert rows[1]["predicted_share"] == pytest.approx(89 / 110)
    assert rows[1]["actual_share"] == pytest.approx(60 / 110)


def test_bootstrap_ci_contains_point_estimate():
    rng = np.random.default_rng(3)
    score = rng.random(500)
    y = rng.random(500) < score
    low, high = metrics.bootstrap_ci(metrics.roc_auc, y, score, 100, np.random.default_rng(4))
    assert low < metrics.roc_auc(y, score) < high
