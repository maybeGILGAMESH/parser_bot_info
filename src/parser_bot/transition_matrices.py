from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path

import pandas as pd

MATRIX_KEYWORDS = ("темпер", "осадки", "дефиц", "радиац")
DECADE_KEYWORD = "декада"


@dataclass(frozen=True, slots=True)
class MatrixSheetSummary:
    sheet_name: str
    metric_label: str
    inferred_kind: str
    non_empty_rows: int
    non_empty_cols: int
    year_from: int | None
    year_to: int | None
    year_count: int
    class_labels: tuple[str, ...]
    matrix_row_labels: tuple[str, ...]
    count_matrix: tuple[tuple[float | None, ...], ...]
    probability_matrix: tuple[tuple[float | None, ...], ...]
    decade_blocks: tuple[str, ...]
    preview_rows: tuple[tuple[str, ...], ...]


@dataclass(frozen=True, slots=True)
class MatrixWorkbookSummary:
    file_name: str
    file_path: str
    sheet_count: int
    inferred_purpose: str
    sheet_summaries: tuple[MatrixSheetSummary, ...]


def analyze_transition_workbooks(paths: list[str | Path]) -> list[MatrixWorkbookSummary]:
    summaries: list[MatrixWorkbookSummary] = []
    for path in paths:
        source = Path(path).expanduser()
        if not source.exists() or not source.is_file():
            continue
        summaries.append(analyze_transition_workbook(source))
    return summaries


def analyze_transition_workbook(path: str | Path) -> MatrixWorkbookSummary:
    source = Path(path).expanduser()
    workbook = pd.ExcelFile(source, engine="xlrd")
    sheet_summaries = tuple(analyze_transition_sheet(source, sheet_name) for sheet_name in workbook.sheet_names)
    return MatrixWorkbookSummary(
        file_name=source.name,
        file_path=str(source),
        sheet_count=len(sheet_summaries),
        inferred_purpose=_infer_workbook_purpose(source.name, sheet_summaries),
        sheet_summaries=sheet_summaries,
    )


def analyze_transition_sheet(path: str | Path, sheet_name: str) -> MatrixSheetSummary:
    frame = pd.read_excel(Path(path), sheet_name=sheet_name, header=None, engine="xlrd")
    non_empty_rows = int(frame.notna().any(axis=1).sum()) if not frame.empty else 0
    non_empty_cols = int(frame.notna().any(axis=0).sum()) if not frame.empty else 0
    metric_label = _extract_metric_label(frame, sheet_name)
    year_values = _extract_year_values(frame)
    class_labels, row_labels, count_matrix, probability_matrix = _extract_first_matrix(frame)
    decade_blocks = tuple(_extract_decade_blocks(frame))
    inferred_kind = _infer_sheet_kind(
        frame=frame,
        year_values=year_values,
        decade_blocks=decade_blocks,
        probability_matrix=probability_matrix,
        count_matrix=count_matrix,
    )
    return MatrixSheetSummary(
        sheet_name=sheet_name,
        metric_label=metric_label,
        inferred_kind=inferred_kind,
        non_empty_rows=non_empty_rows,
        non_empty_cols=non_empty_cols,
        year_from=min(year_values) if year_values else None,
        year_to=max(year_values) if year_values else None,
        year_count=len(year_values),
        class_labels=class_labels,
        matrix_row_labels=row_labels,
        count_matrix=count_matrix,
        probability_matrix=probability_matrix,
        decade_blocks=decade_blocks,
        preview_rows=_build_preview_rows(frame),
    )


def matrix_to_frame(
    row_labels: tuple[str, ...],
    class_labels: tuple[str, ...],
    values: tuple[tuple[float | None, ...], ...],
) -> pd.DataFrame:
    if not row_labels or not class_labels or not values:
        return pd.DataFrame()
    return pd.DataFrame(list(values), index=list(row_labels), columns=list(class_labels))


def _infer_workbook_purpose(file_name: str, sheets: tuple[MatrixSheetSummary, ...]) -> str:
    lowered = file_name.casefold()
    if "готов" in lowered:
        return "Готовые декадные матрицы переходов по классам."
    if "jcf" in lowered and any(sheet.probability_matrix for sheet in sheets):
        return "Комбинированный файл: исторические ряды, матрицы переходов и вероятностные сводки."
    if any(sheet.year_count for sheet in sheets) and not any(sheet.probability_matrix for sheet in sheets):
        return "Базовые исторические декадные ряды по климатическим показателям."
    return "Промежуточный или служебный файл по переходным матрицам."


def _infer_sheet_kind(
    frame: pd.DataFrame,
    year_values: list[int],
    decade_blocks: tuple[str, ...],
    probability_matrix: tuple[tuple[float | None, ...], ...],
    count_matrix: tuple[tuple[float | None, ...], ...],
) -> str:
    if frame.empty:
        return "пустой лист"
    if probability_matrix:
        return "сводные вероятности переходов"
    if decade_blocks and year_values and count_matrix:
        return "декадные ряды и матрицы переходов"
    if year_values:
        return "исторические декадные ряды"
    if count_matrix:
        return "матрица переходов по классам"
    return "служебный лист"


def _extract_metric_label(frame: pd.DataFrame, fallback: str) -> str:
    if frame.empty:
        return fallback
    for row_index in range(min(12, len(frame))):
        for col_index in range(min(12, frame.shape[1])):
            value = _cell_text(frame.iat[row_index, col_index])
            if any(keyword in value.casefold() for keyword in MATRIX_KEYWORDS):
                return value
    return fallback


