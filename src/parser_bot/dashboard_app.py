from __future__ import annotations

from dataclasses import asdict
from datetime import date
from pathlib import Path

import pandas as pd
import plotly.graph_objects as go
import streamlit as st

from parser_bot.catalog import DATASET_TEMPLATES, list_dataset_templates
from parser_bot.config import load_config
from parser_bot.service import AisoriDataService
from parser_bot.station_catalog import load_station_catalog
from parser_bot.transition_matrices import analyze_transition_workbooks, matrix_to_frame

PROJECT_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_CONFIG_PATH = PROJECT_ROOT / "config" / "datasets.json"
DEFAULT_STATION_CATALOG_PATH = PROJECT_ROOT / "data" / "stations" / "monthly_temperature_station_catalog.json"
DEFAULT_MATRIX_FILE_PATHS = [
    "/home/user/Downloads/матрицы.xls",
    "/home/user/Downloads/матрицготов.xls",
    "/home/user/Downloads/матрицыc jcf 1.xls",
    "/home/user/Downloads/матрицыc jcf2.xls",
]


def main() -> None:
    st.set_page_config(
        page_title="AISORI-M Climate Dashboard",
        page_icon="M",
        layout="wide",
        initial_sidebar_state="expanded",
    )
    _inject_styles()

    st.markdown(
        """
        <div class="hero">
            <div>
                <div class="eyebrow">AISORI-M / meteo.ru</div>
                <h1>Климатическая витрина по станциям</h1>
                <p>Ручное обновление по индексу станции, единое локальное хранилище и графики по месячным и суточным рядам.</p>
            </div>
        </div>
        """,
        unsafe_allow_html=True,
    )

    with st.sidebar:
        st.subheader("Управление")
        config_path_str = st.text_input("Путь к конфигу", str(DEFAULT_CONFIG_PATH))
        station_catalog_path_str = st.text_input("Путь к справочнику станций", str(DEFAULT_STATION_CATALOG_PATH))
        station_search = st.text_input("Поиск станции", "").strip()
        active_only = st.checkbox("Только вероятно действующие станции", value=False)
        map_size_mode = st.selectbox(
            "Размер маркера на карте",
            options=["По годам наблюдений", "По высоте станции", "Одинаковый"],
            index=0,
        )
        monthly_years = st.slider("Месячный диапазон лет", min_value=1980, max_value=date.today().year, value=(2020, 2022))
        daily_dates = st.date_input(
            "Суточный диапазон",
            value=(date(2024, 1, 1), date(2024, 1, 15)),
            format="YYYY-MM-DD",
        )
        enabled_templates = [item for item in list_dataset_templates() if item.available]
        selected_datasets = st.multiselect(
            "Какие наборы обновлять",
            options=[item.key for item in enabled_templates],
            default=[
                "monthly_temperature",
                "monthly_humidity",
                "monthly_precipitation",
                "monthly_vapor_pressure",
                "monthly_vapor_pressure_deficit",
                "daily_temperature",
                "daily_humidity",
                "daily_vapor_pressure_deficit",
                "daily_precipitation",
            ],
            format_func=lambda key: DATASET_TEMPLATES[key].title,
        )
        refresh_clicked = st.button("Обновить данные сейчас", width="stretch", type="primary")

    config_path = Path(config_path_str).expanduser()
    station_catalog_path = Path(station_catalog_path_str).expanduser()
    try:
        service = get_service(config_path, config_path.stat().st_mtime_ns)
    except Exception as exc:
        st.error(f"Не удалось загрузить конфиг или сервис данных: {exc}")
        st.stop()
    stations_frame = load_station_catalog_frame(station_catalog_path)
    stations_frame = apply_map_size_mode(stations_frame, map_size_mode)
    filtered_stations = filter_stations(stations_frame, station_search, active_only)
    stations_tab, matrices_tab = st.tabs(["Станции и ряды", "Матрицы переходных кривых"])

    with stations_tab:
        selected_station = render_station_catalog(filtered_stations, stations_frame, station_search, map_size_mode)
        station_code = selected_station["wmo_index"] if selected_station is not None else ""
        validation = validate_station_request(selected_station, monthly_years, daily_dates, selected_datasets)
        render_station_validation(validation)
        handle_refresh_actions(
            refresh_clicked=refresh_clicked,
            validation=validation,
            service=service,
            station_code=station_code,
            selected_datasets=selected_datasets,
            monthly_years=monthly_years,
            daily_dates=daily_dates,
        )

        observations = pd.DataFrame(service.fetch_observations(station_code))
        refresh_log = pd.DataFrame(service.fetch_latest_refreshes(station_code))

        if observations.empty:
            st.info("По этой станции в локальном хранилище пока нет данных. Нажмите кнопку обновления в боковой панели.")
        else:
            _render_summary(observations)
            _render_charts(observations)
            _render_tables(observations, refresh_log)

    with matrices_tab:
        render_transition_matrix_placeholder()


