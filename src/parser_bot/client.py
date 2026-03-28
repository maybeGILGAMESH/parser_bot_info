from __future__ import annotations

import logging
import re
from dataclasses import dataclass
from html import unescape
from pathlib import Path
from typing import Iterable
from xml.etree import ElementTree

import requests

LOGGER = logging.getLogger(__name__)


class AisoriError(RuntimeError):
    """Raised when AISORI-M workflow fails."""


@dataclass(slots=True)
class DownloadResult:
    dataset_name: str
    file_path: Path
    url: str


def extract_view_state(html: str) -> str:
    match = re.search(r'name="jakarta.faces.ViewState"[^>]+value="([^"]+)"', html)
    if not match:
        raise AisoriError("Не удалось найти jakarta.faces.ViewState")
    return match.group(1)


def extract_form_action(html: str, form_id: str) -> str:
    pattern = rf'<form\b(?=[^>]*\bid="{re.escape(form_id)}")[^>]*\baction="([^"]+)"'
    match = re.search(pattern, html)
    if not match:
        raise AisoriError(f"Не удалось найти form action для формы {form_id}")
    return match.group(1)


def parse_select_options(select_html: str) -> list[tuple[str, str]]:
    return [
        (value.strip(), unescape(label).strip())
        for value, label in re.findall(r'<option value="([^"]*)">(.*?)</option>', select_html, re.S)
    ]


def parse_partial_response(xml_text: str) -> dict[str, str]:
    root = ElementTree.fromstring(xml_text)
    updates: dict[str, str] = {}
    for update in root.findall(".//update"):
        update_id = update.attrib.get("id")
        if update_id:
            updates[update_id] = update.text or ""
    return updates


def sanitize_filename(name: str) -> str:
    sanitized = re.sub(r"[^\w.\-]+", "_", name, flags=re.UNICODE).strip("._")
    return sanitized or "archive.bin"


