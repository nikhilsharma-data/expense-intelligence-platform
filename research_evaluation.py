"""Reproducible synthetic evaluations for the platform's analytics methods.

Scores from these examples are teaching/demo results, not estimates of
performance on real bank statements or population data.
"""

from collections import Counter, defaultdict
from datetime import date
from math import log
import re

from analytics import detect_unusual_spending


CATEGORIES = (
    "Income",
    "Rent",
    "Food",
    "Groceries",
    "Transport",
    "Fuel",
    "Utilities",
    "Healthcare",
    "Insurance",
    "Investments",
    "Subscriptions",
    "Shopping",
    "EMI & Loans",
    "Credit Card Payment",
    "Cash Withdrawal",
    "Vehicle",
    "Transfers",
    "Bank Charges",
    "Others",
)


# Fixed, manually labelled examples. Each category has one training example
# followed by two holdout examples; no random split or evaluation-text fitting.
CATEGORIZATION_DATA = (
    ("SALARY CREDIT ACME PAYROLL", "Income", "train"),
    ("MONTHLY SALARY DEPOSIT", "Income", "evaluation"),
    ("NEFT CR EMPLOYER", "Income", "evaluation"),
    ("HOUSE RENT TRANSFER", "Rent", "train"),
    ("LANDLORD RENT PAYMENT", "Rent", "evaluation"),
    ("MONTHLY HOUSE RENT", "Rent", "evaluation"),
    ("SWIGGY FOOD ORDER", "Food", "train"),
    ("ZOMATO DINNER ORDER", "Food", "evaluation"),
    ("CAFE LUNCH", "Food", "evaluation"),
    ("DMART GROCERY PURCHASE", "Groceries", "train"),
    ("SUPERMARKET GROCERIES", "Groceries", "evaluation"),
    ("RELIANCE FRESH SHOPPING", "Groceries", "evaluation"),
    ("UBER RIDE FARE", "Transport", "train"),
    ("METRO CARD RECHARGE", "Transport", "evaluation"),
    ("RAILWAY TICKET", "Transport", "evaluation"),
    ("PETROL PUMP FUEL", "Fuel", "train"),
    ("DIESEL STATION PAYMENT", "Fuel", "evaluation"),
    ("IOCL PETROL", "Fuel", "evaluation"),
    ("ELECTRICITY BILL", "Utilities", "train"),
    ("BROADBAND INTERNET BILL", "Utilities", "evaluation"),
    ("AIRTEL MOBILE BILL", "Utilities", "evaluation"),
    ("PHARMACY MEDICINE", "Healthcare", "train"),
    ("CLINIC DOCTOR VISIT", "Healthcare", "evaluation"),
    ("HOSPITAL LAB TEST", "Healthcare", "evaluation"),
    ("HOMEINSURE PREMIUM", "Insurance", "train"),
    ("ZENITHINSURE POLICY", "Insurance", "evaluation"),
    ("LIFE INSURANCE PREMIUM", "Insurance", "evaluation"),
    ("MUTUAL FUND SIP", "Investments", "train"),
    ("ZERODHA EQUITY INVESTMENT", "Investments", "evaluation"),
    ("NPS CONTRIBUTION", "Investments", "evaluation"),
    ("NETFLIX SUBSCRIPTION", "Subscriptions", "train"),
    ("SPOTIFY PREMIUM", "Subscriptions", "evaluation"),
    ("YOUTUBE STREAMING", "Subscriptions", "evaluation"),
    ("AMAZON ORDER", "Shopping", "train"),
    ("FLIPKART PURCHASE", "Shopping", "evaluation"),
    ("MYNTRA RETAIL", "Shopping", "evaluation"),
    ("HOME LOAN EMI", "EMI & Loans", "train"),
    ("PERSONAL LOAN PAYMENT", "EMI & Loans", "evaluation"),
    ("VEHICLELOAN INSTALLMENT", "EMI & Loans", "evaluation"),
    ("CREDIT CARD PAYMENT", "Credit Card Payment", "train"),
    ("CC9876 SETTLEMENT", "Credit Card Payment", "evaluation"),
    ("MOPSOTHDRCARD BILL", "Credit Card Payment", "evaluation"),
    ("ATM CASH WDL", "Cash Withdrawal", "train"),
    ("CASH WITHDRAWAL", "Cash Withdrawal", "evaluation"),
    ("ATL/ BRANCH WITHDRAWAL", "Cash Withdrawal", "evaluation"),
    ("APEX MOTORS SERVICE", "Vehicle", "train"),
    ("GARAGE REPAIR", "Vehicle", "evaluation"),
    ("VEHICLE MAINTENANCE", "Vehicle", "evaluation"),
    ("UPI TRANSFER FRIEND", "Transfers", "train"),
    ("PAYTM WALLET TRANSFER", "Transfers", "evaluation"),
    ("NEFT DR TRANSFER", "Transfers", "evaluation"),
    ("SMS ALERT FEE", "Bank Charges", "train"),
    ("MIN BAL CHARGE", "Bank Charges", "evaluation"),
    ("SERVICE CHARGE PENALTY", "Bank Charges", "evaluation"),
    ("UNKNOWN MERCHANT 42", "Others", "train"),
    ("COMMUNITY DONATION", "Others", "evaluation"),
    ("CITY LIBRARY CONTRIBUTION", "Others", "evaluation"),
)


