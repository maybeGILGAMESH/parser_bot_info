from __future__ import annotations

import json
import re
import zipfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any

MISSING_VALUE_TOKENS = {"", "-", "--", "---", "----", "-----"}
STATION_CODE_FIELD_NAMES = {"Индекс ВМО", "Синоптический индекс станции"}


@dataclass(slots=True)
class ParsedArchive:
    zip_path: Path
    field_names: list[str]
    stations: dict[str, str]
    records: list[dict[str, Any]]

    def to_json(self, limit: int | None = None) -> str:
        payload = {
            "zip_path": str(self.zip_path),
            "field_names": self.field_names,
            "stations": self.stations,
            "records": self.records[:limit] if limit is not None else self.records,
        }
        return json.dumps(payload, ensure_ascii=False, indent=2)


def parse_result_zip(path: str | Path) -> ParsedArchive:
    zip_path = Path(path)
    with zipfile.ZipFile(zip_path) as archive:
        data_name = _find_member(archive, "wr")
        fields_name = _find_member(archive, "fld")
        stations_name = _find_member(archive, "statlist")

        field_names = _parse_field_names(_read_text(archive, fields_name))
        stations = _parse_station_map(_read_text(archive, stations_name))
        records = _parse_records(_read_text(archive, data_name), field_names, stations)

    return ParsedArchive(
        zip_path=zip_path,
        field_names=field_names,
        stations=stations,
        records=records,
    )


def _find_member(archive: zipfile.ZipFile, prefix: str) -> str:
    for name in archive.namelist():
        lowered = Path(name).name.lower()
        if lowered.startswith(prefix.lower()) and lowered.endswith(".txt"):
            return name
    raise FileNotFoundError(f"В архиве не найден файл с префиксом '{prefix}'")


def _read_text(archive: zipfile.ZipFile, member_name: str) -> str:
    payload = archive.read(member_name)
    for encoding in ("cp1251", "utf-8", "latin-1"):
        try:
            return payload.decode(encoding)
        except UnicodeDecodeError:
            continue
    return payload.decode("utf-8", errors="replace")


def _parse_field_names(text: str) -> list[str]:
    field_names: list[str] = []
    for raw_line in text.splitlines():
        line = raw_line.strip()
        if not line:
            continue
        parts = line.split(maxsplit=3)
        if len(parts) < 4:
            continue
        field_names.append(_normalize_field_name(parts[3]))
    if not field_names:
        raise ValueError("Не удалось распознать список полей из fld*.txt")
    return field_names


def _parse_station_map(text: str) -> dict[str, str]:
    stations: dict[str, str] = {}
    for raw_line in text.splitlines():
        line = raw_line.strip()
        if not line:
            continue
        code, _, name = line.partition(" ")
        stations[code] = re.sub(r"\s+", " ", name).strip()
    return stations


def _parse_records(text: str, field_names: list[str], stations: dict[str, str]) -> list[dict[str, Any]]:
    records: list[dict[str, Any]] = []
    for raw_line in text.splitlines():
        line = raw_line.rstrip()
        if not line:
            continue
        tokens = re.split(r"\s+", line.strip())
        if len(tokens) != len(field_names):
            raise ValueError(
                f"Количество значений в строке ({len(tokens)}) не совпадает с числом полей ({len(field_names)}): {line}"
            )

        record = {
            field_name: _coerce_token(field_name, token)
            for field_name, token in zip(field_names, tokens, strict=True)
        }
        station_code = ""
        for field_name in STATION_CODE_FIELD_NAMES:
            if field_name in record:
                station_code = str(record.get(field_name, ""))
                break
        if station_code and station_code in stations:
            record["Название станции"] = stations[station_code]
        records.append(record)
    return records


def _normalize_field_name(value: str) -> str:
    return re.sub(r"\s+", " ", value).strip()


def _coerce_token(field_name: str, token: str) -> Any:
    value = token.strip()
    if value in MISSING_VALUE_TOKENS:
        return None
    if field_name == "Индекс ВМО":
        return value
    if re.fullmatch(r"-?\d+", value):
        return int(value)
    if re.fullmatch(r"-?\d+[.,]\d+", value):
        return float(value.replace(",", "."))
    return value
