from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from pathlib import Path
from typing import Any


@dataclass(frozen=True, slots=True)
class ObservationRecord:
    dataset_key: str
    dataset_title: str
    station_code: str
    station_name: str
    observation_date: str
    year: int
    month: int
    day: int | None
    metric_name: str
    value: float
    unit: str
    source_section: str
    source_name: str
    downloaded_at: str


class ObservationStore:
    def __init__(self, database_path: str | Path) -> None:
        self.database_path = Path(database_path)
        self.database_path.parent.mkdir(parents=True, exist_ok=True)
        self._initialize()

    def upsert_observations(self, records: list[ObservationRecord]) -> int:
        if not records:
            return 0
        with sqlite3.connect(self.database_path) as connection:
            connection.executemany(
                """
                INSERT INTO observations (
                    dataset_key, dataset_title, station_code, station_name, observation_date,
                    year, month, day, metric_name, value, unit, source_section, source_name, downloaded_at
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(dataset_key, station_code, observation_date, metric_name)
                DO UPDATE SET
                    dataset_title=excluded.dataset_title,
                    station_name=excluded.station_name,
                    year=excluded.year,
                    month=excluded.month,
                    day=excluded.day,
                    value=excluded.value,
                    unit=excluded.unit,
                    source_section=excluded.source_section,
                    source_name=excluded.source_name,
                    downloaded_at=excluded.downloaded_at
                """,
                [
                    (
                        record.dataset_key,
                        record.dataset_title,
                        record.station_code,
                        record.station_name,
                        record.observation_date,
                        record.year,
                        record.month,
                        record.day,
                        record.metric_name,
                        record.value,
                        record.unit,
                        record.source_section,
                        record.source_name,
                        record.downloaded_at,
                    )
                    for record in records
                ],
            )
            connection.commit()
        return len(records)

    def log_refresh(
        self,
        dataset_key: str,
        station_code: str,
        station_name: str,
        status: str,
        message: str,
        zip_path: str,
        record_count: int,
        started_at: str,
        finished_at: str,
    ) -> None:
        with sqlite3.connect(self.database_path) as connection:
            connection.execute(
                """
                INSERT INTO refresh_runs (
                    dataset_key, station_code, station_name, status, message,
                    zip_path, record_count, started_at, finished_at
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    dataset_key,
                    station_code,
                    station_name,
                    status,
                    message,
                    zip_path,
                    record_count,
                    started_at,
                    finished_at,
                ),
            )
            connection.commit()

    def fetch_observations(self, station_code: str) -> list[dict[str, Any]]:
        with sqlite3.connect(self.database_path) as connection:
            connection.row_factory = sqlite3.Row
            rows = connection.execute(
                """
                SELECT *
                FROM observations
                WHERE station_code = ?
                ORDER BY observation_date, dataset_key
                """,
                (station_code,),
            ).fetchall()
        return [dict(row) for row in rows]

    def fetch_latest_refreshes(self, station_code: str) -> list[dict[str, Any]]:
        with sqlite3.connect(self.database_path) as connection:
            connection.row_factory = sqlite3.Row
            rows = connection.execute(
                """
                SELECT dataset_key, station_code, station_name, status, message, zip_path,
                       record_count, started_at, finished_at
                FROM refresh_runs
                WHERE station_code = ?
                ORDER BY finished_at DESC
                LIMIT 20
                """,
                (station_code,),
            ).fetchall()
        return [dict(row) for row in rows]

    def _initialize(self) -> None:
        with sqlite3.connect(self.database_path) as connection:
            connection.executescript(
                """
                CREATE TABLE IF NOT EXISTS observations (
                    dataset_key TEXT NOT NULL,
                    dataset_title TEXT NOT NULL,
                    station_code TEXT NOT NULL,
                    station_name TEXT NOT NULL,
                    observation_date TEXT NOT NULL,
                    year INTEGER NOT NULL,
                    month INTEGER NOT NULL,
                    day INTEGER,
                    metric_name TEXT NOT NULL,
                    value REAL NOT NULL,
                    unit TEXT NOT NULL,
                    source_section TEXT NOT NULL,
                    source_name TEXT NOT NULL,
                    downloaded_at TEXT NOT NULL,
                    PRIMARY KEY (dataset_key, station_code, observation_date, metric_name)
                );

                CREATE TABLE IF NOT EXISTS refresh_runs (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    dataset_key TEXT NOT NULL,
                    station_code TEXT NOT NULL,
                    station_name TEXT NOT NULL,
                    status TEXT NOT NULL,
                    message TEXT NOT NULL,
                    zip_path TEXT NOT NULL,
                    record_count INTEGER NOT NULL,
                    started_at TEXT NOT NULL,
                    finished_at TEXT NOT NULL
                );
                """
            )
            connection.commit()
