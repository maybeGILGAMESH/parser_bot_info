from __future__ import annotations

import json
import re
import subprocess
from dataclasses import asdict, dataclass
from pathlib import Path

import pandas as pd


@dataclass(frozen=True, slots=True)
class StationCatalogEntry:
    row_number: int
    wmo_index: str
    station_name: str
    latitude_text: str
    longitude_text: str
    latitude: float
    longitude: float
    elevation_m: int
    start_year: int
    observation_years: int
    note: str
    end_year: int | None = None
    is_likely_active: bool = True
    status_label: str = "Активная/рабочая"
    operation_period: str = ""
    closure_note: str = ""
    transfer_note: str = ""
    rename_note: str = ""
    incident_note: str = ""


ROW_PATTERN = re.compile(
    r"^\s*(\d+)\s+(\d{5})\s+(.+?)\s+(-?\d{1,3}\s*[°оo]\s*\d{2}\s*[′’']?)\s+(-?\d{1,3}\s*[°оo]\s*\d{2}\s*[′’']?)\s+(-?\s*\d+)\s+(\d{4})(?:\s+(.*))?$",
    re.IGNORECASE,
)
SENTENCE_SPLIT_PATTERN = re.compile(r"\s*;\s*|(?<!наз\.)(?<=\.)\s*(?=[А-ЯЁA-Z])")
CLOSURE_KEYWORDS = ("закрыт", "законсерв", "прекращен", "прекращены", "нет штата")
TRANSFER_KEYWORDS = ("перенос", "перенес", "перенесен", "перенесена", "перенесены")
RENAME_KEYWORDS = (" наз.", "переимен", "сменил название", "смена названия")
INCIDENT_KEYWORDS = ("сгорел", "сгорела", "сгорело", "пожар")


def build_station_catalog_from_pdf(pdf_path: str | Path, output_path: str | Path) -> list[StationCatalogEntry]:
    text = _extract_text_from_pdf(Path(pdf_path))
    entries = parse_station_catalog_text(text)
    _write_station_catalog(entries, output_path)
    return entries


def build_station_catalog_from_xlsx(xlsx_path: str | Path, output_path: str | Path, current_year: int = 2026) -> list[StationCatalogEntry]:
    frame = pd.read_excel(Path(xlsx_path), header=None)
    entries = parse_station_catalog_frame(frame, current_year=current_year)
    _write_station_catalog(entries, output_path)
    return entries


def build_station_catalog(source_path: str | Path, output_path: str | Path) -> list[StationCatalogEntry]:
    source = Path(source_path)
    suffix = source.suffix.casefold()
    if suffix in {".xlsx", ".xls"}:
        return build_station_catalog_from_xlsx(source, output_path)
    if suffix == ".pdf":
        return build_station_catalog_from_pdf(source, output_path)
    raise ValueError(f"Неподдерживаемый формат справочника: {source.suffix}")


def _write_station_catalog(entries: list[StationCatalogEntry], output_path: str | Path) -> None:
    output_file = Path(output_path)
    output_file.parent.mkdir(parents=True, exist_ok=True)
    payload = [asdict(entry) for entry in entries]
    output_file.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def load_station_catalog(path: str | Path) -> list[StationCatalogEntry]:
    source = Path(path)
    suffix = source.suffix.casefold()
    if suffix == ".json":
        payload = json.loads(source.read_text(encoding="utf-8"))
        return [StationCatalogEntry(**_normalize_loaded_entry(item)) for item in payload]
    if suffix in {".xlsx", ".xls"}:
        return parse_station_catalog_frame(pd.read_excel(source, header=None))
    if suffix == ".pdf":
        return parse_station_catalog_text(_extract_text_from_pdf(source))
    raise ValueError(f"Неподдерживаемый формат справочника: {source.suffix}")


def parse_station_catalog_text(text: str, current_year: int = 2026) -> list[StationCatalogEntry]:
    entries: list[dict] = []
    current: dict | None = None
    for raw_line in text.splitlines():
        line = raw_line.rstrip("\n")
        stripped = line.strip()
        if not stripped:
            continue
        if stripped.startswith("№") or stripped.startswith("п/п") or stripped.startswith("Индекс"):
            continue
        if stripped == "\x0c":
            continue
        cleaned = line.replace("\x0c", "").rstrip()
        match = ROW_PATTERN.match(cleaned)
        if match:
            current = _build_entry_dict(
                {
                    "row_number": int(match.group(1)),
                    "wmo_index": match.group(2),
                    "station_name": _clean_spaces(match.group(3)),
                    "latitude_text": _normalize_coordinate_text(match.group(4)),
                    "longitude_text": _normalize_coordinate_text(match.group(5)),
                    "latitude": coordinate_to_decimal(match.group(4)),
                    "longitude": coordinate_to_decimal(match.group(5)),
                    "elevation_m": int(match.group(6).replace(" ", "")),
                    "start_year": int(match.group(7)),
                    "note": _clean_spaces(match.group(8) or ""),
                },
                current_year=current_year,
            )
            entries.append(current)
            continue

        if current is None:
            continue

        indent = len(cleaned) - len(cleaned.lstrip(" "))
        continuation = _clean_spaces(cleaned)
        if not continuation:
            continue
        if indent < 40 and (
            continuation.startswith("(")
            or len(current["station_name"]) < 28
            or current["station_name"].endswith(",")
            or len(continuation.split()) <= 2
        ):
            current["station_name"] = f"{current['station_name']} {continuation}".strip()
        else:
            current["note"] = f"{current['note']} {continuation}".strip()
            _apply_note_metadata(current, current_year)

    return [StationCatalogEntry(**item) for item in entries]


