"""Custom metrics and strict assertions for Visual Anchor RAG evaluation."""

from __future__ import annotations

from dataclasses import dataclass
from io import StringIO
from typing import Any


@dataclass(frozen=True)
class TableCellExpectation:
    """Expected value for a specific markdown table cell."""

    row: str
    column: str
    value: str


def markdown_to_dataframe(markdown_table: str):
    """Parse a markdown table into a pandas DataFrame.

    Raises:
        AssertionError: If pandas is unavailable or table is invalid.
    """

    try:
        import pandas as pd
    except ImportError as exc:
        raise AssertionError("pandas is required for markdown table assertions.") from exc

    lines = [line.strip() for line in markdown_table.splitlines() if line.strip()]
    if len(lines) < 2:
        raise AssertionError("Markdown table must include header and separator lines.")

    normalized = []
    for line in lines:
        if not line.startswith("|"):
            continue
        normalized.append(line)

    if len(normalized) < 2:
        raise AssertionError("No valid markdown rows found.")

    csv_lines: list[str] = []
    for idx, line in enumerate(normalized):
        # Skip the markdown separator row.
        if idx == 1:
            continue
        columns = [segment.strip() for segment in line.strip("|").split("|")]
        escaped = [col.replace('"', '""') for col in columns]
        csv_lines.append(",".join(f'\"{col}\"' for col in escaped))

    dataframe = pd.read_csv(StringIO("\n".join(csv_lines)))
    dataframe.columns = [str(column).strip() for column in dataframe.columns]
    return dataframe


def assert_markdown_table_match(
    predicted: str,
    expected: str,
    key_cells: list[TableCellExpectation] | None = None,
) -> None:
    """Assert strict markdown table equivalence, including critical cells.

    Rules:
    - Header set and row count must match exactly.
    - Full-cell normalized matrix must match exactly.
    - Optional key-cells must match exact values.
    """

    predicted_df = markdown_to_dataframe(predicted)
    expected_df = markdown_to_dataframe(expected)

    if list(predicted_df.columns) != list(expected_df.columns):
        raise AssertionError(
            "Column mismatch between predicted and expected markdown tables. "
            f"Predicted={list(predicted_df.columns)} Expected={list(expected_df.columns)}"
        )

    if len(predicted_df.index) != len(expected_df.index):
        raise AssertionError(
            "Row count mismatch between predicted and expected markdown tables. "
            f"Predicted={len(predicted_df.index)} Expected={len(expected_df.index)}"
        )

    normalized_pred = predicted_df.fillna("").astype(str).apply(lambda column: column.map(lambda value: value.strip()))
    normalized_exp = expected_df.fillna("").astype(str).apply(lambda column: column.map(lambda value: value.strip()))

    if not normalized_pred.equals(normalized_exp):
        diff_rows: list[int] = []
        for idx in range(len(normalized_pred.index)):
            if not normalized_pred.iloc[idx].equals(normalized_exp.iloc[idx]):
                diff_rows.append(idx)
        raise AssertionError(f"Table content mismatch at rows: {diff_rows}")

    if key_cells:
        for expected_cell in key_cells:
            _assert_key_cell(normalized_pred, expected_cell)


def context_recall_score(retrieved_contexts: list[dict[str, Any]], expected_ids: list[str]) -> float:
    """Compute context recall for expected context IDs in top-k retrieval output."""

    if not expected_ids:
        return 1.0

    retrieved_ids = {str(item.get("id", "")) for item in retrieved_contexts}
    matches = sum(1 for expected_id in expected_ids if expected_id in retrieved_ids)
    return matches / len(expected_ids)


def _assert_key_cell(predicted_df, expectation: TableCellExpectation) -> None:
    """Assert exact expected value in a row/column identified by row label."""

    row_label_column = predicted_df.columns[0]
    matching_rows = predicted_df[predicted_df[row_label_column].str.strip() == expectation.row.strip()]
    if matching_rows.empty:
        raise AssertionError(f"Expected row '{expectation.row}' not found in predicted table.")

    if expectation.column not in predicted_df.columns:
        raise AssertionError(
            f"Expected column '{expectation.column}' not found. Available={list(predicted_df.columns)}"
        )

    actual = str(matching_rows.iloc[0][expectation.column]).strip()
    expected_value = expectation.value.strip()
    if actual != expected_value:
        raise AssertionError(
            f"Cell mismatch for row='{expectation.row}' column='{expectation.column}'. "
            f"Expected='{expected_value}' Actual='{actual}'"
        )