@st.cache_resource(show_spinner=False)
def get_service(config_path: Path, _config_mtime_ns: int) -> AisoriDataService:
    config = load_config(config_path)
    return AisoriDataService(config=config, project_root=PROJECT_ROOT)


@st.cache_data(show_spinner=False)
def load_station_catalog_frame(catalog_path: Path) -> pd.DataFrame:
    entries = load_station_catalog(catalog_path)
    frame = pd.DataFrame([asdict(entry) for entry in entries])
    if frame.empty:
        return frame
    for column, default in (
        ("note", ""),
        ("closure_note", ""),
        ("transfer_note", ""),
        ("rename_note", ""),
        ("incident_note", ""),
        ("status_label", "Активная/рабочая"),
        ("operation_period", ""),
    ):
        if column not in frame:
            frame[column] = default
        frame[column] = frame[column].fillna(default)
    if "is_likely_active" not in frame:
        frame["is_likely_active"] = True
    frame["is_likely_active"] = frame["is_likely_active"].fillna(True)
    frame["label"] = frame["wmo_index"] + " - " + frame["station_name"]
    frame["marker_size"] = frame["observation_years"].clip(lower=1).pow(0.55) * 2.6
    return frame


def validate_station_request(
    selected_station,
    monthly_years: tuple[int, int],
    daily_dates,
    selected_datasets: list[str],
) -> dict:
    validation = {"blocking_messages": [], "warning_messages": []}
    if selected_station is None or not selected_datasets:
        return validation

    requested_year_from, requested_year_to = _requested_year_bounds(monthly_years, daily_dates, selected_datasets)
    start_year = _optional_int(selected_station.get("start_year"))
    end_year = _optional_int(selected_station.get("end_year"))
    status_label = str(selected_station.get("status_label", "")).strip()

    if start_year is not None and requested_year_from is not None and requested_year_from < start_year:
        validation["blocking_messages"].append(
            f"Станция начала работать только с {start_year} года, а запрос начинается с {requested_year_from}."
        )
    if end_year is not None and requested_year_to is not None and requested_year_to > int(end_year):
        validation["blocking_messages"].append(
            f"Станция отмечена как `{status_label}` и в справочнике закрыта по {end_year} год. Запрос идет до {requested_year_to}."
        )
    if end_year is not None and requested_year_from is not None and requested_year_from > int(end_year):
        validation["blocking_messages"].append(
            f"Запрошен период после завершения работы станции: станция закрыта по {end_year} год, а запрос начинается с {requested_year_from}."
        )

    rename_note = str(selected_station.get("rename_note", "")).strip()
    transfer_note = str(selected_station.get("transfer_note", "")).strip()
    closure_note = str(selected_station.get("closure_note", "")).strip()
    incident_note = str(selected_station.get("incident_note", "")).strip()

    if rename_note:
        validation["warning_messages"].append(f"У станции менялось название: {rename_note}")
    if transfer_note:
        validation["warning_messages"].append(f"У станции были переносы: {transfer_note}")
    if closure_note and end_year is None:
        validation["warning_messages"].append(
            f"В примечаниях есть сведения о перерывах или консервации, даже если станция считается действующей: {closure_note}"
        )
    if incident_note:
        validation["warning_messages"].append(f"В истории станции есть инциденты: {incident_note}")

    return validation


def render_station_validation(validation: dict) -> None:
    blocking_messages = validation["blocking_messages"]
    warning_messages = validation["warning_messages"]
    if blocking_messages:
        st.error("Перед отправкой запроса найдено несоответствие по периоду работы станции.")
        for message in blocking_messages:
            st.markdown(f"- {message}")
    elif warning_messages:
        st.warning("По станции есть исторические оговорки. Запрос можно выполнять, но результат стоит трактовать аккуратно.")
    for message in warning_messages:
        st.markdown(f"- {message}")


