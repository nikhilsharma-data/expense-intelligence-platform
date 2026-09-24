import pytest

from main import (
    safe_number,
    parse_money,
    categorize,
    get_date_filter,
    build_date_clause,
    parse_dr_cr_text_line,
    parse_pdf_text_transactions,
)


# ---------------------------------------------------
# safe_number
# ---------------------------------------------------

def test_safe_number_with_value():
    assert safe_number(100) == 100.0


def test_safe_number_with_none():
    assert safe_number(None) == 0.0


# ---------------------------------------------------
# parse_money
# ---------------------------------------------------

def test_parse_money_with_commas():
    assert parse_money("1,250.50") == 1250.50


def test_parse_money_with_rupee_symbol():
    assert parse_money("₹2,500") == 2500.0


def test_parse_money_with_inr():
    assert parse_money("INR 10,000") == 10000.0


def test_parse_money_empty_value():
    assert parse_money("") is None


def test_parse_money_invalid_value():
    assert parse_money("ABC") is None


# ---------------------------------------------------
# categorize
# ---------------------------------------------------

@pytest.mark.parametrize(
    "description, expected_category",
    [
        ("SWIGGY FOOD ORDER", "Food"),
        ("ZOMATO ONLINE", "Food"),
        ("UBER TRIP", "Transport"),
        ("DMART PURCHASE", "Groceries"),
        ("NETFLIX SUBSCRIPTION", "Subscriptions"),
        ("AMAZON PURCHASE", "Shopping"),
        ("SALARY CREDIT", "Income"),
        ("ATM CASH WITHDRAWAL", "Cash Withdrawal"),
        ("MUTUAL FUND SIP", "Investments"),
        ("ELECTRICITY BILL", "Utilities"),
    ],
)
def test_categorize(description, expected_category):
    assert categorize(description) == expected_category


def test_categorize_unknown_transaction():
    assert categorize("XYZ RANDOM MERCHANT 123") == "Others"


# ---------------------------------------------------
# get_date_filter
# ---------------------------------------------------

def test_get_date_filter_all():
    assert get_date_filter("all") is None


def test_get_date_filter_30_days():
    result = get_date_filter("30d")
    assert result is not None


def test_get_date_filter_90_days():
    result = get_date_filter("90d")
    assert result is not None


def test_get_date_filter_ytd():
    result = get_date_filter("ytd")
    assert result is not None


# ---------------------------------------------------
# build_date_clause
# ---------------------------------------------------

def test_build_date_clause_without_date():
    clause, params = build_date_clause(None)

    assert clause == ""
    assert params == ()


def test_build_date_clause_with_date():
    test_date = get_date_filter("30d")

    clause, params = build_date_clause(test_date)

    assert clause == " AND date >= %s"
    assert params == (test_date,)


# ---------------------------------------------------
# PDF line parsing
# ---------------------------------------------------

def test_parse_dr_cr_text_line_debit():
    line = "01/01/2026 SWIGGY FOOD 450.00(Dr) 10,000.00(Dr)"

    result = parse_dr_cr_text_line(line)

    assert result is not None

    date_value, description, amount = result

    assert date_value == "01/01/2026"
    assert "SWIGGY" in description
    assert amount == -450.00


def test_parse_dr_cr_text_line_credit():
    line = "02/01/2026 SALARY CREDIT 60000.00(Cr) 70000.00(Cr)"

    result = parse_dr_cr_text_line(line)

    assert result is not None

    date_value, description, amount = result

    assert date_value == "02/01/2026"
    assert "SALARY" in description
    assert amount == 60000.00


def test_parse_dr_cr_text_line_invalid():
    line = "This is not a transaction"

    assert parse_dr_cr_text_line(line) is None


# ---------------------------------------------------
# PDF text transaction parsing
# ---------------------------------------------------

def test_parse_pdf_text_transactions():
    text = """
    01/01/2026 SWIGGY FOOD 450.00(Dr) 10,000.00(Dr)
    02/01/2026 SALARY CREDIT 60000.00(Cr) 70000.00(Cr)
    """

    transactions = parse_pdf_text_transactions(text)

    assert len(transactions) == 2

    assert transactions[0][2] == -450.00
    assert transactions[1][2] == 60000.00