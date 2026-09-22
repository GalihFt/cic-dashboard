from __future__ import annotations

import shutil
import threading
from io import BytesIO
from pathlib import Path

import duckdb
import pandas as pd
import pyarrow.parquet as pq

from data.dashboard_repository import DEFAULT_DATASET, format_month
from data.duckdb_repository import DuckDBParquetRepository, dataset_files, dataset_glob
from data.parquet_dataset import (
    atomic_replace_dataset,
    clone_dataset,
    new_staging_path,
    partition_directory,
    physical_table,
    publish_dataset,
)
from pipeline.build_dashboard_data import build_dashboard_frame


DEFAULT_MANAGED_DATASET = DEFAULT_DATASET.with_name("managed_activity")


class DuplicateMonthError(ValueError):
    pass


class DashboardDataStore:
    """Own safe monthly mutations for the partitioned managed dataset."""

    def __init__(self, dataset_path: Path = DEFAULT_MANAGED_DATASET, dashboard_path: Path | None = None) -> None:
        self.dataset_path = dataset_path
        self.dashboard_path = dashboard_path or (
            DEFAULT_DATASET
            if dataset_path == DEFAULT_MANAGED_DATASET
            else dataset_path.with_name(f"published_{dataset_path.name}")
        )
        self._lock = threading.Lock()
        if not DuckDBParquetRepository(self.dataset_path).exists and DuckDBParquetRepository(self.dashboard_path).exists:
            publish_dataset(self.dashboard_path, self.dataset_path)

    @staticmethod
    def _read_upload(filename: str, content: bytes) -> pd.DataFrame:
        extension = Path(filename).suffix.lower()
        if extension == ".csv":
            return pd.read_csv(BytesIO(content), low_memory=False)
        if extension == ".xlsx":
            return pd.read_excel(BytesIO(content))
        raise ValueError("Format file tidak didukung. Gunakan CSV atau Excel (.xlsx).")

    @staticmethod
    def _validate_staged(dataset_path: Path) -> int:
        connection = duckdb.connect()
        try:
            source = dataset_glob(dataset_path)
            invalid = connection.execute(
                """
                SELECT event_id
                FROM read_parquet(?, hive_partitioning = true)
                WHERE event_id IS NULL OR TRIM(event_id) = '' OR event_id = 'UNKNOWN'
                LIMIT 1
                """,
                [source],
            ).fetchone()
            if invalid:
                raise ValueError("event_id tidak boleh null, kosong, atau UNKNOWN")
            duplicate = connection.execute(
                """
                SELECT event_id, COUNT(*)
                FROM read_parquet(?, hive_partitioning = true)
                GROUP BY event_id
                HAVING COUNT(*) > 1
                LIMIT 1
                """,
                [source],
            ).fetchone()
            if duplicate:
                raise ValueError(f"Event {duplicate[0]} sudah tersimpan atau muncul lebih dari sekali.")
            return int(
                connection.execute(
                    "SELECT COUNT(*) FROM read_parquet(?, hive_partitioning = true)",
                    [source],
                ).fetchone()[0]
            )
        finally:
            connection.close()

    def import_bytes(self, filename: str, content: bytes) -> dict[str, object]:
        raw = self._read_upload(filename, content)
        prepared = build_dashboard_frame(raw.reset_index(drop=True), source_name=filename)
        months = sorted(prepared["activity_month"].dropna().unique().tolist())
        if len(months) != 1:
            readable = ", ".join(format_month(month) for month in months) or "tidak terdeteksi"
            raise ValueError(f"Satu file harus berisi tepat satu bulan. Bulan terdeteksi: {readable}.")
        month = months[0]

        with self._lock:
            repository = DuckDBParquetRepository(self.dataset_path)
            if month in repository.months():
                raise DuplicateMonthError(
                    f"Data {format_month(month)} sudah ada. Hapus bulan tersebut terlebih dahulu atau batalkan upload."
                )

            staged = new_staging_path(self.dataset_path)
            try:
                clone_dataset(self.dataset_path, staged)
                table = physical_table(prepared)
                existing_files = dataset_files(self.dataset_path)
                if existing_files:
                    expected = pq.read_schema(existing_files[0]).remove_metadata()
                    if not table.schema.remove_metadata().equals(expected):
                        raise ValueError("Schema file tidak sesuai dengan dataset existing")
                destination = partition_directory(staged, month) / "part-00000.parquet"
                destination.parent.mkdir(parents=True, exist_ok=False)
                pq.write_table(table, destination, compression="zstd")
                self._validate_staged(staged)
                atomic_replace_dataset(staged, self.dataset_path)
            finally:
                shutil.rmtree(staged, ignore_errors=True)

        return {
            "month": month,
            "month_label": format_month(month),
            "events": len(prepared),
            "containers": prepared["container_no"].nunique(),
            "filename": filename,
        }

    def delete_month(self, month: str) -> int:
        with self._lock:
            repository = DuckDBParquetRepository(self.dataset_path)
            if month not in repository.months():
                raise ValueError(f"Data {format_month(month)} tidak ditemukan.")
            connection = duckdb.connect()
            try:
                deleted = int(
                    connection.execute(
                        """
                        SELECT COUNT(*)
                        FROM read_parquet(?, hive_partitioning = true)
                        WHERE activity_month = ?
                        """,
                        [repository.source_parameter, month],
                    ).fetchone()[0]
                )
            finally:
                connection.close()
            target = partition_directory(self.dataset_path, month)
            trash = new_staging_path(target, "deleted")
            target.rename(trash)
            shutil.rmtree(trash)
        return deleted

    def publish(self) -> dict[str, object]:
        with self._lock:
            managed = DuckDBParquetRepository(self.dataset_path)
            if not managed.exists:
                raise ValueError("Belum ada data yang dapat di-refresh ke dashboard.")
            publish_dataset(self.dataset_path, self.dashboard_path)
        return {
            "events": managed.row_count(),
            "months": len(managed.months()),
            "refreshed_at": self.last_refreshed,
        }

    @property
    def last_refreshed(self) -> pd.Timestamp:
        version = DuckDBParquetRepository(self.dashboard_path).version
        if not version:
            return pd.NaT
        return pd.Timestamp.fromtimestamp(max(item[1] for item in version) / 1_000_000_000)

    @staticmethod
    def _monthly_counts(dataset_path: Path) -> dict[str, int]:
        repository = DuckDBParquetRepository(dataset_path)
        if not repository.exists:
            return {}
        connection = duckdb.connect()
        try:
            rows = connection.execute(
                """
                SELECT activity_month, COUNT(*) AS events
                FROM read_parquet(?, hive_partitioning = true)
                GROUP BY activity_month
                """,
                [repository.source_parameter],
            ).fetchall()
            return {str(month): int(events) for month, events in rows}
        finally:
            connection.close()

    def monthly_summary(self) -> list[dict[str, object]]:
        repository = DuckDBParquetRepository(self.dataset_path)
        if not repository.exists:
            return []
        connection = duckdb.connect()
        try:
            rows = connection.execute(
                """
                SELECT
                    activity_month,
                    COUNT(*) AS events,
                    COUNT(DISTINCT container_no) AS containers,
                    STRING_AGG(DISTINCT port, ', ' ORDER BY port) AS ports,
                    STRING_AGG(DISTINCT source_file, ', ' ORDER BY source_file) AS source_file,
                    MAX(ingested_at) AS ingested_at
                FROM read_parquet(?, hive_partitioning = true)
                GROUP BY activity_month
                ORDER BY activity_month DESC
                """,
                [repository.source_parameter],
            ).fetchall()
        finally:
            connection.close()

        published_counts = self._monthly_counts(self.dashboard_path)
        summaries = []
        for month, events, containers, ports, source_file, ingested_at in rows:
            month = str(month)
            is_published = published_counts.get(month) == int(events)
            timestamp = pd.Timestamp(ingested_at) if ingested_at is not None else pd.NaT
            summaries.append(
                {
                    "month": month,
                    "month_label": format_month(month),
                    "events": int(events),
                    "containers": int(containers),
                    "ports": ports or "",
                    "source_file": source_file or "Data lama",
                    "ingested_at": timestamp.strftime("%d %b %Y %H:%M") if pd.notna(timestamp) else "-",
                    "refresh_status": "Sudah refresh" if is_published else "Belum refresh",
                    "is_published": is_published,
                }
            )
        return summaries

    def preview_month(self, month: str, limit: int = 50) -> pd.DataFrame:
        repository = DuckDBParquetRepository(self.dataset_path)
        if not repository.exists:
            return pd.DataFrame()
        connection = duckdb.connect()
        try:
            return connection.execute(
                """
                SELECT *
                FROM read_parquet(?, hive_partitioning = true)
                WHERE activity_month = ?
                ORDER BY activity_date, event_id
                LIMIT ?
                """,
                [repository.source_parameter, month, limit],
            ).df()
        finally:
            connection.close()