def handle_refresh_actions(
    refresh_clicked: bool,
    validation: dict,
    service: AisoriDataService,
    station_code: str,
    selected_datasets: list[str],
    monthly_years: tuple[int, int],
    daily_dates,
) -> None:
    request_signature = _build_request_signature(station_code, selected_datasets, monthly_years, daily_dates)
    blocking_messages = validation["blocking_messages"]
    pending_signature = st.session_state.get("pending_force_refresh_signature")

    if refresh_clicked:
        if blocking_messages:
            st.session_state["pending_force_refresh_signature"] = request_signature
            st.session_state["pending_force_refresh_messages"] = list(blocking_messages)
        else:
            _run_refresh(
                service=service,
                station_code=station_code,
                selected_datasets=selected_datasets,
                monthly_years=monthly_years,
                daily_dates=daily_dates,
            )
            _clear_pending_force_refresh()

    if pending_signature == request_signature and blocking_messages:
        st.info("Если хотите, можно все равно выполнить запрос вручную, несмотря на предупреждение по истории станции.")
        if st.button("Все равно отправить запрос", width="stretch"):
            _run_refresh(
                service=service,
                station_code=station_code,
                selected_datasets=selected_datasets,
                monthly_years=monthly_years,
                daily_dates=daily_dates,
            )
            _clear_pending_force_refresh()
            st.rerun()


def _requested_year_bounds(
    monthly_years: tuple[int, int],
    daily_dates,
    selected_datasets: list[str],
) -> tuple[int | None, int | None]:
    year_candidates: list[int] = []
    for dataset_key in selected_datasets:
        template = DATASET_TEMPLATES[dataset_key]
        if template.frequency == "monthly":
            year_candidates.extend([monthly_years[0], monthly_years[1]])
        elif isinstance(daily_dates, tuple) and len(daily_dates) == 2:
            year_candidates.extend([daily_dates[0].year, daily_dates[1].year])
    if not year_candidates:
        return None, None
    return min(year_candidates), max(year_candidates)


def _optional_int(value) -> int | None:
    if value is None or pd.isna(value):
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _build_request_signature(
    station_code: str,
    selected_datasets: list[str],
    monthly_years: tuple[int, int],
    daily_dates,
) -> tuple:
    daily_signature = ()
    if isinstance(daily_dates, tuple) and len(daily_dates) == 2:
        daily_signature = (daily_dates[0].isoformat(), daily_dates[1].isoformat())
    return (
        station_code,
        tuple(sorted(selected_datasets)),
        monthly_years,
        daily_signature,
    )


def _clear_pending_force_refresh() -> None:
    st.session_state.pop("pending_force_refresh_signature", None)
    st.session_state.pop("pending_force_refresh_messages", None)


def apply_map_size_mode(stations_frame: pd.DataFrame, mode: str) -> pd.DataFrame:
    frame = stations_frame.copy()
    if mode == "По высоте станции":
        frame["marker_size"] = frame["elevation_m"].abs().clip(lower=1).pow(0.35) * 2.4
    elif mode == "Одинаковый":
        frame["marker_size"] = 7.0
    else:
        frame["marker_size"] = frame["observation_years"].clip(lower=1).pow(0.55) * 2.6
    return frame


def _run_refresh(
    service: AisoriDataService,
    station_code: str,
    selected_datasets: list[str],
    monthly_years: tuple[int, int],
    daily_dates,
) -> None:
    if not station_code:
        st.error("Укажите индекс станции.")
        return
    if not selected_datasets:
        st.warning("Выберите хотя бы один набор данных.")
        return

    if isinstance(daily_dates, tuple) and len(daily_dates) == 2:
        day_from_value, day_to_value = daily_dates
    else:
        st.error("Для суточных данных нужен диапазон из двух дат.")
        return

    with st.spinner("Тяну данные из AISORI-M и обновляю локальное хранилище..."):
        results = []
        failures: list[tuple[str, str]] = []
        for dataset_key in selected_datasets:
            template = DATASET_TEMPLATES[dataset_key]
            try:
                if template.frequency == "monthly":
                    result = service.refresh_dataset(
                        dataset_key=dataset_key,
                        station_query=station_code,
                        year_from=monthly_years[0],
                        year_to=monthly_years[1],
                    )
                else:
                    result = service.refresh_dataset(
                        dataset_key=dataset_key,
                        station_query=station_code,
                        year_from=day_from_value.year,
                        year_to=day_to_value.year,
                        month_from=day_from_value.month,
                        month_to=day_to_value.month,
                        day_from=day_from_value.day,
                        day_to=day_to_value.day,
                    )
                results.append(result)
            except Exception as exc:
                failures.append((template.title, str(exc)))

    for result in results:
        st.success(
            f"{result.dataset_title}: {result.record_count} записей, станция {result.station_name}, архив {result.zip_path}"
        )
    for dataset_title, message in failures:
        st.warning(f"{dataset_title}: {message}")