def parse_station_catalog_frame(frame: pd.DataFrame, current_year: int = 2026) -> list[StationCatalogEntry]:
    entries: list[dict] = []
    current: dict | None = None
    for _, row in frame.iterrows():
        row_number = _safe_int(row.iloc[0])
        wmo_index = _safe_wmo_index(row.iloc[1])
        if row_number is not None and wmo_index is not None:
            current = _build_entry_dict(
                {
                    "row_number": row_number,
                    "wmo_index": wmo_index,
                    "station_name": _clean_spaces(_cell_text(row.iloc[2])),
                    "latitude_text": _normalize_coordinate_text(_extract_coordinate_from_row(row, 4, 5)),
                    "longitude_text": _normalize_coordinate_text(_extract_coordinate_from_row(row, 6)),
                    "latitude": coordinate_to_decimal(_extract_coordinate_from_row(row, 4, 5)),
                    "longitude": coordinate_to_decimal(_extract_coordinate_from_row(row, 6)),
                    "elevation_m": _safe_signed_int(row.iloc[7]),
                    "start_year": _safe_signed_int(row.iloc[8]),
                    "note": _extract_note_from_row(row),
                },
                current_year=current_year,
            )
            entries.append(current)
            continue

        if current is None:
            continue

        station_name_continuation = _clean_spaces(_cell_text(row.iloc[2]))
        note_continuation = _extract_note_from_row(row)
        if station_name_continuation and not any(_cell_text(row.iloc[i]) for i in (4, 6, 7, 8)):
            current["station_name"] = f"{current['station_name']} {station_name_continuation}".strip()
        if note_continuation:
            current["note"] = f"{current['note']} {note_continuation}".strip()
            _apply_note_metadata(current, current_year)

    return [StationCatalogEntry(**item) for item in entries]


def coordinate_to_decimal(value: str) -> float:
    normalized = _normalize_coordinate_text(value)
    parts = re.findall(r"-?\d{1,3}", normalized)
    if len(parts) < 2:
        raise ValueError(f"Не удалось распознать координату: {value}")
    degrees = int(parts[0])
    minutes = int(parts[1])
    if abs(degrees) <= 59 and abs(minutes) >= 60:
        degrees, minutes = minutes, degrees
    sign = -1 if degrees < 0 else 1
    absolute_degrees = abs(degrees)
    return round(sign * (absolute_degrees + (minutes / 60.0)), 6)


def _normalize_coordinate_text(value: str) -> str:
    raw = (
        value.replace("\n", " ")
        .replace("о", "°")
        .replace("o", "°")
        .replace("’", "′")
        .replace("'", "′")
    )
    sign = "-" if "-" in raw else ""
    parts = re.findall(r"\d{1,3}", raw)
    if len(parts) >= 2:
        degrees = int(parts[0])
        minutes = int(parts[1])
        if degrees <= 59 and minutes >= 60:
            degrees, minutes = minutes, degrees
        return f"{sign}{degrees}°{minutes:02d}′"
    return re.sub(r"\s+", "", raw)


def _clean_spaces(value: str) -> str:
    return re.sub(r"\s+", " ", value).strip()


def _cell_text(value) -> str:
    if value is None or (isinstance(value, float) and pd.isna(value)):
        return ""
    return str(value).replace("\n", " ").strip()


def _extract_note_from_row(row: pd.Series) -> str:
    parts = []
    for index in range(9, len(row)):
        text = _cell_text(row.iloc[index])
        if text:
            parts.append(text)
    return _clean_spaces(" ".join(parts))


def _extract_coordinate_from_row(row: pd.Series, *indices: int) -> str:
    parts = []
    for index in indices:
        text = _cell_text(row.iloc[index])
        if text:
            parts.append(text)
    return " ".join(parts)


def _safe_int(value) -> int | None:
    text = _cell_text(value)
    if not text:
        return None
    match = re.fullmatch(r"\d+", text)
    if not match:
        return None
    return int(text)


def _safe_wmo_index(value) -> str | None:
    text = _cell_text(value)
    if not text:
        return None
    digits = re.sub(r"\D+", "", text)
    if len(digits) != 5:
        return None
    return digits


def _safe_signed_int(value) -> int:
    text = _cell_text(value).replace(" ", "")
    match = re.search(r"-?\d+", text)
    if not match:
        raise ValueError(f"Не удалось распознать числовое значение: {value}")
    return int(match.group(0))


