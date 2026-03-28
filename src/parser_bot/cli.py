from __future__ import annotations

import argparse
import json
import logging
from pathlib import Path

from .catalog import DATASET_TEMPLATES
from .client import AisoriClient
from .config import AppConfig, load_config
from .parse_result import parse_result_zip
from .service import AisoriDataService
from .station_catalog import build_station_catalog
from .scheduler import run_once, run_scheduler


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="AISORI-M parser bot")
    parser.add_argument(
        "--config",
        default="config/datasets.json",
        help="Путь до JSON-конфига",
    )
    parser.add_argument(
        "--log-level",
        default="INFO",
        help="Уровень логирования: DEBUG, INFO, WARNING, ERROR",
    )
    parser.add_argument(
        "--headed",
        action="store_true",
        help="Запускать Playwright в видимом браузере",
    )

    subparsers = parser.add_subparsers(dest="command", required=True)
    subparsers.add_parser("run-once", help="Скачать все наборы один раз")
    subparsers.add_parser("run-scheduler", help="Запустить ежедневный планировщик")

    list_sources = subparsers.add_parser("list-sources", help="Показать доступные источники")
    list_sources.add_argument("--section", required=True, help="Название раздела БД, например 'Месяц'")

    parse_zip = subparsers.add_parser("parse-zip", help="Разобрать скачанный ZIP и показать записи")
    parse_zip.add_argument("--zip-path", required=True, help="Путь до wr*.zip")
    parse_zip.add_argument("--limit", type=int, default=5, help="Сколько записей показать")

    refresh_station = subparsers.add_parser("refresh-station", help="Обновить выбранные наборы по индексу станции")
    refresh_station.add_argument("--station", required=True, help="Индекс станции, например 27612")
    refresh_station.add_argument(
        "--datasets",
        nargs="+",
        required=True,
        help=f"Ключи наборов: {', '.join(DATASET_TEMPLATES)}",
    )
    refresh_station.add_argument("--year-from", type=int, required=True, help="Год начала диапазона")
    refresh_station.add_argument("--year-to", type=int, required=True, help="Год конца диапазона")
    refresh_station.add_argument("--month-from", type=int, help="Месяц начала для суточных данных")
    refresh_station.add_argument("--month-to", type=int, help="Месяц конца для суточных данных")
    refresh_station.add_argument("--day-from", type=int, help="День начала для суточных данных")
    refresh_station.add_argument("--day-to", type=int, help="День конца для суточных данных")

    build_catalog = subparsers.add_parser("build-station-catalog", help="Построить локальный справочник станций из PDF")
    build_catalog.add_argument("--pdf-path", required=True, help="Путь к PDF-каталогу станций")
    build_catalog.add_argument(
        "--output-path",
        default="data/stations/monthly_temperature_station_catalog.json",
        help="Куда сохранить JSON-справочник",
    )
    return parser


def main() -> None:
    parser = build_parser()
    args = parser.parse_args()

    logging.basicConfig(
        level=getattr(logging, args.log_level.upper(), logging.INFO),
        format="%(asctime)s | %(levelname)s | %(name)s | %(message)s",
    )

    if args.command == "parse-zip":
        _parse_zip(args.zip_path, args.limit)
        return

    if args.command == "build-station-catalog":
        _build_station_catalog(args.pdf_path, args.output_path)
        return

    config_path = Path(args.config).resolve()
    project_root = Path.cwd().resolve()
    config = load_config(config_path)
    if args.headed:
        config.browser.headless = False

    if args.command == "run-once":
        run_once(config, project_root)
        return

    if args.command == "run-scheduler":
        run_scheduler(config, project_root)
        return

    if args.command == "list-sources":
        _list_sources(config, args.section)
        return

    if args.command == "refresh-station":
        _refresh_station(config, project_root, args)
        return

    parser.error(f"Неизвестная команда: {args.command}")


def _list_sources(config: AppConfig, section: str) -> None:
    client = AisoriClient()
    client.login(
        username=config.credentials.username,
        password=config.credentials.password,
        email=config.credentials.email,
    )
    client.open_select_page()
    options = client.list_sources(section)
    if not options:
        print(f"Для раздела '{section}' источники не найдены")
        return
    for value, label in options:
        print(f"{value}\t{label}")


def _parse_zip(zip_path: str, limit: int) -> None:
    parsed = parse_result_zip(zip_path)
    preview = {
        "zip_path": str(parsed.zip_path),
        "field_names": parsed.field_names,
        "stations": parsed.stations,
        "records": parsed.records[:limit],
    }
    print(json.dumps(preview, ensure_ascii=False, indent=2))


def _refresh_station(config: AppConfig, project_root: Path, args) -> None:
    service = AisoriDataService(config=config, project_root=project_root)
    for dataset_key in args.datasets:
        result = service.refresh_dataset(
            dataset_key=dataset_key,
            station_query=args.station,
            year_from=args.year_from,
            year_to=args.year_to,
            month_from=args.month_from,
            month_to=args.month_to,
            day_from=args.day_from,
            day_to=args.day_to,
        )
        print(
            json.dumps(
                {
                    "dataset_key": result.dataset_key,
                    "dataset_title": result.dataset_title,
                    "station_code": result.station_code,
                    "station_name": result.station_name,
                    "record_count": result.record_count,
                    "zip_path": str(result.zip_path),
                    "status": result.status,
                    "message": result.message,
                },
                ensure_ascii=False,
                indent=2,
            )
        )


def _build_station_catalog(pdf_path: str, output_path: str) -> None:
    entries = build_station_catalog(source_path=pdf_path, output_path=output_path)
    print(
        json.dumps(
            {
                "source_path": pdf_path,
                "output_path": output_path,
                "station_count": len(entries),
            },
            ensure_ascii=False,
            indent=2,
        )
    )


if __name__ == "__main__":
    main()
