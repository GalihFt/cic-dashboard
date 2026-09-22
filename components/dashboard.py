from __future__ import annotations

from html import escape

import pandas as pd
from nicegui import run, ui

from components.charts import cargo_option, composition_option, stuffing_option, trend_option
from data.dashboard_report import build_dashboard_report
from data.dashboard_repository import DashboardRepository, format_month


def _number(value: int) -> str:
    return f"{value:,}"


def _card(title: str, subtitle: str | None = None, extra_class: str = ""):
    card = ui.element("section").classes(f"dashboard-card {extra_class}".strip())
    with card:
        header = ui.row().classes("card-header")
        with header:
            with ui.column().classes("gap-0"):
                ui.label(title).classes("card-title")
                if subtitle:
                    ui.label(subtitle).classes("card-subtitle")
    return card, header


def build_sidebar(active: str) -> None:
    with ui.element("aside").classes("sidebar"):
        with ui.element("div").classes("sidebar-logo"):
            ui.icon("view_in_ar", size="28px")
        for key, icon, label, target in [
            ("dashboard", "dashboard", "Dashboard", "/"),
            ("database", "storage", "Database Management", "/database"),
        ]:
            with ui.link(target=target).props(f'aria-label="{label}"').classes(
                f"sidebar-button {'active' if key == active else ''}"
            ):
                ui.icon(icon, size="21px").classes("sidebar-menu-icon")
                ui.tooltip(label)


def _detail_table_html(rows: list[dict[str, object]]) -> str:
    groups = [
        ("bongkar", "BONGKAR"),
        ("muat", "MUAT"),
        ("bongkar_trans", "Bongkar Trans"),
        ("muat_trans", "Muat Trans"),
    ]
    metric_order = [("20", "fl"), ("40", "fl"), ("20", "mt"), ("40", "mt")]
    header_groups = "".join(f'<th colspan="4" class="group-{key}">{label}</th>' for key, label in groups)
    subheaders = "".join(
        f'<th class="group-{group}">{size} {condition.upper()}</th>'
        for group, _ in groups
        for size, condition in metric_order
    )
    if not rows:
        body = '<tr><td colspan="19" class="empty-table">Tidak ada data kapal untuk filter ini.</td></tr>'
    else:
        body_rows = []
        for row in rows:
            metrics = "".join(
                f'<td class="num">{int(row[f"{group}_{size}_{condition}"]):,}</td>'
                for group, _ in groups
                for size, condition in metric_order
            )
            body_rows.append(
                "<tr>"
                f'<td>{escape(str(row["port"]))}</td>'
                f'<td class="voyage">{escape(str(row["vessel_voyage"]))}</td>'
                f'<td>{escape(str(row["td_month"]))}</td>'
                f"{metrics}</tr>"
            )
        body = "".join(body_rows)
    return f"""
    <div class="detail-table-wrap">
      <table class="detail-table" data-testid="vessel-detail-table">
        <thead>
          <tr>
            <th rowspan="2" class="fixed-head">PORT ID</th>
            <th rowspan="2" class="fixed-head voyage">VESSEL VOYAGE</th>
            <th rowspan="2" class="fixed-head">TD MONTH</th>
            {header_groups}
          </tr>
          <tr>{subheaders}</tr>
        </thead>
        <tbody>{body}</tbody>
      </table>
    </div>
    """