def render_station_catalog(
    filtered_stations: pd.DataFrame,
    stations_frame: pd.DataFrame,
    station_search: str,
    map_size_mode: str,
):
    st.markdown("### Справочник станций")
    if stations_frame.empty:
        st.error("Справочник станций пуст.")
        return None

    if filtered_stations.empty:
        st.info("По запросу станции не найдены.")
        st.plotly_chart(
            _build_station_map(stations_frame.head(0), stations_frame, None, map_size_mode),
            width="stretch",
        )
        return None

    selected_label = st.selectbox(
        "Выберите станцию из найденных",
        options=filtered_stations["label"].tolist(),
        index=0,
    )
    selected_station = filtered_stations[filtered_stations["label"] == selected_label].iloc[0]

    info_columns = st.columns(4)
    info_columns[0].metric("Индекс ВМО", selected_station["wmo_index"])
    info_columns[1].metric("Высота, м", int(selected_station["elevation_m"]))
    info_columns[2].metric("Период работы", selected_station["operation_period"])
    info_columns[3].metric("Статус", selected_station["status_label"])

    history_items = [
        ("Переименование", selected_station.get("rename_note", "")),
        ("Перенос", selected_station.get("transfer_note", "")),
        ("Закрытие/консервация", selected_station.get("closure_note", "")),
        ("Инциденты", selected_station.get("incident_note", "")),
    ]
    shown_history = [(label, value) for label, value in history_items if value]
    if shown_history:
        st.markdown("**Что есть в примечаниях по этой станции**")
        for label, value in shown_history:
            st.markdown(f"- **{label}:** {value}")

    if selected_station["note"]:
        st.markdown("**Полное примечание по станции**")
        st.info(selected_station["note"])

    st.plotly_chart(
        _build_station_map(filtered_stations, stations_frame, selected_station["wmo_index"], map_size_mode),
        width="stretch",
    )

    st.markdown(f"**Найденные станции:** {len(filtered_stations)}")
    shown = filtered_stations[
        [
            "wmo_index",
            "station_name",
            "status_label",
            "operation_period",
            "rename_note",
            "transfer_note",
            "closure_note",
            "incident_note",
            "latitude",
            "longitude",
            "elevation_m",
            "start_year",
            "end_year",
            "note",
        ]
    ].copy()
    st.dataframe(shown, width="stretch", hide_index=True, height=460)

    return selected_station


def _render_summary(observations: pd.DataFrame) -> None:
    latest = (
        observations.sort_values("observation_date")
        .groupby("dataset_key", as_index=False)
        .tail(1)
        .sort_values("dataset_key")
    )
    station_name = observations["station_name"].iloc[-1]
    st.markdown(f"### Станция: `{observations['station_code'].iloc[-1]}` {station_name}")
    columns = st.columns(max(1, len(latest)))
    for column, (_, row) in zip(columns, latest.iterrows(), strict=False):
        with column:
            st.markdown(
                f"""
                <div class="metric-card">
                    <div class="metric-title">{row['dataset_title']}</div>
                    <div class="metric-value">{row['value']:.1f} {row['unit']}</div>
                    <div class="metric-meta">{row['observation_date']}</div>
                </div>
                """,
                unsafe_allow_html=True,
            )


def _render_charts(observations: pd.DataFrame) -> None:
    monthly_tab, daily_tab = st.tabs(["Месячные ряды", "Суточные ряды"])

    with monthly_tab:
        monthly = observations[observations["dataset_key"].str.startswith("monthly_")].copy()
        if monthly.empty:
            st.info("Месячных данных пока нет.")
        else:
            for dataset_key in [
                "monthly_temperature",
                "monthly_humidity",
                "monthly_precipitation",
                "monthly_vapor_pressure",
                "monthly_vapor_pressure_deficit",
            ]:
                dataset_frame = monthly[monthly["dataset_key"] == dataset_key].copy()
                if dataset_frame.empty:
                    continue
                template = DATASET_TEMPLATES[dataset_key]
                left, right = st.columns([2, 1])
                with left:
                    st.plotly_chart(_build_line_chart(dataset_frame, template.title, template.chart_color), width="stretch")
                with right:
                    st.plotly_chart(_build_heatmap(dataset_frame, template.title, template.chart_color), width="stretch")

    with daily_tab:
        daily = observations[observations["dataset_key"].str.startswith("daily_")].copy()
        if daily.empty:
            st.info("Суточных данных пока нет.")
        else:
            for dataset_key in [
                "daily_temperature",
                "daily_humidity",
                "daily_vapor_pressure_deficit",
                "daily_precipitation",
            ]:
                dataset_frame = daily[daily["dataset_key"] == dataset_key].copy()
                if dataset_frame.empty:
                    continue
                template = DATASET_TEMPLATES[dataset_key]
                if dataset_key == "daily_precipitation":
                    st.plotly_chart(_build_bar_chart(dataset_frame, template.title, template.chart_color), width="stretch")
                else:
                    st.plotly_chart(_build_line_chart(dataset_frame, template.title, template.chart_color), width="stretch")


