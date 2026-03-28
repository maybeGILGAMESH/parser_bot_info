import unittest
from pathlib import Path

from parser_bot.parse_result import ParsedArchive
from parser_bot.service import (
    _dataset_source_requirements,
    _filter_observations_by_requested_range,
    _format_station_unavailable_message,
    _normalize_daily_term_mean,
    _saturation_vapor_pressure,
)
from parser_bot.storage import ObservationRecord


class DummyTemplate:
    def __init__(self, key: str, title: str, metric_name: str, unit: str) -> None:
        self.key = key
        self.title = title
        self.metric_name = metric_name
        self.unit = unit
        self.database_section = "Сроки"
        self.source_name = "8-срочные наблюдения на станциях, (SROK8C)"


class ServiceTestCase(unittest.TestCase):
    def test_normalize_daily_term_mean(self) -> None:
        template = DummyTemplate(
            key="daily_temperature",
            title="Суточная средняя температура воздуха",
            metric_name="Температура воздуха по сухому терм-ру",
            unit="°C",
        )
        parsed = ParsedArchive(
            zip_path=Path("dummy.zip"),
            field_names=[],
            stations={"27612": "Москва, ВДНХ"},
            records=[
                {
                    "Синоптический индекс станции": "27612",
                    "Название станции": "Москва, ВДНХ",
                    "Год источника (местный)": 2024,
                    "Месяц источника (местный)": 1,
                    "День источника (местный)": 1,
                    "Срок источника (местный)": 0,
                    "Температура воздуха по сухому терм-ру": -10.0,
                },
                {
                    "Синоптический индекс станции": "27612",
                    "Название станции": "Москва, ВДНХ",
                    "Год источника (местный)": 2024,
                    "Месяц источника (местный)": 1,
                    "День источника (местный)": 1,
                    "Срок источника (местный)": 3,
                    "Температура воздуха по сухому терм-ру": -14.0,
                },
            ],
        )

        records = _normalize_daily_term_mean(template, parsed, "2026-03-28T00:00:00+00:00")

        self.assertEqual(len(records), 1)
        self.assertEqual(records[0].observation_date, "2024-01-01")
        self.assertEqual(records[0].value, -12.0)

    def test_saturation_vapor_pressure(self) -> None:
        self.assertEqual(_saturation_vapor_pressure(20.0), 23.326)

    def test_dataset_source_requirements_for_derived_monthly_deficit(self) -> None:
        self.assertEqual(
            _dataset_source_requirements("monthly_vapor_pressure_deficit"),
            [
                ("Месяц", "Температура воздуха"),
                ("Месяц", "Упругость водяного пара"),
            ],
        )

    def test_format_station_unavailable_message_for_index(self) -> None:
        message = _format_station_unavailable_message(
            "26701",
            [("Месяц", "Температура воздуха"), ("Сроки", "8-срочные наблюдения на станциях, (SROK8C)")],
        )
        self.assertIn("26701", message)
        self.assertIn("Месяц -> Температура воздуха", message)
        self.assertIn("Сроки -> 8-срочные наблюдения на станциях, (SROK8C)", message)

    def test_filter_observations_by_requested_range_for_daily_records(self) -> None:
        records = [
            ObservationRecord(
                dataset_key="daily_temperature",
                dataset_title="Суточная средняя температура воздуха",
                station_code="27612",
                station_name="Москва, ВДНХ",
                observation_date=observation_date,
                year=2024,
                month=1,
                day=day,
                metric_name="Температура воздуха по сухому терм-ру",
                value=float(day),
                unit="°C",
                source_section="Сроки",
                source_name="8-срочные наблюдения на станциях, (SROK8C)",
                downloaded_at="2026-03-28T00:00:00+00:00",
            )
            for day, observation_date in ((15, "2024-01-15"), (16, "2024-01-16"))
        ]

        filtered = _filter_observations_by_requested_range(
            records,
            year_from=2024,
            year_to=2024,
            month_from=1,
            month_to=1,
            day_from=1,
            day_to=15,
        )

        self.assertEqual(len(filtered), 1)
        self.assertEqual(filtered[0].observation_date, "2024-01-15")


if __name__ == "__main__":
    unittest.main()
