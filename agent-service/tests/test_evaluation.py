from decimal import Decimal

from app.evaluation import compare_records, extract_dataframe_rows


def test_compare_records_ignores_row_and_column_order():
    actual = [{"amount": "10.0000001", "region": "华东"}, {"amount": 5, "region": "华北"}]
    expected = [{"region": "华北", "amount": 5.0}, {"region": "华东", "amount": 10}]
    assert compare_records(actual, expected, tolerance=Decimal("0.000001")) == (True, None)


def test_compare_records_rejects_missing_column():
    equivalent, message = compare_records([{"amount": 10}], [{"amount": 10, "region": "华东"}])
    assert not equivalent
    assert "unexpected row" in message


def test_compare_records_preserves_duplicate_rows():
    equivalent, message = compare_records([{"count": 1}, {"count": 1}], [{"count": 1}, {"count": 2}])
    assert not equivalent
    assert "unexpected row" in message


def test_extract_dataframe_rows_only_accepts_safe_query_component():
    chunks = [
        {"rich": {"type": "dataframe", "data": {"title": "Other", "data": [{"x": 0}]}}},
        {"rich": {"type": "dataframe", "data": {"title": "安全查询结果", "data": [{"x": 1}]}}},
    ]
    assert extract_dataframe_rows(chunks) == [{"x": 1}]
