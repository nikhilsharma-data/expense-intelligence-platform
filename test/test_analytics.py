from datetime import date

from analytics import (
    detect_unusual_spending,
    evaluate_monthly_forecast,
    monthly_spending_forecast,
)


def test_chronological_rolling_origins_compare_both_methods():
    result = evaluate_monthly_forecast(
        ["2026-01", "2026-02", "2026-03", "2026-04"],
        [0, 0, 10, 0],
    )
    assert result["available"] is True
    assert result["periods"] == 2
    assert result["splits"][0]["train_through"] == "2026-02"
    assert result["splits"][0]["test_month"] == "2026-03"
    assert result["splits"][0]["forecast"] == 0
    assert result["splits"][0]["actual"] == 10
    assert result["splits"][1]["train_through"] == "2026-03"
    assert result["splits"][1]["test_month"] == "2026-04"
    assert result["mae"] == 6.67
    assert result["rmse"] == 7.45
    assert result["baseline_mae"] == 10
    assert result["baseline_rmse"] == 10


def test_metrics_are_withheld_when_too_few_holdouts_exist():
    result = evaluate_monthly_forecast(["2026-01", "2026-02", "2026-03"], [10, 20, 0])
    assert result["available"] is False
    assert result["periods"] == 1
    assert result["mae"] is None
    assert result["baseline_rmse"] is None
    assert "At least 2 held-out months" in result["reason"]


def test_forecast_handles_empty_sparse_history_and_reports_requirements():
    empty = monthly_spending_forecast([])
    assert empty["available"] is False
    assert empty["history_months"] == 0
    assert empty["evaluation"]["available"] is False

    sparse = monthly_spending_forecast([(date(2026, 1, 1), -20)])
    assert sparse["available"] is False
    assert "at least 3 calendar months" in sparse["reason"]
    assert sparse["forecast_amount"] is None


def test_forecast_includes_horizon_actuals_and_plain_language_explanation():
    rows = [
        (date(2026, 1, 1), -100),
        (date(2026, 3, 1), -300),
        (date(2026, 4, 1), -500),
        (date(2026, 5, 1), -400),
    ]
    result = monthly_spending_forecast(rows)
    assert result["available"] is True
    assert result["horizon_months"] == 1
    assert result["history_months"] == 5  # February is represented as zero.
    assert result["history_transactions"] == 4
    assert result["historical_actuals"][1] == {"month": "2026-02", "total": 0.0, "transactions": 0}
    assert result["forecast_month"] == "2026-06"
    assert result["forecast_amount"] == 400
    assert "arithmetic mean" in result["method"]
    assert result["evaluation"]["periods"] == 3
    assert result["currency"].startswith("Unknown")


def test_anomaly_detection_handles_empty_and_sparse_history_without_claims():
    assert detect_unusual_spending([])["flags"] == []
    rows = [(date(2026, 1, i), "shop", "Shopping", -10) for i in range(1, 4)]
    result = detect_unusual_spending(rows)
    assert result["flags"] == []
    assert "At least 3 earlier" in result["sparse_history_message"]
    assert "fraud" in result["label"]


def test_anomaly_explanation_includes_historical_reference_and_threshold():
    rows = [(date(2026, 1, i), "shop", "Shopping", -10) for i in range(1, 4)]
    rows.append((date(2026, 1, 4), "large shop", "Shopping", -1000))
    result = detect_unusual_spending(rows)
    assert len(result["flags"]) == 1
    flag = result["flags"][0]
    assert flag["historical_sample_size"] == 3
    assert flag["historical_median"] == 10
    assert flag["threshold_amount"] == 110
    assert "3 earlier same-category transactions" in flag["reason"]
    assert "not confirmed fraud" in flag["reason"]