def filter_stations(stations_frame: pd.DataFrame, query: str, active_only: bool) -> pd.DataFrame:
    frame = stations_frame.copy()
    if active_only:
        frame = frame[frame["is_likely_active"]].copy()
    if not query:
        return frame.sort_values(["row_number", "wmo_index"]).reset_index(drop=True)
    normalized = query.strip().casefold()
    exact_code_mask = frame["wmo_index"] == normalized
    search_blob = (
        frame["wmo_index"].fillna("")
        + " "
        + frame["station_name"].fillna("")
        + " "
        + frame["status_label"].fillna("")
        + " "
        + frame["operation_period"].fillna("")
        + " "
        + frame["rename_note"].fillna("")
        + " "
        + frame["transfer_note"].fillna("")
        + " "
        + frame["closure_note"].fillna("")
        + " "
        + frame["incident_note"].fillna("")
        + " "
        + frame["note"].fillna("")
    ).str.casefold()
    matches = frame[search_blob.str.contains(normalized, regex=False)].copy()
    if exact_code_mask.any():
        exact_matches = frame[exact_code_mask].copy()
        remaining = matches[matches["wmo_index"] != normalized].copy()
        return pd.concat([exact_matches, remaining], ignore_index=True)
    return matches.sort_values(["row_number", "wmo_index"]).reset_index(drop=True)


@st.cache_data(show_spinner=False)
def load_transition_matrix_summaries(paths: tuple[str, ...]):
    return analyze_transition_workbooks(list(paths))


