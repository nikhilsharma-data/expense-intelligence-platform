"""Deterministic forecasting and unusual-spending explanations."""
from __future__ import annotations

from collections import defaultdict
from datetime import date, datetime
from math import isfinite, sqrt
from statistics import median


def _as_date(value):
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    if isinstance(value, str):
        try:
            return date.fromisoformat(value[:10])
        except ValueError:
            return None
    return None


def _monthly_series(rows):
    totals = defaultdict(float)
    transaction_counts = defaultdict(int)
    for txn_date, amount in rows:
        txn_date = _as_date(txn_date)
        try:
            amount = float(amount)
        except (TypeError, ValueError):
            continue
        if txn_date is None or not isfinite(amount) or amount >= 0:
            continue
        key = (txn_date.year, txn_date.month)
        totals[key] += abs(amount)
        transaction_counts[key] += 1
    if not totals:
        return [], [], 0

    first_year, first_month = min(totals)
    last_year, last_month = max(totals)
    points = []
    amounts = []
    year, month = first_year, first_month
    while (year, month) <= (last_year, last_month):
        key = (year, month)
        points.append({
            "month": f"{year:04d}-{month:02d}",
            "total": round(totals.get(key, 0.0), 2),
            "transactions": transaction_counts.get(key, 0),
        })
        amounts.append(totals.get(key, 0.0))
        if month == 12:
            year, month = year + 1, 1
        else:
            month += 1
    return points, amounts, sum(transaction_counts.values())


def evaluate_monthly_forecast(months, values, window=3, minimum_periods=2):
    """Compare rolling means to the last-month baseline at chronological origins.

    Each target month is predicted using earlier observed calendar months only.
    Missing calendar months must already be represented as zero in ``values``.
    Metrics are withheld until there are at least ``minimum_periods`` holdouts.
    """
    predictions = []
    baseline_predictions = []
    actuals = []
    splits = []
    for target_index in range(2, len(values)):
        train_start = max(0, target_index - window)
        train_values = values[train_start:target_index]
        if not train_values:
            continue
        predicted = sum(train_values) / len(train_values)
        baseline = values[target_index - 1]
        actual = values[target_index]
        predictions.append(predicted)
        baseline_predictions.append(baseline)
        actuals.append(actual)
        splits.append({
            "train_through": months[target_index - 1],
            "test_month": months[target_index],
            "forecast": round(predicted, 2),
            "baseline_forecast": round(baseline, 2),
            "actual": round(actual, 2),
        })

    result = {
        "available": len(actuals) >= minimum_periods,
        "periods": len(actuals),
        "minimum_periods": minimum_periods,
        "method": "Chronological expanding-origin evaluation; each target uses earlier months only.",
        "splits": splits,
        "mae": None,
        "rmse": None,
        "baseline_mae": None,
        "baseline_rmse": None,
        "reason": None,
    }
    if not result["available"]:
        result["reason"] = (
            f"At least {minimum_periods} held-out months are required to report metrics; "
            f"{len(actuals)} available."
        )
        return result

    def errors(predicted):
        residuals = [actual - estimate for actual, estimate in zip(actuals, predicted)]
        mae = sum(abs(value) for value in residuals) / len(residuals)
        rmse = sqrt(sum(value * value for value in residuals) / len(residuals))
        return round(mae, 2), round(rmse, 2)

    result["mae"], result["rmse"] = errors(predictions)
    result["baseline_mae"], result["baseline_rmse"] = errors(baseline_predictions)
    return result


def monthly_spending_forecast(rows, minimum_history=3, window=3, minimum_evaluation_periods=2):
    """Forecast next month's expense total with a recent-month mean.

    Amounts follow the app's signed convention: expenses are negative. Forecast
    values preserve imported units because the database has no currency field.
    """
    history, values, transaction_count = _monthly_series(rows)
    base = {
        "horizon_months": 1,
        "history_months": len(history),
        "history_transactions": transaction_count,
        "historical_actuals": history,
        "currency": "Unknown; amounts are shown in imported units.",
        "evaluation": evaluate_monthly_forecast(
            [item["month"] for item in history], values,
            window=window, minimum_periods=minimum_evaluation_periods,
        ),
    }
    if len(values) < minimum_history:
        base.update({
            "available": False,
            "reason": (f"A forecast needs at least {minimum_history} calendar months of expense history; "
                       f"{len(values)} available. Missing months between transactions count as zero."),
            "method": f"A one-month forecast uses the arithmetic mean of up to the latest {window} calendar months.",
            "forecast_month": None,
            "forecast_amount": None,
            "baseline_amount": None,
        })
        return base

    recent = values[-window:]
    last_year, last_month = map(int, history[-1]["month"].split("-"))
    next_month = f"{last_year + (last_month == 12):04d}-{1 if last_month == 12 else last_month + 1:02d}"
    base.update({
        "available": True,
        "reason": None,
        "forecast_month": next_month,
        "forecast_amount": round(sum(recent) / len(recent), 2),
        "baseline_amount": round(values[-1], 2),
        "method": (f"One month ahead: arithmetic mean of the latest {len(recent)} calendar months "
                   "of expenses, including zero-spend months. The baseline uses the last observed month."),
        "limitations": "Small-sample historical estimate. It has no confidence interval or accuracy guarantee.",
    })
    return base


def detect_unusual_spending(rows, minimum_history=3, threshold=3.5):
    """Flag unusually high transaction amounts against earlier same-category data."""
    transactions = []
    for txn_date, description, category, amount in rows:
        txn_date = _as_date(txn_date)
        try:
            amount = float(amount)
        except (TypeError, ValueError):
            continue
        if txn_date is None or not isfinite(amount) or amount >= 0:
            continue
        transactions.append({
            "date": txn_date.isoformat(),
            "description": str(description or ""),
            "category": str(category or "Others"),
            "amount": abs(amount),
        })
    transactions.sort(key=lambda item: item["date"])

    flags = []
    for txn in transactions:
        history = [
            prior["amount"] for prior in transactions
            if prior["category"] == txn["category"] and prior["date"] < txn["date"]
        ]
        if len(history) < minimum_history:
            continue
        center = median(history)
        mad = median([abs(value - center) for value in history])
        scale = 1.4826 * mad
        if scale == 0:
            threshold_amount = max(center * 2, center + 100)
            score = None
            unusual = txn["amount"] > threshold_amount
            threshold_reason = f"amount above {threshold_amount:.2f} (zero historical MAD rule)"
        else:
            threshold_amount = center + threshold * scale
            score = (txn["amount"] - center) / scale
            unusual = score >= threshold
            threshold_reason = f"robust deviation at least {threshold:.1f}"
        if not unusual:
            continue
        flags.append({
            **txn,
            "possible_anomaly": True,
            "historical_sample_size": len(history),
            "historical_median": round(center, 2),
            "historical_mad": round(mad, 2),
            "threshold_amount": round(threshold_amount, 2),
            "robust_deviation": None if score is None else round(score, 2),
            "reason": (f"{txn['category']} amount {txn['amount']:.2f} exceeds the {threshold_reason}; "
                       f"reference: {len(history)} earlier same-category transactions, "
                       f"median {center:.2f}, MAD {mad:.2f}. This is a possible unusual expense, not confirmed fraud."),
        })
    return {
        "flags": flags,
        "minimum_history": minimum_history,
        "label": "Possible unusual spending only; flags are not confirmed fraud.",
        "sparse_history_message": f"At least {minimum_history} earlier same-category transactions are required before a flag can be considered.",
    }