# Separately curated synthetic challenge descriptions. Labels represent the
# intended transaction purpose, assigned by the author before running either
# classifier. Descriptions use merchant/context variants outside the current
# rule vocabulary; they are not observations from a real financial dataset.
CATEGORIZATION_INDEPENDENT_DATA = (
    ("ACME PAYROLL REF 7841", "Income"),
    ("EMPLOYER CREDIT MONTHLY 2640", "Income"),
    ("LEASE 12B MAY 2026", "Rent"),
    ("RESIDENCE OCCUPANCY 8F", "Rent"),
    ("KHANA JUNCTION POS 9482", "Food"),
    ("URBAN TANDOOR CARD 1138", "Food"),
    ("FRESHCART ORDER 491", "Groceries"),
    ("GREENBASKET WEEKLY 72", "Groceries"),
    ("CITYRIDE TRIP 8841", "Transport"),
    ("COMMUTER PASS REF 1120", "Transport"),
    ("SHELL V-POWER STATION 388", "Fuel"),
    ("FORECOURT RECEIPT PUMP 0261", "Fuel"),
    ("AQUAGRID ACCT 5503", "Utilities"),
    ("NORTHLINE POWER ACCT 3382", "Utilities"),
    ("APOLLO RX ORDER 075", "Healthcare"),
    ("CITYCARE OUTPATIENT REF 2516", "Healthcare"),
    ("TATA AIG POLICY 0309", "Insurance"),
    ("LIFE COVER REF 6017", "Insurance"),
    ("NIFTY50 AUTO-DEBIT 918", "Investments"),
    ("BSE ETF ALLOTMENT 317", "Investments"),
    ("CLOUDBOX RECURRING PLAN 492", "Subscriptions"),
    ("VIMEO CREATOR PLAN 217", "Subscriptions"),
    ("DECATHLON ORDER 7735", "Shopping"),
    ("UNIQLO POS 0832", "Shopping"),
    ("HOMEFINANCE INSTALMENT 12 OF 36", "EMI & Loans"),
    ("AUTO FINANCE REPAYMENT 5042", "EMI & Loans"),
    ("VISA AUTOPAY STATEMENT 83", "Credit Card Payment"),
    ("AMEX BALANCE SETTLEMENT 2801", "Credit Card Payment"),
    ("BRANCH COUNTER PAYOUT 024", "Cash Withdrawal"),
    ("CASH DISPENSER TXN 883", "Cash Withdrawal"),
    ("TIRE ALIGNMENT INVOICE 440", "Vehicle"),
    ("AUTOCARE REPAIR 6192", "Vehicle"),
    ("BENEFICIARY SWEEP TO A KUMAR", "Transfers"),
    ("BENEFICIARY PAYOUT BANK REF 31", "Transfers"),
    ("LEDGER MAINTENANCE DEBIT 240", "Bank Charges"),
    ("ACCOUNT UPKEEP MONTHLY 18", "Bank Charges"),
    ("CITY MUSEUM ENTRY 02", "Others"),
    ("COMMUNITY THEATER TICKET 61", "Others"),
)