def _extract_year_values(frame: pd.DataFrame) -> list[int]:
    year_values: set[int] = set()
    if frame.empty:
        return []
    for row_index in range(len(frame)):
        for col_index in range(min(3, frame.shape[1])):
            candidate = _to_year(frame.iat[row_index, col_index])
            if candidate is not None:
                year_values.add(candidate)
    return sorted(year_values)


def _extract_first_matrix(
    frame: pd.DataFrame,
) -> tuple[tuple[str, ...], tuple[str, ...], tuple[tuple[float | None, ...], ...], tuple[tuple[float | None, ...], ...]]:
    if frame.empty:
        return (), (), (), ()

    for row_index in range(min(30, len(frame))):
        row_values = [_cell_text(frame.iat[row_index, col_index]) for col_index in range(min(frame.shape[1], 20))]
        if "Сумма" not in row_values:
            continue
        sum_col = row_values.index("Сумма")
        class_labels, label_col = _extract_class_labels_from_row(row_values, sum_col)
        if len(class_labels) < 3 or label_col is None:
            continue
        row_labels: list[str] = []
        count_rows: list[tuple[float | None, ...]] = []
        probability_rows: list[tuple[float | None, ...]] = []
        next_row = row_index + 1
        while next_row < len(frame):
            label = _cell_text(frame.iat[next_row, label_col])
            if label == "Сумма" or label.casefold() == "год":
                break
            if label and _looks_like_class_label(label):
                values = tuple(_to_float(frame.iat[next_row, label_col + 1 + offset]) for offset in range(len(class_labels)))
                if any(value is not None for value in values):
                    row_labels.append(label)
                    count_rows.append(values)
                    probability_candidate = _extract_probability_row(frame, next_row + 1, label_col, len(class_labels))
                    if probability_candidate is not None:
                        probability_rows.append(probability_candidate)
                        next_row += 2
                        continue
                next_row += 1
                continue
            if row_labels:
                break
            next_row += 1

        if count_rows:
            if len(probability_rows) != len(count_rows):
                probability_rows = []
            return tuple(class_labels), tuple(row_labels), tuple(count_rows), tuple(probability_rows)
    return (), (), (), ()


def _extract_class_labels_from_row(row_values: list[str], sum_col: int) -> tuple[list[str], int | None]:
    class_labels: list[str] = []
    metric_col: int | None = None
    for col_index in range(sum_col - 1, -1, -1):
        value = row_values[col_index]
        if not value:
            continue
        if _looks_like_class_label(value):
            class_labels.insert(0, value)
            continue
        metric_col = col_index
        break
    return class_labels, metric_col


def _extract_probability_row(
    frame: pd.DataFrame,
    row_index: int,
    label_col: int,
    class_count: int,
) -> tuple[float | None, ...] | None:
    if row_index >= len(frame):
        return None
    if _cell_text(frame.iat[row_index, label_col]):
        return None
    values = tuple(_to_float(frame.iat[row_index, label_col + 1 + offset]) for offset in range(class_count))
    numeric_values = [value for value in values if value is not None]
    if not numeric_values:
        return None
    total = sum(numeric_values)
    if 0.95 <= total <= 1.05:
        return values
    return None


def _extract_decade_blocks(frame: pd.DataFrame) -> list[str]:
    if frame.empty:
        return []
    blocks: list[str] = []
    for row_index in range(len(frame)):
        for col_index in range(frame.shape[1]):
            value = _cell_text(frame.iat[row_index, col_index])
            if DECADE_KEYWORD in value.casefold():
                blocks.append(value)
    return _dedupe_preserve_order(blocks)


def _build_preview_rows(frame: pd.DataFrame, row_limit: int = 18, col_limit: int = 14) -> tuple[tuple[str, ...], ...]:
    if frame.empty:
        return ()
    preview_rows: list[tuple[str, ...]] = []
    for row_index in range(min(row_limit, len(frame))):
        preview_rows.append(tuple(_cell_text(frame.iat[row_index, col_index]) for col_index in range(min(col_limit, frame.shape[1]))))
    return tuple(preview_rows)


def _looks_like_class_label(value: str) -> bool:
    normalized = value.strip().casefold()
    if not normalized or normalized == "сумма":
        return False
    if any(keyword in normalized for keyword in MATRIX_KEYWORDS):
        return False
    return bool(re.search(r"\d", normalized) and ("-" in normalized or "." in normalized))


def _cell_text(value) -> str:
    if value is None or (isinstance(value, float) and pd.isna(value)):
        return ""
    return str(value).replace("\n", " ").strip()


def _to_float(value) -> float | None:
    text = _cell_text(value)
    if not text:
        return None
    try:
        return float(text.replace(",", "."))
    except ValueError:
        return None


def _to_year(value) -> int | None:
    numeric = _to_float(value)
    if numeric is None:
        return None
    year = int(numeric)
    if abs(numeric - year) > 1e-6:
        return None
    if 1900 <= year <= 2100:
        return year
    return None


def _dedupe_preserve_order(values: list[str]) -> list[str]:
    seen: set[str] = set()
    result: list[str] = []
    for value in values:
        if value in seen:
            continue
        seen.add(value)
        result.append(value)
    return result
