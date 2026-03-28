from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, timezone
from math import exp
from pathlib import Path
from statistics import mean

from .browser_client import AisoriBrowserClient
from .catalog import MONTH_FIELD_ORDER, build_dataset_config, get_dataset_template
from .config import AppConfig
from .parse_result import ParsedArchive, parse_result_zip
from .storage import ObservationRecord, ObservationStore


@dataclass(frozen=True, slots=True)
class RefreshResult:
    dataset_key: str
    dataset_title: str
    station_code: str
    station_name: str
    record_count: int
    zip_path: Path
    started_at: str
    finished_at: str
    status: str
    message: str


class AisoriDataService:
    def __init__(self, config: AppConfig, project_root: Path) -> None:
        self.config = config
        self.project_root = project_root
        self.raw_root = project_root / "data" / "raw"
        self.store = ObservationStore(project_root / "data" / "observations.sqlite3")

    def refresh_dataset(
        self,
        dataset_key: str,
        station_query: str,
        year_from: int | None,
        year_to: int | None,
        month_from: int | None = None,
        month_to: int | None = None,
        day_from: int | None = None,
        day_to: int | None = None,
    ) -> RefreshResult:
        template = get_dataset_template(dataset_key)
        started_at = _utc_now()

        if dataset_key == "monthly_vapor_pressure_deficit":
            return self._refresh_monthly_vapor_pressure_deficit(
                template=template,
                station_query=station_query,
                year_from=year_from,
                year_to=year_to,
                started_at=started_at,
            )

        try:
            raw_output_dir = self.raw_root / dataset_key / _sanitize_station_query(station_query)
            dataset = build_dataset_config(
                template=template,
                station_query=station_query,
                raw_output_dir=raw_output_dir,
                year_from=year_from,
                year_to=year_to,
                month_from=month_from,
                month_to=month_to,
                day_from=day_from,
                day_to=day_to,
            )
            browser_client = AisoriBrowserClient(browser_config=self.config.browser)
            download_result = browser_client.download_dataset(
                credentials=self.config.credentials,
                dataset=dataset,
                output_root=self.project_root,
            )
            parsed = parse_result_zip(download_result.file_path)
            observations = normalize_archive(template, parsed, started_at)
            record_count = self.store.upsert_observations(observations)
            station_code = observations[0].station_code if observations else station_query.strip()
            station_name = observations[0].station_name if observations else station_query.strip()
            finished_at = _utc_now()
            result = RefreshResult(
                dataset_key=template.key,
                dataset_title=template.title,
                station_code=station_code,
                station_name=station_name,
                record_count=record_count,
                zip_path=download_result.file_path,
                started_at=started_at,
                finished_at=finished_at,
                status="success",
                message="Данные обновлены",
            )
        except Exception as exc:
            finished_at = _utc_now()
            station_code = station_query.strip()
            station_name = station_query.strip()
            result = RefreshResult(
                dataset_key=template.key,
                dataset_title=template.title,
                station_code=station_code,
                station_name=station_name,
                record_count=0,
                zip_path=Path(),
                started_at=started_at,
                finished_at=finished_at,
                status="error",
                message=str(exc),
            )

        self.store.log_refresh(
            dataset_key=result.dataset_key,
            station_code=result.station_code,
            station_name=result.station_name,
            status=result.status,
            message=result.message,
            zip_path=str(result.zip_path),
            record_count=result.record_count,
            started_at=result.started_at,
            finished_at=result.finished_at,
        )
        if result.status != "success":
            raise RuntimeError(result.message)
        return result

    def _refresh_monthly_vapor_pressure_deficit(
        self,
        template,
        station_query: str,
        year_from: int | None,
        year_to: int | None,
        started_at: str,
    ) -> RefreshResult:
        temperature_result = self.refresh_dataset(
            dataset_key="monthly_temperature",
            station_query=station_query,
            year_from=year_from,
            year_to=year_to,
        )
        self.refresh_dataset(
            dataset_key="monthly_vapor_pressure",
            station_query=station_query,
            year_from=year_from,
            year_to=year_to,
        )

        observations = self.store.fetch_observations(temperature_result.station_code)
        temperature_rows = {
            (item["year"], item["month"]): item
            for item in observations
            if item["dataset_key"] == "monthly_temperature"
            and _within_year_range(item["year"], year_from, year_to)
        }
        vapor_rows = {
            (item["year"], item["month"]): item
            for item in observations
            if item["dataset_key"] == "monthly_vapor_pressure"
            and _within_year_range(item["year"], year_from, year_to)
        }

        derived_records: list[ObservationRecord] = []
        for key, temperature_row in sorted(temperature_rows.items()):
            vapor_row = vapor_rows.get(key)
            if vapor_row is None:
                continue
            saturation = _saturation_vapor_pressure(float(temperature_row["value"]))
            deficit = round(saturation - float(vapor_row["value"]), 4)
            if deficit < 0:
                deficit = 0.0
            derived_records.append(
                ObservationRecord(
                    dataset_key=template.key,
                    dataset_title=template.title,
                    station_code=temperature_row["station_code"],
                    station_name=temperature_row["station_name"],
                    observation_date=temperature_row["observation_date"],
                    year=int(temperature_row["year"]),
                    month=int(temperature_row["month"]),
                    day=None,
                    metric_name=template.metric_name,
                    value=deficit,
                    unit=template.unit,
                    source_section=template.database_section,
                    source_name="derived_from_monthly_temperature_and_vapor_pressure",
                    downloaded_at=started_at,
                )
            )

        record_count = self.store.upsert_observations(derived_records)
        finished_at = _utc_now()
        result = RefreshResult(
            dataset_key=template.key,
            dataset_title=template.title,
            station_code=temperature_result.station_code,
            station_name=temperature_result.station_name,
            record_count=record_count,
            zip_path=Path(),
            started_at=started_at,
            finished_at=finished_at,
            status="success",
            message="Данные обновлены",
        )
        self.store.log_refresh(
            dataset_key=result.dataset_key,
            station_code=result.station_code,
            station_name=result.station_name,
            status=result.status,
            message=result.message,
            zip_path=str(result.zip_path),
            record_count=result.record_count,
            started_at=result.started_at,
            finished_at=result.finished_at,
        )
        return result

    def fetch_observations(self, station_code: str) -> list[dict]:
        return self.store.fetch_observations(station_code.strip())

    def fetch_latest_refreshes(self, station_code: str) -> list[dict]:
        return self.store.fetch_latest_refreshes(station_code.strip())