# Hand-labeled scenarios describe transaction context, not the detector's
# threshold outcome. Expected labels are intentionally mixed with near-boundary
# and moderate cases so this remains an evaluation rather than a perfect-score
# fixture.
ANOMALY_REFERENCES = (
    (date(2026, 1, 1), "food-reference-1", "Food", -10),
    (date(2026, 1, 2), "food-reference-2", "Food", -10),
    (date(2026, 1, 3), "food-reference-3", "Food", -10),
    (date(2026, 1, 1), "ride-reference-1", "Transport", -35),
    (date(2026, 1, 2), "ride-reference-2", "Transport", -50),
    (date(2026, 1, 3), "ride-reference-3", "Transport", -65),
    (date(2026, 1, 1), "market-reference-1", "Groceries", -30),
    (date(2026, 1, 2), "market-reference-2", "Groceries", -35),
    (date(2026, 1, 3), "market-reference-3", "Groceries", -40),
    (date(2026, 1, 1), "shop-reference-1", "Shopping", -100),
    (date(2026, 1, 2), "shop-reference-2", "Shopping", -120),
    (date(2026, 1, 3), "shop-reference-3", "Shopping", -140),
    (date(2026, 1, 1), "utility-reference-1", "Utilities", -20),
    (date(2026, 1, 2), "utility-reference-2", "Utilities", -35),
    (date(2026, 1, 1), "insurance-reference-1", "Insurance", -25),
)

ANOMALY_TARGETS = (
    # A normal meal and a disputed group charge near the zero-MAD threshold.
    (date(2026, 1, 4), "food-ordinary-meal", "Food", -18, False, "Routine meal"),
    (date(2026, 1, 5), "food-disputed-group-charge", "Food", -95, True, "Manually labeled disputed charge"),
    (date(2026, 1, 6), "food-catering-order", "Food", -180, True, "Manually labeled unusual catering order"),
    # Transport history has nonzero MAD. A moderate unusual fare is expected
    # even though the detector may not cross its robust-score cutoff.
    (date(2026, 1, 4), "ride-ordinary-fare", "Transport", -70, False, "Routine fare"),
    (date(2026, 1, 5), "ride-disputed-fare", "Transport", -115, True, "Manually labeled disputed fare"),
    (date(2026, 1, 6), "ride-rare-route-fare", "Transport", -180, True, "Manually labeled unusual route fare"),
    # A legitimate bulk purchase is included to measure a plausible false alarm.
    (date(2026, 1, 4), "market-legitimate-stock-up", "Groceries", -95, False, "Legitimate household stock-up"),
    # Near the nonzero-MAD cutoff, but labeled as a legitimate planned purchase.
    (date(2026, 1, 4), "shop-planned-appliance", "Shopping", -220, False, "Planned household purchase"),
    # Expected unusual, but explicitly unassessable with fewer than 3 prior rows.
    (date(2026, 1, 3), "utility-unexpected-debit", "Utilities", -500, True, "Manually labeled unexpected debit"),
    (date(2026, 1, 2), "insurance-unexpected-debit", "Insurance", -500, True, "Manually labeled unexpected debit"),
)

ANOMALY_FUTURE_CONTEXT = (
    (date(2026, 1, 7), "food-future-context", "Food", -10000),
)


def _tokens(text):
    return re.findall(r"[a-z0-9]+", str(text).lower())


class _MultinomialNaiveBayes:
    """Small standard-library text classifier used as a reproducible comparator."""

    def fit(self, rows):
        self.document_counts = Counter()
        self.token_counts = defaultdict(Counter)
        self.total_tokens = Counter()
        self.vocabulary = set()
        for text, label in rows:
            tokens = _tokens(text)
            self.document_counts[label] += 1
            self.token_counts[label].update(tokens)
            self.total_tokens[label] += len(tokens)
            self.vocabulary.update(tokens)
        self.labels = tuple(sorted(self.document_counts))
        self.total_documents = sum(self.document_counts.values())
        return self

    def predict(self, text):
        tokens = _tokens(text)
        vocab_size = max(1, len(self.vocabulary))
        scores = {}
        for label in self.labels:
            denominator = self.total_tokens[label] + vocab_size
            score = log(self.document_counts[label] / self.total_documents)
            score += sum(
                log((self.token_counts[label][token] + 1) / denominator)
                for token in tokens
            )
            scores[label] = score
        return max(self.labels, key=lambda label: scores[label])