def render_transition_matrix_placeholder() -> None:
    st.markdown("### Матрицы переходных кривых")
    st.caption("Аналитическая заготовка по вашим XLS-файлам. Здесь мы пока не считаем прогнозы из наших рядов автоматически, а аккуратно разбираем структуру готовых матриц и исходных исторических данных.")

    matrix_paths_text = st.text_area(
        "Файлы матриц",
        value="\n".join(DEFAULT_MATRIX_FILE_PATHS),
        height=120,
        help="Можно перечислять по одному пути на строку.",
    )
    matrix_paths = tuple(path.strip() for path in matrix_paths_text.splitlines() if path.strip())
    workbook_summaries = load_transition_matrix_summaries(matrix_paths)

    if not workbook_summaries:
        st.warning("Не удалось найти ни одного файла матриц по указанным путям.")
        return

    overview_rows = []
    for workbook in workbook_summaries:
        metrics = sorted({sheet.metric_label for sheet in workbook.sheet_summaries if sheet.metric_label})
        year_from = min((sheet.year_from for sheet in workbook.sheet_summaries if sheet.year_from is not None), default=None)
        year_to = max((sheet.year_to for sheet in workbook.sheet_summaries if sheet.year_to is not None), default=None)
        overview_rows.append(
            {
                "file_name": workbook.file_name,
                "sheet_count": workbook.sheet_count,
                "inferred_purpose": workbook.inferred_purpose,
                "metrics_found": ", ".join(metrics),
                "year_span": f"{year_from} - {year_to}" if year_from and year_to else "",
            }
        )
    st.dataframe(pd.DataFrame(overview_rows), width="stretch", hide_index=True)

    workbook_labels = [f"{item.file_name} — {item.inferred_purpose}" for item in workbook_summaries]
    selected_workbook_label = st.selectbox("Выберите файл матриц", workbook_labels, index=0)
    selected_workbook = workbook_summaries[workbook_labels.index(selected_workbook_label)]

    sheet_labels = [
        f"{sheet.sheet_name} — {sheet.metric_label} — {sheet.inferred_kind}"
        for sheet in selected_workbook.sheet_summaries
    ]
    selected_sheet_label = st.selectbox("Выберите лист", sheet_labels, index=0)
    selected_sheet = selected_workbook.sheet_summaries[sheet_labels.index(selected_sheet_label)]

    info_columns = st.columns(4)
    info_columns[0].metric("Метрика", selected_sheet.metric_label)
    info_columns[1].metric("Тип листа", selected_sheet.inferred_kind)
    info_columns[2].metric("Лет в ряду", selected_sheet.year_count)
    info_columns[3].metric("Диапазон лет", _format_year_span(selected_sheet.year_from, selected_sheet.year_to))

    if selected_sheet.class_labels:
        st.markdown(f"**Классы состояний:** {', '.join(selected_sheet.class_labels)}")
    if selected_sheet.decade_blocks:
        st.markdown("**Обнаруженные декадные блоки**")
        for block in selected_sheet.decade_blocks[:12]:
            st.markdown(f"- {block}")

    inferred_notes = build_matrix_inference_notes(selected_workbook.file_name, selected_sheet)
    st.markdown("**Что, вероятно, означают эти данные**")
    for note in inferred_notes:
        st.markdown(f"- {note}")

    count_frame = matrix_to_frame(selected_sheet.matrix_row_labels, selected_sheet.class_labels, selected_sheet.count_matrix)
    probability_frame = matrix_to_frame(selected_sheet.matrix_row_labels, selected_sheet.class_labels, selected_sheet.probability_matrix)
    chart_left, chart_right = st.columns(2)
    with chart_left:
        if not count_frame.empty:
            st.plotly_chart(_build_transition_heatmap(count_frame, f"{selected_sheet.metric_label}: матрица переходов"), width="stretch")
        else:
            st.info("На этом листе не удалось автоматически выделить первую матрицу переходов.")
    with chart_right:
        if not probability_frame.empty:
            st.plotly_chart(_build_transition_heatmap(probability_frame, f"{selected_sheet.metric_label}: вероятности переходов"), width="stretch")
        else:
            st.info("Нормированные вероятности на выбранном листе не обнаружены.")

    st.markdown("**Предпросмотр листа**")
    preview_frame = pd.DataFrame(selected_sheet.preview_rows)
    st.dataframe(preview_frame, width="stretch", hide_index=True)


def build_matrix_inference_notes(workbook_name: str, sheet_summary) -> list[str]:
    notes = [
        "По историческим декадным значениям показатель разбивается на интервалы-классы, и для каждой пары соседних состояний считается число переходов.",
        "Если рядом с матрицей есть строки с долями, это уже не просто счетчики, а нормированные вероятности перехода из одного класса в другой.",
        "Листы с годами и декадами выглядят как исходный ряд, из которого затем строятся матрицы для первой, второй и третьей декад по месяцам с апреля по сентябрь.",
    ]
    lowered_name = workbook_name.casefold()
    if "готов" in lowered_name:
        notes.append("Файл выглядит как готовый набор декадных матриц по температуре: слева общая матрица, ниже и правее блоки по отдельным декадам.")
    if "jcf" in lowered_name:
        notes.append("Файлы с `jcf` похожи на комбинированные рабочие книги: в них рядом живут исходные ряды, переходные матрицы и сводные вероятности.")
    if "дефиц" in sheet_summary.metric_label.casefold():
        notes.append("Дефицит влажности воздуха здесь уже выступает как самостоятельный предиктор, а не просто производная от температуры и осадков.")
    if "радиац" in sheet_summary.metric_label.casefold():
        notes.append("Для радиационного показателя исторический ряд начинается позднее остальных, поэтому матрица может считаться на меньшей выборке.")
    return notes


def _format_year_span(year_from: int | None, year_to: int | None) -> str:
    if year_from is None or year_to is None:
        return "нет ряда"
    return f"{year_from} - {year_to}"


def _render_tables(observations: pd.DataFrame, refresh_log: pd.DataFrame) -> None:
    data_tab, refresh_tab = st.tabs(["Таблица наблюдений", "Журнал обновлений"])
    with data_tab:
        shown = observations.sort_values(["dataset_key", "observation_date"], ascending=[True, False]).copy()
        st.dataframe(
            shown[
                [
                    "dataset_title",
                    "station_code",
                    "station_name",
                    "observation_date",
                    "value",
                    "unit",
                    "downloaded_at",
                ]
            ],
            width="stretch",
            hide_index=True,
        )
    with refresh_tab:
        if refresh_log.empty:
            st.info("Журнал обновлений пока пуст.")
        else:
            st.dataframe(refresh_log, width="stretch", hide_index=True)


