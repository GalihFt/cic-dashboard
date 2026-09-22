from pathlib import Path

from nicegui import ui

from components.data_management import DataManagementPage
from components.dashboard import DashboardPage
from data.dashboard_repository import DEFAULT_DATASET, DashboardRepository
from data.dashboard_store import DashboardDataStore
from data.duckdb_repository import DuckDBParquetRepository
from pipeline.build_dashboard_data import build_dashboard_data


PROJECT_ROOT = Path(__file__).resolve().parent


def ensure_dataset() -> None:
    if not DuckDBParquetRepository(DEFAULT_DATASET).exists:
        build_dashboard_data()


ensure_dataset()
repository = DashboardRepository(keep_fact=False)
store = DashboardDataStore()
ui.add_css(
    (PROJECT_ROOT / "styles" / "dashboard.css").read_text(encoding="utf-8"),
    shared=True,
)


@ui.page("/")
def dashboard() -> None:
    repository.reload_if_changed()
    DashboardPage(repository).build()


@ui.page("/database")
def database_management() -> None:
    repository.reload_if_changed()
    DataManagementPage(store, repository).build()


if __name__ in {"__main__", "__mp_main__"}:
    ui.run(
        title="Container Inventory Control Dashboard",
        host="127.0.0.1",
        port=8080,
        reload=False,
        show=False,
    )
