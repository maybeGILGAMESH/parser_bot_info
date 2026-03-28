from __future__ import annotations

import logging
import re
import time
from dataclasses import dataclass
from pathlib import Path

from playwright.sync_api import Page, sync_playwright

from .config import BrowserConfig, Credentials, DatasetConfig

LOGGER = logging.getLogger(__name__)


class BrowserAutomationError(RuntimeError):
    """Raised when Playwright automation fails."""


@dataclass(slots=True)
class BrowserDownloadResult:
    dataset_name: str
    file_path: Path
    station_count: int


class AisoriBrowserClient:
    def __init__(
        self,
        browser_config: BrowserConfig,
        base_url: str = "http://aisori-m.meteo.ru/aisori-m/index1.xhtml",
    ) -> None:
        self.browser_config = browser_config
        self.base_url = base_url

    def download_dataset(
        self,
        credentials: Credentials,
        dataset: DatasetConfig,
        output_root: Path,
    ) -> BrowserDownloadResult:
        if not dataset.stations:
            raise BrowserAutomationError(
                f"Для набора '{dataset.name}' не указаны станции. Заполните поле 'stations' в конфиге."
            )

        target_dir = output_root / dataset.output_dir if not dataset.output_dir.is_absolute() else dataset.output_dir
        target_dir.mkdir(parents=True, exist_ok=True)

        with sync_playwright() as playwright:
            browser = playwright.chromium.launch(headless=self.browser_config.headless)
            context = browser.new_context(accept_downloads=True)
            page = context.new_page()
            page.set_default_timeout(20_000)

            try:
                self._login(page, credentials)
                self._open_select_page(page)
                self._prepare_source(page, dataset)
                self._select_stations(page, dataset.stations)
                self._open_query_page(page)
                self._fill_query(page, dataset)
                self._build_result(page)
                self._wait_for_result_page(page)
                download_path = self._download_result(page, target_dir)
                return BrowserDownloadResult(
                    dataset_name=dataset.name,
                    file_path=download_path,
                    station_count=len(dataset.stations),
                )
            finally:
                context.close()
                browser.close()

    def probe_station_availability(
        self,
        credentials: Credentials,
        source_requests: list[tuple[str, str]],
        station_query: str,
    ) -> dict[tuple[str, str], bool]:
        unique_requests = list(dict.fromkeys(source_requests))
        if not unique_requests:
            return {}

        with sync_playwright() as playwright:
            browser = playwright.chromium.launch(headless=self.browser_config.headless)
            context = browser.new_context(accept_downloads=False)
            page = context.new_page()
            page.set_default_timeout(20_000)

            try:
                self._login(page, credentials)
                self._open_select_page(page)
                result: dict[tuple[str, str], bool] = {}
                for database_section, source_name in unique_requests:
                    self._prepare_source_labels(page, database_section, source_name)
                    result[(database_section, source_name)] = self._station_exists(page, station_query)
                return result
            finally:
                context.close()
                browser.close()

    def _login(self, page: Page, credentials: Credentials) -> None:
        LOGGER.info("Открываю страницу входа AISORI-M")
        page.goto(self.base_url, wait_until="domcontentloaded")
        page.get_by_role("link", name="Страница авторизации").click()
        page.wait_for_timeout(1200)
        page.get_by_role("link", name="Переход на страницу авторизации").click()
        page.wait_for_timeout(1200)

        page.locator('input[name="j_idt13:usr"]').fill(credentials.username)
        page.locator('input[name="j_idt13:pwd"]').fill(credentials.password)
        page.locator('input[name="j_idt13:email"]').fill(credentials.email)
        page.get_by_role("button", name="OK").click()
        page.wait_for_timeout(1800)

        if "Выбор данных" not in page.locator("body").inner_text():
            raise BrowserAutomationError("Не удалось авторизоваться в AISORI-M")

    def _open_select_page(self, page: Page) -> None:
        page.get_by_role("button", name="Выбор данных").click()
        page.wait_for_timeout(1800)
        if not page.url.endswith("/select.xhtml"):
            raise BrowserAutomationError(f"Не удалось открыть select.xhtml, текущий URL: {page.url}")

    def _prepare_source(self, page: Page, dataset: DatasetConfig) -> None:
        self._prepare_source_labels(page, dataset.database_section, dataset.source_name)

    @staticmethod
    def _prepare_source_labels(page: Page, database_section: str, source_name: str) -> None:
        page.select_option('select[name="form1:razbd"]', label=database_section)
        page.wait_for_timeout(1800)
        page.select_option('select[name="form1:istd"]', label=source_name)
        page.wait_for_timeout(2200)

    def _select_stations(self, page: Page, stations: list[str]) -> None:
        filter_input = page.locator('input[id="form1:hlist1_filter"]')
        resolved_stations: list[str] = []
        for station in stations:
            station = station.strip()
            query = station if station.isdigit() else station.split(" ", 1)[1] if " " in station else station
            filter_input.fill(query)
            page.wait_for_timeout(800)

            station_text, station_locator = self._resolve_station_locator(page, station)

            station_locator.click()
            page.get_by_role("button", name=">", exact=True).click()
            page.wait_for_timeout(1200)
            filter_input.fill("")
            page.wait_for_timeout(300)
            resolved_stations.append(station_text)

        selected_text = page.locator("#form1\\:hlist2 ul.ui-selectlistbox-list").inner_text()
        missing = [station for station in resolved_stations if station not in selected_text]
        if missing:
            raise BrowserAutomationError(f"Не удалось перенести станции в правый список: {missing}")

    def _station_exists(self, page: Page, station_query: str) -> bool:
        filter_input = page.locator('input[id="form1:hlist1_filter"]')
        query = station_query if station_query.isdigit() else station_query.split(" ", 1)[1] if " " in station_query else station_query
        filter_input.fill(query)
        page.wait_for_timeout(800)
        station_match = self._find_station_locator(page, station_query)
        filter_input.fill("")
        page.wait_for_timeout(300)
        return station_match is not None

    def _open_query_page(self, page: Page) -> None:
        page.get_by_role("button", name="Дальше").click()
        page.wait_for_timeout(2500)
        body_text = page.locator("body").inner_text()
        if "Параметры запроса" not in body_text:
            raise BrowserAutomationError("Не удалось открыть страницу параметров запроса")

    def _fill_query(self, page: Page, dataset: DatasetConfig) -> None:
        section = dataset.database_section.strip().casefold()
        if section == "сутки":
            self._fill_daily_filters(page, dataset)
        elif section == "сроки":
            self._fill_term_filters(page, dataset)
        else:
            self._fill_monthly_filters(page, dataset)

        self._apply_query_parameter_selection(page, dataset)
        self._validate_result_fields(page, dataset)

        if dataset.output_mode == "per_station":
            page.get_by_label("Файлы по станциям").check()
        else:
            page.get_by_label("Одним файлом").check()

        page.wait_for_timeout(800)

    def _build_result(self, page: Page) -> None:
        page.get_by_role("button", name="Результат").click()
        page.get_by_text("Ожидайте завершения запроса").wait_for(state="visible")
        get_result_button = page.get_by_role("button", name="Получить результат")
        self._wait_until_enabled(page, get_result_button, timeout_ms=45_000)
        get_result_button.click()

    def _wait_for_result_page(self, page: Page) -> None:
        page.wait_for_url("**/result.xhtml", timeout=45_000)
        page.wait_for_timeout(1200)
        if "Загрузить" not in page.locator("body").inner_text():
            raise BrowserAutomationError("Страница результата открылась, но кнопка 'Загрузить' не найдена")

    def _download_result(self, page: Page, target_dir: Path) -> Path:
        with page.expect_download(timeout=30_000) as download_info:
            page.get_by_role("button", name="Загрузить").click()
        download = download_info.value
        suggested_name = sanitize_download_name(download.suggested_filename)
        target_path = target_dir / suggested_name
        download.save_as(str(target_path))
        return target_path

    @staticmethod
    def _wait_until_enabled(page: Page, locator, timeout_ms: int) -> None:
        deadline = time.monotonic() + (timeout_ms / 1000)
        while locator.is_disabled():
            page.wait_for_timeout(1000)
            if time.monotonic() >= deadline:
                raise BrowserAutomationError(
                    "Кнопка 'Получить результат' не стала активной в ожидаемое время"
                )

    @staticmethod
    def _fill_monthly_filters(page: Page, dataset: DatasetConfig) -> None:
        if dataset.year_from is not None:
            page.locator('input[name="form2:j_idt37:1:j_idt41"]').fill(str(dataset.year_from))
        if dataset.year_to is not None:
            page.locator('input[name="form2:j_idt37:1:j_idt43"]').fill(str(dataset.year_to))

    @staticmethod
    def _fill_daily_filters(page: Page, dataset: DatasetConfig) -> None:
        if dataset.year_from is not None:
            page.locator('input[name="form2:j_idt37:0:j_idt41"]').fill(str(dataset.year_from))
        if dataset.year_to is not None:
            page.locator('input[name="form2:j_idt37:0:j_idt43"]').fill(str(dataset.year_to))
        if dataset.month_from is not None:
            page.locator('input[name="form2:j_idt37:1:j_idt41"]').fill(str(dataset.month_from))
        if dataset.month_to is not None:
            page.locator('input[name="form2:j_idt37:1:j_idt43"]').fill(str(dataset.month_to))
        if dataset.day_from is not None:
            page.locator('input[name="form2:j_idt37:2:j_idt41"]').fill(str(dataset.day_from))
        if dataset.day_to is not None:
            page.locator('input[name="form2:j_idt37:2:j_idt43"]').fill(str(dataset.day_to))

    @staticmethod
    def _fill_term_filters(page: Page, dataset: DatasetConfig) -> None:
        if dataset.year_from is not None:
            page.locator('input[name="form2:j_idt37:0:j_idt41"]').fill(str(dataset.year_from))
        if dataset.year_to is not None:
            page.locator('input[name="form2:j_idt37:0:j_idt43"]').fill(str(dataset.year_to))
        if dataset.month_from is not None:
            page.locator('input[name="form2:j_idt37:1:j_idt41"]').fill(str(dataset.month_from))
        if dataset.month_to is not None:
            page.locator('input[name="form2:j_idt37:1:j_idt43"]').fill(str(dataset.month_to))
        if dataset.day_from is not None:
            page.locator('input[name="form2:j_idt37:2:j_idt41"]').fill(str(dataset.day_from))
        if dataset.day_to is not None:
            page.locator('input[name="form2:j_idt37:2:j_idt43"]').fill(str(dataset.day_to))

    def _apply_query_parameter_selection(self, page: Page, dataset: DatasetConfig) -> None:
        available_parameters = [
            option.strip()
            for option in page.locator('select[name="form2:flist1_input"] option').all_inner_texts()
            if option.strip()
        ]
        if not available_parameters:
            raise BrowserAutomationError("На странице запроса не найден список параметров для выбора")

        requested_parameters = dataset.query_parameters or dataset.months
        if requested_parameters:
            missing = sorted(set(requested_parameters) - set(available_parameters))
            if missing:
                raise BrowserAutomationError(
                    f"Для набора '{dataset.name}' не найдены параметры на странице запроса: {missing}"
                )
            page.select_option('select[name="form2:flist1_input"]', label=requested_parameters)
            page.locator('button[id="form2:cb2"]').click()
        else:
            page.locator('button[id="form2:cb1"]').click()
        page.wait_for_timeout(1200)

    def _validate_result_fields(self, page: Page, dataset: DatasetConfig) -> None:
        if not dataset.result_fields:
            return
        current_fields = {
            option.strip()
            for option in page.locator('select[name="form2:flist2_input"] option').all_inner_texts()
            if option.strip()
        }
        missing = sorted(set(dataset.result_fields) - current_fields)
        if missing:
            raise BrowserAutomationError(
                f"Для набора '{dataset.name}' итоговые поля результата не сформировались: {missing}"
            )

    @staticmethod
    def _resolve_station_locator(page: Page, station_query: str):
        station_match = AisoriBrowserClient._find_station_locator(page, station_query)
        if station_match is not None:
            return station_match
        if station_query.isdigit():
            raise BrowserAutomationError(f"Станция с индексом '{station_query}' не найдена в списке")
        raise BrowserAutomationError(f"Станция '{station_query}' не найдена в списке")

    @staticmethod
    def _find_station_locator(page: Page, station_query: str):
        if station_query.isdigit():
            station_options = page.locator("#form1\\:hlist1 li")
            option_count = station_options.count()
            for index in range(option_count):
                option = station_options.nth(index)
                option_text = option.inner_text().strip()
                if option_text.startswith(f"{station_query} "):
                    return option_text, option
            return None

        station_locator = page.locator("#form1\\:hlist1 li", has_text=station_query).first
        if station_locator.count() == 0:
            return None
        return station_locator.inner_text().strip(), station_locator


def sanitize_download_name(filename: str) -> str:
    cleaned = re.sub(r"[^\w.\-]+", "_", filename, flags=re.UNICODE).strip("._")
    return cleaned or "result.zip"
