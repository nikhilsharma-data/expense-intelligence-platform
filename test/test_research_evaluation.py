from main import categorize
from research_evaluation import (
    CATEGORIZATION_DATA,
    CATEGORIZATION_INDEPENDENT_DATA,
    CATEGORIES,
    evaluate_anomaly_detection,
    evaluate_categorization,
)


def test_categorization_evaluation_is_fixed_held_out_and_covers_supported_categories():
    result = evaluate_categorization(categorize)

    assert result["dataset_size"] == 57
    assert result["training_size"] == 19
    assert result["evaluation_size"] == 38
    assert result["evaluation_label"].startswith("Rule-vocabulary coverage check")
    assert set(result["category_distribution"]) == set(CATEGORIES)
    assert set(result["methods"]) == {"keyword_rules_baseline", "multinomial_naive_bayes"}
    assert {
        name: (
            metrics["accuracy"],
            metrics["precision_macro"],
            metrics["recall_macro"],
            metrics["f1_macro"],
        )
        for name, metrics in result["methods"].items()
    } == {
        "keyword_rules_baseline": (1.0, 1.0, 1.0, 1.0),
        "multinomial_naive_bayes": (0.2368, 0.2421, 0.2368, 0.2043),
    }
    for metrics in result["methods"].values():
        assert metrics["sample_count"] == 38
        assert set(metrics["confusion_matrix"]) == set(CATEGORIES)
        assert 0 <= metrics["accuracy"] <= 1
        assert 0 <= metrics["precision_macro"] <= 1
        assert 0 <= metrics["recall_macro"] <= 1
        assert 0 <= metrics["f1_macro"] <= 1


def test_categorization_evaluation_split_has_no_text_overlap():
    training = {text for text, _label, split in CATEGORIZATION_DATA if split == "train"}
    evaluation = {text for text, _label, split in CATEGORIZATION_DATA if split == "evaluation"}
    assert len(training) == 19
    assert len(evaluation) == 38
    assert training.isdisjoint(evaluation)


def test_independent_categorization_holdout_avoids_rule_vocabulary_and_reports_metrics():
    result = evaluate_categorization(categorize)
    independent = result["independent_synthetic_holdout"]
    existing_texts = {text for text, _label, _split in CATEGORIZATION_DATA}
    challenge_texts = {text for text, _label in CATEGORIZATION_INDEPENDENT_DATA}

    assert len(CATEGORIZATION_INDEPENDENT_DATA) == 38
    assert challenge_texts.isdisjoint(existing_texts)
    assert set(label for _text, label in CATEGORIZATION_INDEPENDENT_DATA) == set(CATEGORIES)
    assert all(categorize(text) == "Others" for text in challenge_texts)
    assert independent["evaluation_size"] == 38
    assert set(independent["category_distribution"].values()) == {2}
    assert set(independent["methods"]) == {"keyword_rules_baseline", "multinomial_naive_bayes"}
    assert {
        name: (
            metrics["accuracy"],
            metrics["precision_macro"],
            metrics["recall_macro"],
            metrics["f1_macro"],
        )
        for name, metrics in independent["methods"].items()
    } == {
        "keyword_rules_baseline": (0.0526, 0.0028, 0.0526, 0.0053),
        "multinomial_naive_bayes": (0.1316, 0.1772, 0.1316, 0.1296),
    }
    for metrics in independent["methods"].values():
        assert metrics["sample_count"] == 38
        assert 0 <= metrics["accuracy"] <= 1
        assert 0 <= metrics["precision_macro"] <= 1
        assert 0 <= metrics["recall_macro"] <= 1
        assert 0 <= metrics["f1_macro"] <= 1


def test_anomaly_evaluation_reports_mixed_cases_and_withholds_sparse_history():
    result = evaluate_anomaly_detection()

    assert result["dataset_size"] == 26
    assert result["labelled_target_count"] == 10
    assert result["evaluation_size"] == 8
    assert result["unscored_insufficient_history"] == 2
    assert result["coverage"] == 0.8
    assert result["confusion_matrix"] == {
        "true_negative": 3,
        "false_positive": 1,
        "false_negative": 1,
        "true_positive": 3,
    }
    assert result["accuracy"] == 0.75
    assert result["precision"] == 0.75
    assert result["recall"] == 0.75
    assert result["f1"] == 0.75

    cases = {case["description"]: case for case in result["cases"]}
    assert cases["food-disputed-group-charge"]["expected_anomaly"] is True
    assert cases["food-disputed-group-charge"]["predicted_anomaly"] is False
    assert cases["market-legitimate-stock-up"]["expected_anomaly"] is False
    assert cases["market-legitimate-stock-up"]["predicted_anomaly"] is True
    assert cases["utility-unexpected-debit"]["assessment"] == "insufficient_history"
    assert cases["utility-unexpected-debit"]["predicted_anomaly"] is None


def test_anomaly_explanations_use_prior_history_and_report_mad_threshold():
    result = evaluate_anomaly_detection()
    cases = {case["description"]: case for case in result["cases"]}

    zero_mad = cases["food-catering-order"]
    assert zero_mad["predicted_anomaly"] is True
    assert zero_mad["historical_sample_size"] == 5
    assert zero_mad["historical_median"] == 10
    assert zero_mad["historical_mad"] == 0
    assert zero_mad["threshold_amount"] == 110
    assert "5 earlier same-category transactions" in zero_mad["explanation"]
    assert "not confirmed fraud" in zero_mad["explanation"]

    nonzero_mad = cases["ride-disputed-fare"]
    assert nonzero_mad["historical_sample_size"] == 4
    assert nonzero_mad["historical_mad"] == 10
    assert nonzero_mad["explanation"]
    assert "4 earlier same-category transactions" in nonzero_mad["explanation"]
