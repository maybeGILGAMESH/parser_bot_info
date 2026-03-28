from __future__ import annotations

import logging
import time
from pathlib import Path
from zoneinfo import ZoneInfo

from apscheduler.schedulers.blocking import BlockingScheduler
from apscheduler.triggers.cron import CronTrigger

from .browser_client import AisoriBrowserClient
from .config import AppConfig

LOGGER = logging.getLogger(__name__)


def run_once(config: AppConfig, config_dir: Path) -> None:
    client = AisoriBrowserClient(browser_config=config.browser)
    for dataset in config.datasets:
        result = client.download_dataset(
            credentials=config.credentials,
            dataset=dataset,
            output_root=config_dir,
        )
        LOGGER.info(
            "Скачан набор %s (%d станций) -> %s",
            result.dataset_name,
            result.station_count,
            result.file_path,
        )


def run_scheduler(config: AppConfig, config_dir: Path) -> None:
    timezone = ZoneInfo(config.timezone)
    scheduler = BlockingScheduler(timezone=timezone)
    scheduler.add_job(
        run_once,
        CronTrigger(hour=config.schedule.hour, minute=config.schedule.minute, timezone=timezone),
        kwargs={"config": config, "config_dir": config_dir},
        id="daily-download",
        replace_existing=True,
    )
    LOGGER.info(
        "Планировщик запущен. Ежедневный запуск в %02d:%02d (%s)",
        config.schedule.hour,
        config.schedule.minute,
        config.timezone,
    )
    try:
        scheduler.start()
    except (KeyboardInterrupt, SystemExit):
        LOGGER.info("Планировщик остановлен")
        time.sleep(0.1)


def _resolve_output_path(config_dir: Path, output_dir: Path) -> Path:
    if output_dir.is_absolute():
        return output_dir
    return (config_dir / output_dir).resolve()