def _build_line_chart(frame: pd.DataFrame, title: str, color: str) -> go.Figure:
    frame = frame.sort_values("observation_date")
    figure = go.Figure()
    figure.add_trace(
        go.Scatter(
            x=frame["observation_date"],
            y=frame["value"],
            mode="lines+markers",
            line={"color": color, "width": 3},
            marker={"size": 6, "color": color},
            fill="tozeroy",
            fillcolor=_hex_to_rgba(color, 0.12),
            hovertemplate="%{x}<br>%{y:.2f}<extra></extra>",
        )
    )
    figure.update_layout(
        title=title,
        paper_bgcolor="rgba(0,0,0,0)",
        plot_bgcolor="rgba(255,255,255,0.7)",
        margin={"l": 10, "r": 10, "t": 50, "b": 10},
        xaxis_title="Дата",
        yaxis_title=frame["unit"].iloc[0],
        height=360,
    )
    return figure


def _build_heatmap(frame: pd.DataFrame, title: str, color: str) -> go.Figure:
    frame = frame.copy()
    pivot = frame.pivot_table(index="year", columns="month", values="value", aggfunc="mean").sort_index()
    figure = go.Figure(
        data=[
            go.Heatmap(
                z=pivot.values,
                x=[str(column) for column in pivot.columns],
                y=[str(index) for index in pivot.index],
                colorscale=[
                    [0.0, _hex_to_rgba(color, 0.15)],
                    [1.0, color],
                ],
                hovertemplate="Год %{y}<br>Месяц %{x}<br>%{z:.2f}<extra></extra>",
            )
        ]
    )
    figure.update_layout(
        title=f"{title}: карта по месяцам",
        paper_bgcolor="rgba(0,0,0,0)",
        margin={"l": 10, "r": 10, "t": 50, "b": 10},
        height=360,
    )
    return figure


def _build_bar_chart(frame: pd.DataFrame, title: str, color: str) -> go.Figure:
    frame = frame.sort_values("observation_date")
    figure = go.Figure(
        data=[
            go.Bar(
                x=frame["observation_date"],
                y=frame["value"],
                marker_color=color,
                hovertemplate="%{x}<br>%{y:.2f}<extra></extra>",
            )
        ]
    )
    figure.update_layout(
        title=title,
        paper_bgcolor="rgba(0,0,0,0)",
        plot_bgcolor="rgba(255,255,255,0.7)",
        margin={"l": 10, "r": 10, "t": 50, "b": 10},
        xaxis_title="Дата",
        yaxis_title=frame["unit"].iloc[0],
        height=420,
    )
    return figure


def _build_transition_heatmap(frame: pd.DataFrame, title: str) -> go.Figure:
    figure = go.Figure(
        data=[
            go.Heatmap(
                z=frame.values,
                x=[str(column) for column in frame.columns],
                y=[str(index) for index in frame.index],
                colorscale="YlOrBr",
                hovertemplate="Из %{y}<br>В %{x}<br>%{z:.3f}<extra></extra>",
            )
        ]
    )
    figure.update_layout(
        title=title,
        paper_bgcolor="rgba(0,0,0,0)",
        plot_bgcolor="rgba(255,255,255,0.7)",
        margin={"l": 10, "r": 10, "t": 50, "b": 10},
        height=420,
        xaxis_title="Следующее состояние",
        yaxis_title="Текущее состояние",
    )
    return figure


