from __future__ import annotations

from pathlib import Path

import duckdb
import pandas as pd


PARTITION_COLUMN = "activity_month"
DATA_FILE_PATTERN = f"{PARTITION_COLUMN}=*/*.parquet"


def dataset_glob(dataset_path: Path) -> str:
    if dataset_path.is_file():
        return str(dataset_path.resolve())
    return str((dataset_path / DATA_FILE_PATTERN).resolve())


def dataset_files(dataset_path: Path) -> list[Path]:
    if dataset_path.is_file():
        return [dataset_path]
    if not dataset_path.is_dir():
        return []
    return sorted(dataset_path.glob(DATA_FILE_PATTERN))


def dataset_exists(dataset_path: Path) -> bool:
    return bool(dataset_files(dataset_path))


def dataset_version(dataset_path: Path) -> tuple[tuple[str, int, int], ...]:
    if dataset_path.is_file():
        stat = dataset_path.stat()
        return ((dataset_path.name, stat.st_mtime_ns, stat.st_size),)
    return tuple(
        (str(path.relative_to(dataset_path)), path.stat().st_mtime_ns, path.stat().st_size)
        for path in dataset_files(dataset_path)
    )


class DuckDBParquetRepository:
    """Small query layer for a Hive-partitioned Parquet dataset."""

    def __init__(self, dataset_path: Path) -> None:
        self.dataset_path = dataset_path

    @property
    def exists(self) -> bool:
        return dataset_exists(self.dataset_path)

    @property
    def version(self) -> tuple[tuple[str, int, int], ...]:
        return dataset_version(self.dataset_path)

    @property
    def source_sql(self) -> str:
        return "read_parquet(?, hive_partitioning = true)"

    @property
    def source_parameter(self) -> str:
        return dataset_glob(self.dataset_path)

    def dataframe(self, sql: str, parameters: list[object] | None = None) -> pd.DataFrame:
        connection = duckdb.connect()
        try:
            return connection.execute(sql, parameters or []).df()
        finally:
            connection.close()

    def scalar(self, sql: str, parameters: list[object] | None = None) -> object:
        connection = duckdb.connect()
        try:
            row = connection.execute(sql, parameters or []).fetchone()
            return row[0] if row else None
        finally:
            connection.close()

    def months(self) -> list[str]:
        if not self.exists:
            return []
        rows = self.dataframe(
            f"""
            SELECT DISTINCT activity_month
            FROM {self.source_sql}
            ORDER BY activity_month
            """,
            [self.source_parameter],
        )
        return rows["activity_month"].astype(str).tolist()

    def row_count(self) -> int:
        if not self.exists:
            return 0
        return int(
            self.scalar(
                f"SELECT COUNT(*) FROM {self.source_sql}",
                [self.source_parameter],
            )
        )