def _build_entry_dict(base: dict, current_year: int) -> dict:
    item = dict(base)
    item["observation_years"] = max(1, current_year - int(item["start_year"]) + 1)
    _apply_note_metadata(item, current_year)
    return item


def _apply_note_metadata(item: dict, current_year: int) -> None:
    note = _clean_spaces(item.get("note", ""))
    closure_note = _extract_matching_fragments(note, CLOSURE_KEYWORDS)
    transfer_note = _extract_matching_fragments(note, TRANSFER_KEYWORDS)
    rename_note = _extract_matching_fragments(note, RENAME_KEYWORDS)
    incident_note = _extract_matching_fragments(note, INCIDENT_KEYWORDS)
    end_year = _extract_end_year(note, closure_note)
    is_active = end_year is None
    item["end_year"] = end_year
    item["is_likely_active"] = is_active
    item["closure_note"] = closure_note
    item["transfer_note"] = transfer_note
    item["rename_note"] = rename_note
    item["incident_note"] = incident_note
    item["status_label"] = _build_status_label(
        note=note,
        is_active=is_active,
        closure_note=closure_note,
        transfer_note=transfer_note,
        rename_note=rename_note,
        incident_note=incident_note,
    )
    item["observation_years"] = max(1, (end_year or current_year) - int(item["start_year"]) + 1)
    end_label = "н.в." if end_year is None else str(end_year)
    item["operation_period"] = f"{item['start_year']} - {end_label}"


def _extract_end_year(note: str, closure_note: str) -> int | None:
    normalized = note.casefold()
    if not closure_note:
        return None
    if _has_reopening(note) or "была законсервирована" in normalized:
        return None
    if "временно законсервирована" in normalized:
        return None
    date_matches = re.findall(r"\d{2}\.\d{2}\.(\d{4})", closure_note)
    if date_matches:
        return max(int(match) for match in date_matches)
    range_matches = re.findall(r"((?:19|20)\d{2})\s*-\s*((?:19|20)\d{2})", closure_note)
    if range_matches and ("была законсервирована" in normalized or "были законсервированы" in normalized):
        return None
    if range_matches:
        return max(int(end_year) for _, end_year in range_matches)
    year_matches = re.findall(r"(?:19|20)\d{2}", closure_note)
    if year_matches:
        return max(int(match) for match in year_matches)
    return None


def _build_status_label(
    note: str,
    is_active: bool,
    closure_note: str,
    transfer_note: str,
    rename_note: str,
    incident_note: str,
) -> str:
    normalized = note.casefold()
    if not is_active:
        if "нет штата" in normalized or "прекращ" in normalized:
            return "Наблюдения прекращены"
        if "законсерв" in normalized and "закрыт" in normalized:
            return "Законсервирована и закрыта"
        if "законсерв" in normalized:
            return "Законсервирована"
        return "Закрыта"
    if incident_note and _has_reopening(note):
        return "После инцидента восстановлена"
    if closure_note and (_has_reopening(note) or "была законсервирована" in normalized):
        return "Работает, были перерывы"
    if incident_note and transfer_note:
        return "Действующая, был инцидент и перенос"
    if incident_note:
        return "Действующая, есть инцидент в истории"
    if transfer_note and rename_note:
        return "Действующая, перенос и смена названия"
    if transfer_note:
        return "Действующая, были переносы"
    if rename_note:
        return "Действующая, смена названия"
    if closure_note and "временно законсервирована" in normalized:
        return "Работает, была временно законсервирована"
    return "Активная/рабочая"


def _extract_matching_fragments(note: str, keywords: tuple[str, ...]) -> str:
    if not note:
        return ""
    fragments = []
    for fragment in _split_note_fragments(note):
        normalized = fragment.casefold()
        if any(keyword in normalized for keyword in keywords):
            fragments.append(fragment)
    return " | ".join(_dedupe_preserve_order(fragments))


def _split_note_fragments(note: str) -> list[str]:
    return [fragment for fragment in (_clean_spaces(part) for part in SENTENCE_SPLIT_PATTERN.split(note)) if fragment]


def _dedupe_preserve_order(values: list[str]) -> list[str]:
    seen: set[str] = set()
    result: list[str] = []
    for value in values:
        if value in seen:
            continue
        seen.add(value)
        result.append(value)
    return result


def _has_reopening(note: str) -> bool:
    return bool(re.search(r"\bоткрыт\w*", note.casefold()))


def _normalize_loaded_entry(item: dict) -> dict:
    normalized = dict(item)
    required_fields = {"operation_period", "closure_note", "transfer_note", "rename_note", "incident_note"}
    if not required_fields.issubset(normalized):
        _apply_note_metadata(normalized, current_year=2026)
    return normalized


def _extract_text_from_pdf(pdf_path: Path) -> str:
    result = subprocess.run(
        ["pdftotext", "-layout", str(pdf_path), "-"],
        check=True,
        capture_output=True,
    )
    return result.stdout.decode("utf-8", errors="replace")
