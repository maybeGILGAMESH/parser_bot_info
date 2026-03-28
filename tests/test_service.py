import unittest
from pathlib import Path

from parser_bot.parse_result import ParsedArchive
from parser_bot.service import _normalize_daily_term_mean, _saturation_vapor_pressure


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


if __name__ == "__main__":
    unittest.main()