class DashboardPage:
    def __init__(self, repository: DashboardRepository) -> None:
        self.repository = repository
        self.ports: list[str] = []
        latest_period = repository.periods[0] if repository.periods else ""
        self.start_period = latest_period
        self.end_period = latest_period
        self.trend_mode = "Muat"
        self.vessel_search = ""
        self.vessel_rows: list[dict[str, object]] = []
        self.kpi_labels: dict[str, ui.label] = {}
        self.kpi_comparison_labels: dict[str, ui.label] = {}

    def build(self) -> None:
        build_sidebar("dashboard")
        with ui.element("main").classes("dashboard-main"):
            self._header()
            self._kpis()
            self._row_two()
            self._row_three()
            self._detail_table()
        self.refresh()

    def _header(self) -> None:
        with ui.element("header").classes("dashboard-header"):
            with ui.row().classes("brand-block"):
                with ui.element("div").classes("brand-icon"):
                    ui.icon("directions_boat", size="35px")
                with ui.column().classes("gap-0"):
                    ui.label("Container Inventory Control Dashboard").classes("page-title")
                    ui.label("Monitoring Container Movements").classes("page-subtitle")
            with ui.row().classes("header-controls"):
                port_options = self.repository.ports
                self.port_select = (
                    ui.select(
                        port_options,
                        value=self.ports,
                        label="Port (semua jika kosong)",
                        multiple=True,
                        clearable=True,
                        on_change=self._global_filter_changed,
                    )
                    .props('outlined dense options-dense use-chips data-testid="port-filter"')
                    .classes("header-select port-multi-select")
                )
                period_options = {period: format_month(period) for period in self.repository.periods}
                self.start_period_select = (
                    ui.select(period_options, value=self.start_period, label="Mulai", on_change=self._start_period_changed)
                    .props('outlined dense options-dense data-testid="period-start-filter"')
                    .classes("header-select period-select")
                )
                self.end_period_select = (
                    ui.select(period_options, value=self.end_period, label="Sampai", on_change=self._end_period_changed)
                    .props('outlined dense options-dense data-testid="period-end-filter"')
                    .classes("header-select period-select")
                )
                self.download_button = (
                    ui.button(
                        "Download Report",
                        icon="download",
                        on_click=self._download_report,
                    )
                    .props('no-caps unelevated data-testid="download-report"')
                    .classes("download-report-button")
                )
                with ui.row().classes("updated-block"):
                    ui.icon("schedule", size="27px")
                    with ui.column().classes("gap-0"):
                        ui.label("Last Refreshed").classes("updated-label")
                        updated = self.repository.last_updated
                        updated_text = updated.strftime("%d %b %Y %H:%M") if not pd.isna(updated) else "Belum ada data"
                        self.updated_value = ui.label(updated_text).classes("updated-value")

    def _kpi_metric(self, label: str, key: str) -> None:
        with ui.column().classes("kpi-metric gap-0"):
            ui.label(label).classes("kpi-label")
            self.kpi_labels[key] = ui.label("0").classes("kpi-value")
            ui.label("TEU").classes("kpi-unit")
            self.kpi_comparison_labels[key] = ui.label("0.0% vs bulan lalu").classes(
                "kpi-comparison neutral"
            )

    def _movement_card(self, title: str, prefix: str, tint: str, icon: str) -> None:
        with ui.element("section").classes(f"dashboard-card kpi-card {tint}"):
            with ui.row().classes("kpi-card-title"):
                ui.icon(icon, size="20px")
                ui.label(title)
            with ui.row().classes("kpi-metrics"):
                self._kpi_metric("Full", f"{prefix}_full")
                self._kpi_metric("Empty", f"{prefix}_empty")
                self._kpi_metric("Full Transhipment", f"{prefix}_full_trans")
                self._kpi_metric("Empty Transhipment", f"{prefix}_empty_trans")

    def _kpis(self) -> None:
        with ui.element("section").classes("kpi-grid"):
            self._movement_card("Muat", "muat", "tint-blue", "north_east")
            self._movement_card("Bongkar", "bongkar", "tint-purple", "south_west")
            with ui.element("section").classes("dashboard-card vessel-card"):
                with ui.row().classes("vessel-title"):
                    ui.icon("directions_boat", size="27px")
                    ui.label("Kedatangan Vessel")
                self.kpi_labels["vessel_arrivals"] = ui.label("0").classes("vessel-value")
                ui.label("Vessel").classes("vessel-unit")

    def _row_two(self) -> None:
        with ui.element("section").classes("content-grid row-two"):
            card, header = _card("Tren Aktivitas Kontainer", "Total TEU per bulan")
            with header:
                self.trend_toggle = (
                    ui.toggle(["Muat", "Bongkar"], value=self.trend_mode, on_change=self._trend_changed)
                    .props('no-caps unelevated data-testid="trend-toggle"')
                    .classes("segment-toggle two-items")
                )
            with card:
                self.trend_chart = ui.echart({}).classes("chart chart-row-two")

            card, _ = _card("Top Cargo Muat", "Total TEU")
            with card:
                self.cargo_muat_chart = ui.echart({}).classes("chart chart-row-two")

            card, _ = _card("Top Cargo Bongkar", "Total TEU")
            with card:
                self.cargo_bongkar_chart = ui.echart({}).classes("chart chart-row-two")

    def _row_three(self) -> None:
        with ui.element("section").classes("content-grid row-three"):
            card, _ = _card("Komposisi Container - Grade", "Total TEU per status")
            with card:
                self.composition_grade_chart = ui.echart({}).classes("chart chart-row-three")

            card, _ = _card("Komposisi Container - Size", "Total TEU per status")
            with card:
                self.composition_size_chart = ui.echart({}).classes("chart chart-row-three")

            card, _ = _card("Aktivitas Stuffing & Stripping", "Total TEU")
            with card:
                self.stuffing_chart = ui.echart({}).classes("chart chart-row-three")

    def _detail_table(self) -> None:
        card, header = _card("Detail Per Kapal", "Quantity container per vessel voyage")
        card.classes("detail-card")
        with header:
            self.vessel_search_input = (
                ui.input(placeholder="Cari port atau vessel", on_change=self._vessel_search_changed)
                .props('dense outlined clearable debounce=250 data-testid="vessel-search"')
                .classes("detail-search")
            )
        with card:
            self.table = ui.html(_detail_table_html([]), sanitize=False).classes("w-full")

    def _global_filter_changed(self) -> None:
        self.ports = list(self.port_select.value or [])
        self.refresh()

    def _start_period_changed(self) -> None:
        self.start_period = self.start_period_select.value
        if self.end_period and self.start_period > self.end_period:
            self.end_period = self.start_period
            self.end_period_select.set_value(self.end_period)
        self.refresh()

    def _end_period_changed(self) -> None:
        self.end_period = self.end_period_select.value
        if self.start_period and self.end_period < self.start_period:
            self.start_period = self.end_period
            self.start_period_select.set_value(self.start_period)
        self.refresh()

    def _trend_changed(self) -> None:
        self.trend_mode = self.trend_toggle.value
        self._refresh_trend()

    def _vessel_search_changed(self) -> None:
        self.vessel_search = (self.vessel_search_input.value or "").strip().casefold()
        self._refresh_table()

    async def _download_report(self) -> None:
        self.download_button.disable()
        try:
            content = await run.io_bound(
                build_dashboard_report,
                self.repository,
                self.ports,
                self.start_period,
                self.end_period,
            )
            filename = f"dashboard_cic_{self.start_period}_{self.end_period}.xlsx"
            ui.download(
                content,
                filename=filename,
                media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            )
            ui.notify("Report dashboard berhasil dibuat.", type="positive")
        except Exception as error:
            ui.notify(f"Report tidak dapat dibuat: {error}", type="negative", timeout=8000)
        finally:
            self.download_button.enable()

    def _refresh_trend(self) -> None:
        categories, series = self.repository.summary_trend(
            self.ports, self.start_period, self.end_period, self.trend_mode
        )
        self._set_chart(self.trend_chart, trend_option(categories, series))

    def _refresh_cargo(self) -> None:
        muat_categories, muat_values = self.repository.summary_cargo_ranking(
            self.ports, self.start_period, self.end_period, "Muat"
        )
        bongkar_categories, bongkar_values = self.repository.summary_cargo_ranking(
            self.ports, self.start_period, self.end_period, "Bongkar"
        )
        self._set_chart(
            self.cargo_muat_chart,
            cargo_option(muat_categories, muat_values, "Muat"),
        )
        self._set_chart(
            self.cargo_bongkar_chart,
            cargo_option(bongkar_categories, bongkar_values, "Bongkar"),
        )

    def _refresh_composition(self) -> None:
        grade_categories, grade_series = self.repository.summary_composition(
            self.ports, self.start_period, self.end_period, "Grade"
        )
        size_categories, size_series = self.repository.summary_composition(
            self.ports, self.start_period, self.end_period, "Size"
        )
        self._set_chart(
            self.composition_grade_chart,
            composition_option(grade_categories, grade_series, "Grade"),
        )
        self._set_chart(
            self.composition_size_chart,
            composition_option(size_categories, size_series, "Size"),
        )

    def _refresh_table(self, rebuild: bool = False) -> None:
        if rebuild:
            self.vessel_rows = self.repository.summary_vessel_detail(
                self.ports, self.start_period, self.end_period
            )
        rows = self.vessel_rows
        if self.vessel_search:
            rows = [
                row
                for row in rows
                if self.vessel_search in str(row["port"]).casefold()
                or self.vessel_search in str(row["vessel_voyage"]).casefold()
                or self.vessel_search in str(row["td_month"]).casefold()
            ]
        self.table.set_content(_detail_table_html(rows))

    @staticmethod
    def _set_chart(chart, options: dict) -> None:
        chart.options.clear()
        chart.options.update(options)
        chart.update()

    def refresh(self) -> None:
        kpis, comparisons = self.repository.summary_kpi_comparison(
            self.ports, self.start_period, self.end_period
        )
        comparison_period = "bulan lalu" if self.start_period == self.end_period else "periode sebelumnya"
        for key, value in kpis.items():
            self.kpi_labels[key].set_text(_number(value))
            if key in self.kpi_comparison_labels:
                change = comparisons[key]
                if change > 0:
                    text = f"+{change:.1f}% vs {comparison_period}"
                    state = "positive"
                elif change < 0:
                    text = f"{change:.1f}% vs {comparison_period}"
                    state = "negative"
                else:
                    text = f"0.0% vs {comparison_period}"
                    state = "neutral"
                label = self.kpi_comparison_labels[key]
                label.set_text(text)
                label.classes(remove="positive negative neutral", add=state)
        self._refresh_trend()
        self._refresh_cargo()
        self._refresh_composition()
        self._set_chart(
            self.stuffing_chart,
            stuffing_option(
                self.repository.summary_stuffing_stripping(
                    self.ports, self.start_period, self.end_period
                )
            ),
        )
        self._refresh_table(rebuild=True)
