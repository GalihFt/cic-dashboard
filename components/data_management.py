from __future__ import annotations

import pandas as pd
from nicegui import events, run, ui

from components.dashboard import build_sidebar
from data.dashboard_repository import DashboardRepository
from data.dashboard_store import DashboardDataStore, DuplicateMonthError


class DataManagementPage:
    def __init__(self, store: DashboardDataStore, repository: DashboardRepository) -> None:
        self.store = store
        self.repository = repository

    def build(self) -> None:
        build_sidebar("database")
        with ui.element("main").classes("dashboard-main database-main"):
            self._header()
            self._workflow()
            self._upload_section()
            self._stored_data_section()

    @staticmethod
    def _header() -> None:
        with ui.element("header").classes("database-header"):
            with ui.column().classes("gap-0"):
                ui.label("Database Management").classes("page-title")
                ui.label("Tambahkan dan kelola data bulanan yang digunakan dashboard").classes("page-subtitle")
            ui.link("Kembali ke Dashboard", target="/").classes("back-dashboard-link")

    @staticmethod
    def _workflow() -> None:
        with ui.element("section").classes("workflow-strip"):
            for number, title, description in [
                ("1", "Unggah data", "Pilih satu file untuk satu bulan"),
                ("2", "Diproses otomatis", "Data divalidasi dan dihitung ke TEU"),
                ("3", "Refresh dashboard", "Publikasikan perubahan ke dashboard"),
            ]:
                with ui.row().classes("workflow-step"):
                    ui.label(number).classes("workflow-number")
                    with ui.column().classes("gap-0"):
                        ui.label(title).classes("workflow-title")
                        ui.label(description).classes("workflow-description")

    def _upload_section(self) -> None:
        with ui.element("section").classes("database-panel upload-panel"):
            with ui.column().classes("upload-copy gap-1"):
                ui.label("Tambah data bulanan").classes("database-section-title")
                ui.label(
                    "Gunakan CSV atau Excel. File harus berisi satu bulan dan bulan tersebut belum tersimpan."
                ).classes("database-section-copy")
            self.upload = ui.upload(
                label="Pilih file CSV atau Excel",
                auto_upload=True,
                max_file_size=30_000_000,
                on_upload=self._handle_upload,
                on_rejected=lambda: ui.notify("File ditolak. Maksimum ukuran file 30 MB.", type="warning"),
            ).props('accept=.csv,.xlsx flat bordered data-testid="monthly-upload"').classes("monthly-upload")
            with ui.row().classes("upload-note"):
                ui.icon("info", size="17px")
                ui.label("Raw file tidak disimpan. Data hasil proses dipublikasikan setelah Refresh Data ditekan.")

    def _stored_data_section(self) -> None:
        with ui.element("section").classes("database-panel stored-panel"):
            with ui.row().classes("stored-heading"):
                with ui.column().classes("gap-0"):
                    ui.label("Data yang tersimpan").classes("database-section-title")
                    ui.label("Satu bulan hanya boleh tersimpan satu kali").classes("database-section-copy")
                with ui.row().classes("stored-controls"):
                    with ui.column().classes("refresh-meta gap-0"):
                        ui.label("Terakhir refresh").classes("month-stat-label")
                        self.last_refresh_label = ui.label().classes("last-refresh-value")
                    self.total_months = ui.label().classes("month-count")
                    self.refresh_data_button = (
                        ui.button("Refresh Data", icon="sync", on_click=self._publish_dashboard)
                        .props('unelevated no-caps data-testid="publish-dashboard"')
                        .classes("refresh-data-button")
                    )
            self.month_list = ui.column().classes("month-list")
        self._refresh_month_list()

    async def _handle_upload(self, event: events.UploadEventArguments) -> None:
        filename = event.file.name
        try:
            content = await event.file.read()
            result = await run.io_bound(self.store.import_bytes, filename, content)
            self._refresh_month_list()
            ui.notify(
                f"{result['month_label']} ditambahkan. Klik Refresh Data untuk memperbarui dashboard.",
                type="positive",
                timeout=7000,
            )
        except DuplicateMonthError as error:
            ui.notify(str(error), type="warning", timeout=7000)
        except Exception as error:
            ui.notify(f"Data tidak dapat diproses: {error}", type="negative", timeout=8000)
        finally:
            self.upload.reset()

    def _refresh_month_list(self) -> None:
        summaries = self.store.monthly_summary()
        self.total_months.set_text(f"{len(summaries)} bulan")
        refreshed_at = self.store.last_refreshed
        self.last_refresh_label.set_text(
            refreshed_at.strftime("%d %b %Y %H:%M") if pd.notna(refreshed_at) else "Belum pernah"
        )
        self.month_list.clear()
        with self.month_list:
            if not summaries:
                with ui.column().classes("database-empty"):
                    ui.icon("inventory_2", size="34px")
                    ui.label("Belum ada data pengelolaan").classes("database-empty-title")
                    ui.label("Unggah file bulanan pertama melalui area di atas.")
                return
            for summary in summaries:
                with ui.element("article").classes("month-row"):
                    with ui.column().classes("month-primary gap-0"):
                        ui.label(summary["month_label"]).classes("month-name")
                        ui.label(summary["source_file"]).classes("month-source")
                    self._month_stat("Event", f"{summary['events']:,}")
                    self._month_stat("Container", f"{summary['containers']:,}")
                    self._month_stat("Port", summary["ports"] or "-")
                    self._month_stat("Ditambahkan", summary["ingested_at"])
                    self._month_stat(
                        "Dashboard",
                        summary["refresh_status"],
                        "published" if summary["is_published"] else "pending",
                    )
                    with ui.row().classes("month-actions"):
                        ui.button(
                            "Lihat",
                            icon="visibility",
                            on_click=lambda month=summary["month"], label=summary["month_label"]: self._show_preview(month, label),
                        ).props("flat dense no-caps").classes("view-data-button")
                        ui.button(
                            "Hapus",
                            icon="delete_outline",
                            on_click=lambda month=summary["month"], label=summary["month_label"]: self._confirm_delete(month, label),
                        ).props("flat dense no-caps").classes("delete-data-button")

    @staticmethod
    def _month_stat(label: str, value: str, state: str = "") -> None:
        with ui.column().classes("month-stat gap-0"):
            ui.label(label).classes("month-stat-label")
            ui.label(value).classes(f"month-stat-value {state}".strip())

    async def _publish_dashboard(self) -> None:
        self.refresh_data_button.disable()
        try:
            result = await run.io_bound(self.store.publish)
            self.repository.reload()
            self._refresh_month_list()
            ui.notify(
                f"Dashboard diperbarui: {result['months']} bulan, {result['events']:,} event.",
                type="positive",
                timeout=6000,
            )
        except Exception as error:
            ui.notify(f"Dashboard tidak dapat diperbarui: {error}", type="negative", timeout=8000)
        finally:
            self.refresh_data_button.enable()

    def _show_preview(self, month: str, month_label: str) -> None:
        preview = self.store.preview_month(month)
        columns = [
            {"name": "date", "label": "Tanggal", "field": "date", "align": "left"},
            {"name": "container", "label": "Container", "field": "container", "align": "left"},
            {"name": "transition", "label": "Status", "field": "transition", "align": "left"},
            {"name": "activity", "label": "Aktivitas", "field": "activity", "align": "left"},
            {"name": "port", "label": "Port", "field": "port", "align": "left"},
            {"name": "voyage", "label": "Vessel Voyage", "field": "voyage", "align": "left"},
            {"name": "type", "label": "Type", "field": "type", "align": "left"},
            {"name": "teu", "label": "TEU", "field": "teu", "align": "right"},
        ]
        rows = [
            {
                "id": row.event_id,
                "date": pd.Timestamp(row.activity_date).strftime("%d/%m/%Y"),
                "container": row.container_no,
                "transition": f"{row.prev_state} → {row.curr_state}",
                "activity": row.activity_name,
                "port": row.port,
                "voyage": row.vessel_voyage,
                "type": row.container_type,
                "teu": int(row.teu),
            }
            for row in preview.itertuples()
        ]
        with ui.dialog() as dialog, ui.card().classes("preview-dialog-card"):
            with ui.row().classes("dialog-heading"):
                with ui.column().classes("gap-0"):
                    ui.label(f"Preview {month_label}").classes("database-section-title")
                    ui.label("Menampilkan maksimal 50 event hasil proses").classes("database-section-copy")
                ui.button(icon="close", on_click=dialog.close).props('flat round aria-label="Tutup"')
            ui.table(columns=columns, rows=rows, row_key="id", pagination=10).classes("preview-table")
        dialog.open()

    def _confirm_delete(self, month: str, month_label: str) -> None:
        with ui.dialog() as dialog, ui.card().classes("delete-dialog-card"):
            ui.label(f"Hapus data {month_label}?").classes("database-section-title")
            ui.label(
                "Data bulan ini akan dihapus dari pengelolaan. Klik Refresh Data setelahnya untuk memperbarui dashboard."
            ).classes("database-section-copy")
            with ui.row().classes("dialog-actions"):
                ui.button("Batal", on_click=dialog.close).props("flat no-caps")
                ui.button(
                    "Hapus data",
                    on_click=lambda: self._delete_month(month, month_label, dialog),
                ).props("unelevated no-caps color=negative")
        dialog.open()

    def _delete_month(self, month: str, month_label: str, dialog) -> None:
        try:
            deleted = self.store.delete_month(month)
            self._refresh_month_list()
            dialog.close()
            ui.notify(
                f"{month_label} dihapus ({deleted:,} event). Klik Refresh Data untuk memperbarui dashboard.",
                type="positive",
                timeout=7000,
            )
        except Exception as error:
            ui.notify(f"Data tidak dapat dihapus: {error}", type="negative")