def _multiclass_metrics(actual, predicted, labels):
    matrix = {label: {guess: 0 for guess in labels} for label in labels}
    for expected, guess in zip(actual, predicted):
        matrix[expected][guess] += 1

    per_class = {}
    for label in labels:
        true_positive = matrix[label][label]
        false_positive = sum(matrix[other][label] for other in labels if other != label)
        false_negative = sum(matrix[label][other] for other in labels if other != label)
        precision = true_positive / (true_positive + false_positive) if true_positive + false_positive else 0.0
        recall = true_positive / (true_positive + false_negative) if true_positive + false_negative else 0.0
        f1 = 2 * precision * recall / (precision + recall) if precision + recall else 0.0
        per_class[label] = {
            "precision": round(precision, 4),
            "recall": round(recall, 4),
            "f1": round(f1, 4),
            "support": sum(matrix[label].values()),
        }

    count = len(actual)
    return {
        "sample_count": count,
        "accuracy": round(sum(a == p for a, p in zip(actual, predicted)) / count, 4) if count else None,
        "precision_macro": round(sum(row["precision"] for row in per_class.values()) / len(labels), 4) if labels else None,
        "recall_macro": round(sum(row["recall"] for row in per_class.values()) / len(labels), 4) if labels else None,
        "f1_macro": round(sum(row["f1"] for row in per_class.values()) / len(labels), 4) if labels else None,
        "per_class": per_class,
        "confusion_matrix": matrix,
    }


def evaluate_categorization(rule_categorizer):
    training = [(text, label) for text, label, split in CATEGORIZATION_DATA if split == "train"]
    holdout = [(text, label) for text, label, split in CATEGORIZATION_DATA if split == "evaluation"]
    labels = list(CATEGORIES)
    actual = [label for _text, label in holdout]
    rule_predictions = [rule_categorizer(text) for text, _label in holdout]
    alternative = _MultinomialNaiveBayes().fit(training)
    alternative_predictions = [alternative.predict(text) for text, _label in holdout]
    distribution = Counter(label for _text, label, _split in CATEGORIZATION_DATA)

    labels = list(CATEGORIES)
    independent_training = [(text, label) for text, label, split in CATEGORIZATION_DATA if split == "train"]
    independent_holdout = list(CATEGORIZATION_INDEPENDENT_DATA)
    independent_actual = [label for _text, label in independent_holdout]
    independent_nb = _MultinomialNaiveBayes().fit(independent_training)
    independent_methods = {
        "keyword_rules_baseline": _multiclass_metrics(
            independent_actual,
            [rule_categorizer(text) for text, _label in independent_holdout],
            labels,
        ),
        "multinomial_naive_bayes": _multiclass_metrics(
            independent_actual,
            [independent_nb.predict(text) for text, _label in independent_holdout],
            labels,
        ),
    }

    return {
        "data_origin": "Synthetic, manually labelled examples; not real bank data.",
        "evaluation_label": "Rule-vocabulary coverage check; not an independent generalization estimate.",
        "methodology": (
            "Fixed category-stratified holdout: one synthetic training description and two "
            "evaluation descriptions per category. The naive Bayes vocabulary and token counts "
            "are learned from training descriptions only. No random split is used."
        ),
        "dataset_size": len(CATEGORIZATION_DATA),
        "training_size": len(training),
        "evaluation_size": len(holdout),
        "category_distribution": dict(distribution),
        "methods": {
            "keyword_rules_baseline": _multiclass_metrics(actual, rule_predictions, labels),
            "multinomial_naive_bayes": _multiclass_metrics(actual, alternative_predictions, labels),
        },
        "independent_synthetic_holdout": {
            "data_origin": "Separately authored synthetic transaction descriptions with manually assigned intended categories.",
            "methodology": (
                "Fixed 38-description challenge holdout, two examples per supported category. "
                "Labels were assigned from the intended transaction context before scoring; "
                "descriptions avoid the current keyword rules' explicit terms. The naive Bayes "
                "model uses the same 19 training examples as the coverage check."
            ),
            "dataset_size": len(independent_holdout),
            "evaluation_size": len(independent_holdout),
            "category_distribution": dict(Counter(label for _text, label in independent_holdout)),
            "methods": independent_methods,
            "limitations": (
                "This small synthetic challenge set is deliberately outside the current rule "
                "vocabulary. It probes vocabulary dependence but does not establish performance "
                "on real statements, new banks, or population data."
            ),
        },
        "limitations": (
            "This small synthetic set exercises known rule vocabulary and does not estimate "
            "performance on real statements, unseen banks, or changing merchant names."
        ),
    }


