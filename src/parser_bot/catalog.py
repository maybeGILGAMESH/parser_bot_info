from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from .config import DatasetConfig


@dataclass(frozen=True, slots=True)
class DatasetTemplate:
    key: str
    title: str
    database_section: str
    source_name: str
    frequency: str
    metric_name: str
    unit: str
    query_parameters: tuple[str, ...]
    result_fields: tuple[str, ...]
    chart_color: str
    available: bool = True
    availability_note: str = ""


DATASET_TEMPLATES: dict[str, DatasetTemplate] = {
    "monthly_temperature": DatasetTemplate(
        key="monthly_temperature",
        title="Месячная температура воздуха",
        database_section="Месяц",
        source_name="Температура воздуха",
        frequency="monthly",
        metric_name="Температура воздуха",
        unit="°C",
        query_parameters=(),
        result_fields=("Индекс ВМО", "Год"),
        chart_color="#d95f02",
    ),
    "monthly_humidity": DatasetTemplate(
        key="monthly_humidity",
        title="Месячная относительная влажность",
        database_section="Месяц",
        source_name="Относительная влажность воздуха",
        frequency="monthly",
        metric_name="Относительная влажность воздуха",
        unit="%",
        query_parameters=(),
        result_fields=("Индекс ВМО", "Год"),
        chart_color="#1b9e77",
    ),
    "monthly_precipitation": DatasetTemplate(
        key="monthly_precipitation",
        title="Месячные осадки",
        database_section="Месяц",
        source_name="Атмосферные осадки",
        frequency="monthly",
        metric_name="Атмосферные осадки",
        unit="мм",
        query_parameters=(),
        result_fields=("Индекс ВМО", "Год"),
        chart_color="#1f78b4",
    ),
    "monthly_vapor_pressure": DatasetTemplate(
        key="monthly_vapor_pressure",
        title="Месячная упругость водяного пара",
        database_section="Месяц",
        source_name="Упругость водяного пара",
        frequency="monthly",
        metric_name="Упругость водяного пара",
        unit="мб",
        query_parameters=(),
        result_fields=("Индекс ВМО", "Год"),
        chart_color="#4c956c",
    ),
    "monthly_vapor_pressure_deficit": DatasetTemplate(
        key="monthly_vapor_pressure_deficit",
        title="Месячный дефицит насыщения водяного пара",
        database_section="Месяц",
        source_name="DERIVED",
        frequency="monthly",
        metric_name="Дефицит насыщения водяного пара",
        unit="мб",
        query_parameters=(),
        result_fields=("Индекс ВМО", "Год"),
        chart_color="#8d6e63",
    ),
    "daily_precipitation": DatasetTemplate(
        key="daily_precipitation",
        title="Суточные осадки",
        database_section="Сутки",
        source_name="TTTR - Температура и осадки",
        frequency="daily",
        metric_name="Количество осадков",
        unit="мм",
        query_parameters=("Количество осадков",),
        result_fields=("Индекс ВМО", "Год", "Месяц", "День", "Количество осадков"),
        chart_color="#0c7cba",
    ),
    "daily_temperature": DatasetTemplate(
        key="daily_temperature",
        title="Суточная средняя температура воздуха",
        database_section="Сроки",
        source_name="8-срочные наблюдения на станциях, (SROK8C)",
        frequency="daily",
        metric_name="Температура воздуха по сухому терм-ру",
        unit="°C",
        query_parameters=(
            "Год источника (местный)",
            "Месяц источника (местный)",
            "День источника (местный)",
            "Срок источника (местный)",
            "Температура воздуха по сухому терм-ру",
        ),
        result_fields=(
            "Синоптический индекс станции",
            "Год источника (местный)",
            "Месяц источника (местный)",
            "День источника (местный)",
            "Срок источника (местный)",
            "Температура воздуха по сухому терм-ру",
        ),
        chart_color="#d95f02",
    ),
    "daily_humidity": DatasetTemplate(
        key="daily_humidity",
        title="Суточная влажность",
        database_section="Сроки",
        source_name="8-срочные наблюдения на станциях, (SROK8C)",
        frequency="daily",
        metric_name="Относительная влажность воздуха",
        unit="%",
        query_parameters=(
            "Год источника (местный)",
            "Месяц источника (местный)",
            "День источника (местный)",
            "Срок источника (местный)",
            "Относительная влажность воздуха",
        ),
        result_fields=(
            "Синоптический индекс станции",
            "Год источника (местный)",
            "Месяц источника (местный)",
            "День источника (местный)",
            "Срок источника (местный)",
            "Относительная влажность воздуха",
        ),
        chart_color="#2a9d8f",
    ),
    "daily_vapor_pressure_deficit": DatasetTemplate(
        key="daily_vapor_pressure_deficit",
        title="Суточный дефицит насыщения водяного пара",
        database_section="Сроки",
        source_name="8-срочные наблюдения на станциях, (SROK8C)",
        frequency="daily",
        metric_name="Дефицит насыщения водяного пара",
        unit="мб",
        query_parameters=(
            "Год источника (местный)",
            "Месяц источника (местный)",
            "День источника (местный)",
            "Срок источника (местный)",
            "Дефицит насыщения водяного пара",
        ),
        result_fields=(
            "Синоптический индекс станции",
            "Год источника (местный)",
            "Месяц источника (местный)",
            "День источника (местный)",
            "Срок источника (местный)",
            "Дефицит насыщения водяного пара",
        ),
        chart_color="#8d6e63",
    ),
}


MONTH_FIELD_ORDER = (
    ("Январь", 1),
    ("Февраль", 2),
    ("Март", 3),
    ("Апрель", 4),
    ("Май", 5),
    ("Июнь", 6),
    ("Июль", 7),
    ("Август", 8),
    ("Сентябрь", 9),
    ("Октябрь", 10),
    ("Ноябрь", 11),
    ("Декабрь", 12),
)


def get_dataset_template(key: str) -> DatasetTemplate:
    try:
        return DATASET_TEMPLATES[key]
    except KeyError as exc:
        raise KeyError(f"Неизвестный набор данных: {key}") from exc


def list_dataset_templates() -> list[DatasetTemplate]:
    return list(DATASET_TEMPLATES.values())


def build_dataset_config(
    template: DatasetTemplate,
    station_query: str,
    raw_output_dir: Path,
    year_from: int | None,
    year_to: int | None,
    month_from: int | None = None,
    month_to: int | None = None,
    day_from: int | None = None,
    day_to: int | None = None,
) -> DatasetConfig:
    if not template.available:
        raise ValueError(template.availability_note or f"Набор {template.title} недоступен")

    return DatasetConfig(
        name=template.key,
        database_section=template.database_section,
        source_name=template.source_name,
        stations=[station_query],
        year_from=year_from,
        year_to=year_to,
        month_from=month_from,
        month_to=month_to,
        day_from=day_from,
        day_to=day_to,
        output_mode="single_file",
        months=[],
        query_parameters=list(template.query_parameters),
        result_fields=list(template.result_fields),
        output_dir=raw_output_dir,
    )