def _build_station_map(
    filtered_frame: pd.DataFrame,
    all_stations_frame: pd.DataFrame,
    selected_station_code: str | None,
    map_size_mode: str,
) -> go.Figure:
    background = all_stations_frame.copy()
    focus = filtered_frame.copy()

    background_trace = go.Scattergeo(
        lon=background["longitude"],
        lat=background["latitude"],
        mode="markers",
        marker={
            "size": background["marker_size"],
            "color": "rgba(83, 109, 142, 0.35)",
            "line": {"width": 0},
        },
        customdata=background[["wmo_index", "station_name", "start_year", "elevation_m"]].values,
        hovertemplate="Индекс %{customdata[0]}<br>%{customdata[1]}<br>Старт %{customdata[2]}<br>Высота %{customdata[3]} м<extra></extra>",
        name="Все станции",
    )

    show_text = len(focus) <= 25
    focus_trace = go.Scattergeo(
        lon=focus["longitude"],
        lat=focus["latitude"],
        mode="markers+text" if show_text else "markers",
        text=focus["wmo_index"] if show_text else None,
        textposition="top center",
        marker={
            "size": focus["marker_size"] + 5,
            "color": "#d95f02",
            "line": {"width": 1.2, "color": "#7a3412"},
        },
        customdata=focus[["wmo_index", "station_name", "start_year", "elevation_m"]].values,
        hovertemplate="Индекс %{customdata[0]}<br>%{customdata[1]}<br>Старт %{customdata[2]}<br>Высота %{customdata[3]} м<extra></extra>",
        name="Найденные станции",
    )

    figure = go.Figure([background_trace, focus_trace])

    if selected_station_code:
        selected = all_stations_frame[all_stations_frame["wmo_index"] == selected_station_code]
        if not selected.empty:
            figure.add_trace(
                go.Scattergeo(
                    lon=selected["longitude"],
                    lat=selected["latitude"],
                    mode="markers+text",
                    text=selected["label"],
                    textposition="bottom right",
                    marker={
                        "size": selected["marker_size"] + 12,
                        "color": "#2a9d8f",
                        "line": {"width": 2, "color": "#0f4c5c"},
                    },
                    hoverinfo="skip",
                    name="Выбранная станция",
                )
            )

    figure.update_geos(
        scope="world",
        projection_type="natural earth",
        showcountries=True,
        countrycolor="rgba(53,80,112,0.28)",
        showland=True,
        landcolor="rgb(242, 237, 222)",
        showocean=True,
        oceancolor="rgb(225, 238, 247)",
        showlakes=True,
        lakecolor="rgb(225, 238, 247)",
        lataxis_range=[40, 82],
        lonaxis_range=[15, 190],
    )
    figure.update_layout(
        title=f"Карта станций по каталогу ВМО • размер: {map_size_mode.lower()}",
        height=620,
        margin={"l": 0, "r": 0, "t": 50, "b": 0},
        paper_bgcolor="rgba(0,0,0,0)",
        legend={"orientation": "h", "y": 1.02, "x": 0},
    )
    return figure


def _inject_styles() -> None:
    st.markdown(
        """
        <style>
        @import url('https://fonts.googleapis.com/css2?family=Space+Grotesk:wght@400;500;700&family=IBM+Plex+Sans:wght@400;500;600&display=swap');
        html, body, [class*="css"]  {
            font-family: "IBM Plex Sans", sans-serif;
        }
        .stApp {
            background:
                radial-gradient(circle at top left, rgba(249, 211, 66, 0.18), transparent 30%),
                radial-gradient(circle at top right, rgba(12, 124, 186, 0.16), transparent 28%),
                linear-gradient(180deg, #f7f3e8 0%, #eef3f8 100%);
        }
        .hero {
            border: 1px solid rgba(12,124,186,0.12);
            border-radius: 24px;
            padding: 28px 30px;
            background: linear-gradient(135deg, rgba(255,255,255,0.92), rgba(239,246,255,0.82));
            box-shadow: 0 24px 80px rgba(31,55,86,0.10);
            margin-bottom: 20px;
        }
        .hero h1 {
            font-family: "Space Grotesk", sans-serif;
            font-size: 2.2rem;
            margin: 0;
            color: #16324f;
        }
        .hero p, .eyebrow {
            color: #355070;
        }
        .eyebrow {
            text-transform: uppercase;
            letter-spacing: 0.12em;
            font-size: 0.78rem;
            margin-bottom: 8px;
        }
        .metric-card {
            border-radius: 18px;
            padding: 18px;
            background: rgba(255,255,255,0.84);
            border: 1px solid rgba(22,50,79,0.08);
            box-shadow: 0 16px 50px rgba(31,55,86,0.08);
            min-height: 118px;
        }
        .metric-title {
            color: #355070;
            font-size: 0.92rem;
            margin-bottom: 10px;
        }
        .metric-value {
            font-family: "Space Grotesk", sans-serif;
            font-size: 1.8rem;
            color: #16324f;
            font-weight: 700;
        }
        .metric-meta {
            color: #5a6f84;
            margin-top: 8px;
        }
        </style>
        """,
        unsafe_allow_html=True,
    )


def _hex_to_rgba(hex_color: str, alpha: float) -> str:
    stripped = hex_color.lstrip("#")
    red = int(stripped[0:2], 16)
    green = int(stripped[2:4], 16)
    blue = int(stripped[4:6], 16)
    return f"rgba({red}, {green}, {blue}, {alpha})"


if __name__ == "__main__":
    main()