def evaluate_anomaly_detection(detector=detect_unusual_spending):
    """Score fixed, manually labeled synthetic targets after category history."""
    target_rows = [case[:4] for case in ANOMALY_TARGETS]
    rows = list(ANOMALY_REFERENCES) + target_rows + list(ANOMALY_FUTURE_CONTEXT)
    result = detector(rows)
    flagged = {
        (item["date"], item["description"], item["category"], round(item["amount"], 2))
        for item in result["flags"]
    }
    expected = []
    predicted = []
    cases = []
    for txn_date, description, category, signed_amount, label, rationale in ANOMALY_TARGETS:
        key = (txn_date.isoformat(), description, category, abs(signed_amount))
        history_count = sum(
            1 for prior_date, _prior_description, prior_category, prior_amount in rows
            if prior_category == category and prior_date < txn_date and prior_amount < 0
        )
        assessment = "scored" if history_count >= 3 else "insufficient_history"
        prediction = key in flagged if assessment == "scored" else None
        if assessment == "scored":
            expected.append(label)
            predicted.append(prediction)
        flag = next((item for item in result["flags"] if (
            item["date"], item["description"], item["category"], round(item["amount"], 2)
        ) == key), None)
        cases.append({
            "date": txn_date.isoformat(),
            "description": description,
            "category": category,
            "amount": abs(signed_amount),
            "expected_anomaly": label,
            "label_rationale": rationale,
            "predicted_anomaly": prediction,
            "assessment": assessment,
            "historical_sample_size": flag["historical_sample_size"] if flag else history_count,
            "explanation": flag["reason"] if flag else None,
            "historical_median": flag["historical_median"] if flag else None,
            "historical_mad": flag["historical_mad"] if flag else None,
            "threshold_amount": flag["threshold_amount"] if flag else None,
        })

    tp = sum(a and p for a, p in zip(expected, predicted))
    tn = sum(not a and not p for a, p in zip(expected, predicted))
    fp = sum(not a and p for a, p in zip(expected, predicted))
    fn = sum(a and not p for a, p in zip(expected, predicted))
    precision = tp / (tp + fp) if tp + fp else 0.0
    recall = tp / (tp + fn) if tp + fn else 0.0
    f1 = 2 * precision * recall / (precision + recall) if precision + recall else 0.0
    count = len(expected)
    return {
        "data_origin": "Synthetic labelled transactions; not real user or bank data.",
        "methodology": (
            "Fixed scenarios have explicit context labels assigned independently of the detector. "
            "They include zero and nonzero MAD histories, borderline and moderate amounts, an "
            "expected false alarm, and two expected anomalies with insufficient history. "
            "Insufficient-history targets are reported but excluded from precision/recall/F1; "
            "only prior same-category transactions can contribute to a target's history."
        ),
        "dataset_size": len(rows),
        "labelled_target_count": len(ANOMALY_TARGETS),
        "evaluation_size": count,
        "unscored_insufficient_history": len(ANOMALY_TARGETS) - count,
        "coverage": round(count / len(ANOMALY_TARGETS), 4) if ANOMALY_TARGETS else None,
        "category_distribution": dict(Counter(case[2] for case in ANOMALY_TARGETS)),
        "cases": cases,
        "accuracy": round((tp + tn) / count, 4) if count else None,
        "precision": round(precision, 4),
        "recall": round(recall, 4),
        "f1": round(f1, 4),
        "confusion_matrix": {"true_negative": tn, "false_positive": fp, "false_negative": fn, "true_positive": tp},
        "limitations": (
            "Synthetic cases use deliberately separated normal and unusual amounts and do not "
            "estimate real-world detection performance or false-alarm rates."
        ),
    }