def normalize_archive(
    template,
    parsed_archive: ParsedArchive,
    downloaded_at: str,
) -> list[ObservationRecord]:
    if template.key in {"daily_temperature", "daily_humidity", "daily_vapor_pressure_deficit"}:
        return _normalize_daily_term_mean(template, parsed_archive, downloaded_at)
    if template.frequency == "monthly":
        return _normalize_monthly(template, parsed_archive, downloaded_at)
    if template.frequency == "daily":
        return _normalize_daily(template, parsed_archive, downloaded_at)
    raise ValueError(f"Неподдерживаемая частота набора: {template.frequency}")


def _normalize_monthly(template, parsed_archive: ParsedArchive, downloaded_at: str) -> list[ObservationRecord]:
    records: list[ObservationRecord] = []
    for record in parsed_archive.records:
        station_code = _resolve_station_code(record)
        station_name = str(record.get("Название станции", station_code))
        year = int(record["Год"])
        for month_name, month_number in MONTH_FIELD_ORDER:
            value = record.get(month_name)
            if value is None:
                continue
            observation_date = date(year, month_number, 1).isoformat()
            records.append(
                ObservationRecord(
                    dataset_key=template.key,
                    dataset_title=template.title,
                    station_code=station_code,
                    station_name=station_name,
                    observation_date=observation_date,
                    year=year,
                    month=month_number,
                    day=None,
                    metric_name=template.metric_name,
                    value=float(value),
                    unit=template.unit,
                    source_section=template.database_section,
                    source_name=template.source_name,
                    downloaded_at=downloaded_at,
                )
            )
    return records


def _normalize_daily(template, parsed_archive: ParsedArchive, downloaded_at: str) -> list[ObservationRecord]:
    records: list[ObservationRecord] = []
    for record in parsed_archive.records:
        value = record.get(template.metric_name)
        if value is None:
            continue
        station_code = _resolve_station_code(record)
        station_name = str(record.get("Название станции", station_code))
        year = int(_pick_record_value(record, "Год"))
        month = int(_pick_record_value(record, "Месяц"))
        day = int(_pick_record_value(record, "День"))
        observation_date = date(year, month, day).isoformat()
        records.append(
            ObservationRecord(
                dataset_key=template.key,
                dataset_title=template.title,
                station_code=station_code,
                station_name=station_name,
                observation_date=observation_date,
                year=year,
                month=month,
                day=day,
                metric_name=template.metric_name,
                value=float(value),
                unit=template.unit,
                source_section=template.database_section,
                source_name=template.source_name,
                downloaded_at=downloaded_at,
            )
        )
    return records


def _normalize_daily_term_mean(template, parsed_archive: ParsedArchive, downloaded_at: str) -> list[ObservationRecord]:
    grouped: dict[tuple[str, str, int, int, int], list[float]] = {}
    for record in parsed_archive.records:
        value = record.get(template.metric_name)
        if value is None:
            continue
        station_code = _resolve_station_code(record)
        station_name = str(record.get("Название станции", station_code))
        year = int(_pick_record_value(record, "Год источника (местный)", "Год"))
        month = int(_pick_record_value(record, "Месяц источника (местный)", "Месяц"))
        day = int(_pick_record_value(record, "День источника (местный)", "День"))
        grouped.setdefault((station_code, station_name, year, month, day), []).append(float(value))

    records: list[ObservationRecord] = []
    for (station_code, station_name, year, month, day), values in sorted(grouped.items()):
        records.append(
            ObservationRecord(
                dataset_key=template.key,
                dataset_title=template.title,
                station_code=station_code,
                station_name=station_name,
                observation_date=date(year, month, day).isoformat(),
                year=year,
                month=month,
                day=day,
                metric_name=template.metric_name,
                value=round(mean(values), 4),
                unit=template.unit,
                source_section=template.database_section,
                source_name=template.source_name,
                downloaded_at=downloaded_at,
            )
        )
    return records


def _resolve_station_code(record: dict) -> str:
    for field_name in ("Индекс ВМО", "Синоптический индекс станции"):
        if field_name in record:
            return str(record[field_name])
    raise KeyError("В записи не найден код станции")


def _pick_record_value(record: dict, *field_names: str):
    for field_name in field_names:
        for actual_name, value in record.items():
            if actual_name == field_name or actual_name.startswith(field_name):
                return value
    raise KeyError(f"В записи не найдено ни одно поле из списка: {field_names}")


def _saturation_vapor_pressure(temperature_celsius: float) -> float:
    return round(6.112 * exp((17.62 * temperature_celsius) / (243.12 + temperature_celsius)), 4)


def _within_year_range(year: int, year_from: int | None, year_to: int | None) -> bool:
    if year_from is not None and year < year_from:
        return False
    if year_to is not None and year > year_to:
        return False
    return True


def _utc_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def _sanitize_station_query(station_query: str) -> str:
    return "".join(ch if ch.isalnum() else "_" for ch in station_query.strip()) or "station"