class AisoriClient:
    def __init__(
        self,
        base_url: str = "http://aisori-m.meteo.ru/aisori-m",
        timeout: int = 30,
        session: requests.Session | None = None,
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self.timeout = timeout
        self.session = session or requests.Session()
        self._index_html: str | None = None
        self._select_html: str | None = None
        self._select_view_state: str | None = None

    def login(self, username: str, password: str, email: str) -> None:
        LOGGER.info("Открываю главную страницу AISORI-M")
        landing = self.session.get(f"{self.base_url}/index1.xhtml", timeout=self.timeout)
        landing.raise_for_status()

        auth_view_state = extract_view_state(landing.text)
        login_page = self.session.post(
            f"{self.base_url}/index1.xhtml",
            data={
                "j_idt10": "j_idt10",
                "j_idt10:j_idt12": "j_idt10:j_idt12",
                "jakarta.faces.ViewState": auth_view_state,
            },
            timeout=self.timeout,
        )
        login_page.raise_for_status()
        if 'id="j_idt13"' not in login_page.text and "Переход на страницу авторизации" in login_page.text:
            middle_view_state = extract_view_state(login_page.text)
            middle_action = extract_form_action(login_page.text, "j_idt12")
            login_page = self.session.post(
                self._absolute_url(middle_action),
                data={
                    "j_idt12": "j_idt12",
                    "j_idt12:j_idt17": "j_idt12:j_idt17",
                    "jakarta.faces.ViewState": middle_view_state,
                },
                timeout=self.timeout,
            )
            login_page.raise_for_status()

        login_view_state = extract_view_state(login_page.text)
        login_action = extract_form_action(login_page.text, "j_idt13")
        index_page = self.session.post(
            self._absolute_url(login_action),
            data={
                "j_idt13": "j_idt13",
                "j_idt13:usr": username,
                "j_idt13:pwd": password,
                "j_idt13:email": email,
                "j_idt13:j_idt27": "j_idt13:j_idt27",
                "jakarta.faces.ViewState": login_view_state,
            },
            timeout=self.timeout,
        )
        index_page.raise_for_status()
        if "Выбор данных" not in index_page.text:
            raise AisoriError("Авторизация не удалась: кнопка 'Выбор данных' не найдена")

        self._index_html = index_page.text
        LOGGER.info("Авторизация выполнена")

    def open_select_page(self) -> str:
        if not self._index_html:
            raise AisoriError("Сначала нужно выполнить login()")

        index_view_state = extract_view_state(self._index_html)
        page = self.session.post(
            f"{self.base_url}/index1.xhtml",
            data={
                "form1": "form1",
                "form1:newbut1": "form1:newbut1",
                "jakarta.faces.ViewState": index_view_state,
            },
            timeout=self.timeout,
        )
        page.raise_for_status()
        if "Раздел БД" not in page.text:
            raise AisoriError("Не удалось открыть страницу выбора данных")

        self._select_html = page.text
        self._select_view_state = extract_view_state(page.text)
        return page.text

    def list_sources(self, section_name: str) -> list[tuple[str, str]]:
        if not self._select_html:
            self.open_select_page()

        updates = self._ajax_change_section(section_name)
        select_html = updates.get("form1:istd", "")
        options = [item for item in parse_select_options(select_html) if item[0]]
        return options

    def download_full_archive(
        self,
        dataset_name: str,
        section_name: str,
        source_name: str,
        output_dir: Path,
    ) -> DownloadResult:
        if not self._select_html:
            self.open_select_page()

        self._ajax_change_section(section_name)
        self._ajax_change_source(section_name, source_name)

        output_dir.mkdir(parents=True, exist_ok=True)
        response = self.session.post(
            f"{self.base_url}/select.xhtml",
            data={
                "form1": "form1",
                "form1:razbd": section_name,
                "form1:istd": source_name,
                "form1:cbload": "form1:cbload",
                "jakarta.faces.ViewState": self._require_select_view_state(),
            },
            timeout=self.timeout,
            stream=True,
        )
        response.raise_for_status()

        content_type = response.headers.get("Content-Type", "")
        if "text/html" in content_type.lower():
            preview = response.text[:800]
            raise AisoriError(
                f"Сайт вернул HTML вместо файла при скачивании '{dataset_name}'. "
                f"Фрагмент ответа: {preview}"
            )

        filename = self._extract_filename(response.headers) or sanitize_filename(
            f"{dataset_name}_{section_name}.bin"
        )
        target_path = output_dir / filename
        with target_path.open("wb") as handle:
            for chunk in response.iter_content(chunk_size=1024 * 128):
                if chunk:
                    handle.write(chunk)

        LOGGER.info("Файл сохранен: %s", target_path)
        return DownloadResult(dataset_name=dataset_name, file_path=target_path, url=response.url)

    def _ajax_change_section(self, section_name: str) -> dict[str, str]:
        updates = self._post_partial(
            source="form1:razbd",
            execute="form1:razbd",
            render="form1:istd form1:hlist1 form1:hlist2 form1:selinf form1:allzipinfo form1:allzipinfo2",
            extra_fields={
                "form1:razbd": section_name,
            },
        )
        self._apply_updates(updates)
        return updates

    def _ajax_change_source(self, section_name: str, source_name: str) -> dict[str, str]:
        updates = self._post_partial(
            source="form1:istd",
            execute="form1:istd",
            render="form1:hlist1 form1:hlist2 form1:selinf form1:allzipinfo form1:allzipinfo2",
            extra_fields={
                "form1:razbd": section_name,
                "form1:istd": source_name,
            },
        )
        self._apply_updates(updates)
        return updates

    def _post_partial(
        self,
        source: str,
        execute: str,
        render: str,
        extra_fields: dict[str, str],
    ) -> dict[str, str]:
        response = self.session.post(
            f"{self.base_url}/select.xhtml",
            data={
                "form1": "form1",
                "jakarta.faces.partial.ajax": "true",
                "jakarta.faces.source": source,
                "jakarta.faces.partial.execute": execute,
                "jakarta.faces.partial.render": render,
                "jakarta.faces.behavior.event": "valueChange",
                "jakarta.faces.partial.event": "valueChange",
                "jakarta.faces.ViewState": self._require_select_view_state(),
                **extra_fields,
            },
            headers={"Faces-Request": "partial/ajax"},
            timeout=self.timeout,
        )
        response.raise_for_status()
        if "<partial-response>" not in response.text:
            raise AisoriError("Сервер не вернул partial-response для AJAX-запроса")
        return parse_partial_response(response.text)

    def _apply_updates(self, updates: dict[str, str]) -> None:
        for update_id, value in updates.items():
            if "jakarta.faces.ViewState" in update_id:
                self._select_view_state = value
        if "form1" in updates:
            self._select_html = updates["form1"]

    def _require_select_view_state(self) -> str:
        if not self._select_view_state:
            raise AisoriError("Не удалось получить ViewState страницы выбора данных")
        return self._select_view_state

    def _absolute_url(self, action: str) -> str:
        if action.startswith("http://") or action.startswith("https://"):
            return action
        return f"http://aisori-m.meteo.ru{action}"

    @staticmethod
    def _extract_filename(headers: requests.structures.CaseInsensitiveDict[str]) -> str | None:
        disposition = headers.get("Content-Disposition", "")
        match = re.search(r'filename="?([^";]+)"?', disposition)
        if not match:
            return None
        return sanitize_filename(unescape(match.group(1)))


def download_datasets(
    client: AisoriClient,
    datasets: Iterable[tuple[str, str, str, Path]],
) -> list[DownloadResult]:
    results: list[DownloadResult] = []
    for dataset_name, section_name, source_name, output_dir in datasets:
        results.append(
            client.download_full_archive(
                dataset_name=dataset_name,
                section_name=section_name,
                source_name=source_name,
                output_dir=output_dir,
            )
        )
    return results
