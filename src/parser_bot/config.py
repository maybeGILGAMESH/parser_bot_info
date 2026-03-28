from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path


@dataclass(slots=True)
class ScheduleConfig:
    hour: int
    minute: int


@dataclass(slots=True)
class BrowserConfig:
    headless: bool


@dataclass(slots=True)
class Credentials:
    username: str
    password: str
    email: str


@dataclass(slots=True)
class DatasetConfig:
    name: str
    database_section: str
    source_name: str
    stations: list[str]
    year_from: int | None
    year_to: int | None
    month_from: int | None
    month_to: int | None
    day_from: int | None
    day_to: int | None
    output_mode: str
    months: list[str]
    query_parameters: list[str]
    result_fields: list[str]
    output_dir: Path


@dataclass(slots=True)
class AppConfig:
    timezone: str
    schedule: ScheduleConfig
    browser: BrowserConfig
    credentials: Credentials
    datasets: list[DatasetConfig]


def load_config(path: str | Path) -> AppConfig:
    config_path = Path(path)
    raw = json.loads(config_path.read_text(encoding="utf-8"))

    schedule = ScheduleConfig(
        hour=int(raw["schedule"]["hour"]),
        minute=int(raw["schedule"]["minute"]),
    )
    browser = BrowserConfig(
        headless=bool(raw.get("browser", {}).get("headless", True)),
    )
    credentials = Credentials(
        username=str(raw["credentials"]["username"]).strip(),
        password=str(raw["credentials"]["password"]).strip(),
        email=str(raw["credentials"]["email"]).strip(),
    )
    _validate_credentials(credentials, config_path)
    datasets = [
        DatasetConfig(
            name=str(item["name"]).strip(),
            database_section=str(item["database_section"]).strip(),
            source_name=str(item["source_name"]).strip(),
            stations=[str(station).strip() for station in item.get("stations", []) if str(station).strip()],
            year_from=_optional_int(item.get("year_from")),
            year_to=_optional_int(item.get("year_to")),
            month_from=_optional_int(item.get("month_from")),
            month_to=_optional_int(item.get("month_to")),
            day_from=_optional_int(item.get("day_from")),
            day_to=_optional_int(item.get("day_to")),
            output_mode=str(item.get("output_mode", "single_file")).strip(),
            months=[str(month).strip() for month in item.get("months", []) if str(month).strip()],
            query_parameters=[
                str(field).strip()
                for field in item.get("query_parameters", [])
                if str(field).strip()
            ],
            result_fields=[
                str(field).strip()
                for field in item.get("result_fields", [])
                if str(field).strip()
            ],
            output_dir=Path(item["output_dir"]),
        )
        for item in raw["datasets"]
    ]
    return AppConfig(
        timezone=str(raw.get("timezone", "Europe/Moscow")).strip(),
        schedule=schedule,
        browser=browser,
        credentials=credentials,
        datasets=datasets,
    )


def _optional_int(value: object) -> int | None:
    if value in (None, ""):
        return None
    return int(value)


def _validate_credentials(credentials: Credentials, config_path: Path) -> None:
    placeholders = {
        "PUT_USERNAME_HERE",
        "PUT_PASSWORD_HERE",
        "PUT_EMAIL_HERE",
    }
    values = {
        credentials.username,
        credentials.password,
        credentials.email,
    }
    if any(value in placeholders or not value for value in values):
        raise ValueError(
            f"В конфиге {config_path} не заполнены реальные credentials.username / password / email."
        )
