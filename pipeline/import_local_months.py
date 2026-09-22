from __future__ import annotations

import argparse
import shutil
from pathlib import Path

import duckdb
import pandas as pd
import pyarrow as pa
import pyarrow.parquet as pq

from data.dashboard_repository import DEFAULT_DATASET, format_month
from data.dashboard_store import DEFAULT_MANAGED_DATASET, DuplicateMonthError
from data.duckdb_repository import DuckDBParquetRepository, dataset_files, dataset_glob
from data.parquet_dataset import (
    atomic_replace_dataset,
    clone_dataset,
    new_staging_path,
    partition_directory,
    physical_table,
    publish_dataset,
)
from pipeline.build_dashboard_data import build_dashboard_frame, prepare_event_identity


PROJECT_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_SOURCE_DIR = PROJECT_ROOT / "source" / "cic-project" / "output-data"
DEFAULT_SOURCES = [
    DEFAULT_SOURCE_DIR / "data-mei-notna.csv",
    DEFAULT_SOURCE_DIR / "data-juni-notna.csv",
    DEFAULT_SOURCE_DIR / "data-juli-notna.csv",
]

IDENTITY_COLUMNS = [
    "NO_HISTORY",
    "NO_CONTAINER",
    "CURRSTATE",
    "CTSTAMP",
    "CTGLSTATUS",
    "PREV_TGLSTATUS",
]


def _schema_text(schema: pa.Schema) -> str:
    return "\n".join(f"  - {field.name}: {field.type}" for field in schema)


def _validate_schema(actual: pa.Schema, expected: pa.Schema, source: Path) -> None:
    actual = actual.remove_metadata()
    expected = expected.remove_metadata()
    if actual.equals(expected):
        return
    raise ValueError(
        f"Schema {source.name} tidak sesuai dengan dataset existing.\n"
        f"Expected:\n{_schema_text(expected)}\n"
        f"Actual:\n{_schema_text(actual)}"
    )


def _validate_staged_dataset(dataset_path: Path) -> int:
    source = dataset_glob(dataset_path)
    connection = duckdb.connect()
    try:
        invalid_event = connection.execute(
            """
            SELECT event_id
            FROM read_parquet(?, hive_partitioning = true)
            WHERE event_id IS NULL OR TRIM(event_id) = '' OR event_id = 'UNKNOWN'
            LIMIT 1
            """,
            [source],
        ).fetchone()
        if invalid_event:
            raise ValueError("event_id tidak boleh null, kosong, atau UNKNOWN")

        duplicate = connection.execute(
            """
            SELECT event_id, COUNT(*) AS frequency
            FROM read_parquet(?, hive_partitioning = true)
            GROUP BY event_id
            HAVING COUNT(*) > 1
            LIMIT 1
            """,
            [source],
        ).fetchone()
        if duplicate:
            raise ValueError(
                f"Duplicate event_id ditemukan: {duplicate[0]} "
                f"({duplicate[1]:,} kemunculan)"
            )

        return int(
            connection.execute(
                "SELECT COUNT(*) FROM read_parquet(?, hive_partitioning = true)",
                [source],
            ).fetchone()[0]
        )
    finally:
        connection.close()


def _selected_source_rows(source: Path) -> tuple[set[int] | None, int]:
    """Return source row positions to keep when synthetic event IDs need deduplication."""
    identity = pd.read_csv(source, usecols=IDENTITY_COLUMNS, low_memory=False)
    history = identity["NO_HISTORY"].astype("string").str.strip()
    missing = history.isna() | history.eq("")
    if not missing.any():
        return None, 0

    selected = prepare_event_identity(identity)
    removed = len(identity) - len(selected)
    return set(selected.index), removed


def import_local_months(
    sources: list[Path],
    managed_output: Path = DEFAULT_MANAGED_DATASET,
    dashboard_output: Path = DEFAULT_DATASET,
    chunk_size: int = 100_000,
    append: bool = True,
) -> dict[str, object]:
    missing = [path for path in sources if not path.is_file()]
    if missing:
        raise FileNotFoundError(
            "File sumber tidak ditemukan: " + ", ".join(str(path) for path in missing)
        )
    if not sources:
        raise ValueError("Berikan minimal satu CSV sumber")
    if chunk_size <= 0:
        raise ValueError("chunk_size harus lebih besar dari nol")

    existing = DuckDBParquetRepository(managed_output)
    existing_months = set(existing.months()) if append else set()
    existing_files = dataset_files(managed_output) if append else []
    expected_schema = (
        pq.read_schema(existing_files[0]).remove_metadata()
        if existing_files
        else None
    )

    staged_output = new_staging_path(managed_output)
    imported_months: dict[str, str] = {}
    added_rows = 0
    ingestion_time = pd.Timestamp.now().floor("s")

    try:
        if append:
            clone_dataset(managed_output, staged_output)
        else:
            staged_output.mkdir(parents=True, exist_ok=False)

        for source in sources:
            print(f"Memproses {source.name} ...", flush=True)
            selected_rows, deduplicated_rows = _selected_source_rows(source)
            if selected_rows is not None:
                print(
                    "  NO_HISTORY kosong: memakai ID sintetis dari "
                    "NO_CONTAINER + CURRSTATE + CTSTAMP + CTGLSTATUS",
                    flush=True,
                )
                if deduplicated_rows:
                    print(f"  deduplikasi: {deduplicated_rows:,} baris dibuang", flush=True)
            source_month: str | None = None
            source_rows = 0
            source_offset = 0
            writer: pq.ParquetWriter | None = None
            try:
                for chunk_number, raw in enumerate(
                    pd.read_csv(source, chunksize=chunk_size, low_memory=False),
                    start=1,
                ):
                    if "NO_HISTORY" not in raw:
                        raise ValueError(f"{source.name} tidak memiliki kolom NO_HISTORY")

                    chunk_start = source_offset
                    source_offset += len(raw)
                    if selected_rows is not None:
                        positions = range(chunk_start, source_offset)
                        keep_mask = [position in selected_rows for position in positions]
                        raw = raw.loc[keep_mask]
                        if raw.empty:
                            continue

                    raw = raw.reset_index(drop=True)
                    prepared = build_dashboard_frame(
                        raw,
                        source_name=source.name,
                        ingested_at=ingestion_time,
                    )
                    months = prepared["activity_month"].dropna().unique().tolist()
                    if len(months) != 1:
                        labels = ", ".join(format_month(month) for month in sorted(months)) or "tidak terdeteksi"
                        raise ValueError(
                            f"{source.name} harus berisi satu bulan; terdeteksi: {labels}"
                        )
                    chunk_month = months[0]
                    if source_month is None:
                        source_month = chunk_month
                        if source_month in existing_months:
                            raise DuplicateMonthError(
                                f"Data {format_month(source_month)} sudah ada; "
                                f"{source.name} tidak dapat ditambahkan."
                            )
                        if source_month in imported_months:
                            raise DuplicateMonthError(
                                f"{source.name} dan {imported_months[source_month]} "
                                f"sama-sama berisi {format_month(source_month)}."
                            )
                        destination = partition_directory(staged_output, source_month) / "part-00000.parquet"
                        destination.parent.mkdir(parents=True, exist_ok=False)
                    elif chunk_month != source_month:
                        raise ValueError(
                            f"{source.name} berisi lebih dari satu bulan: "
                            f"{format_month(source_month)} dan {format_month(chunk_month)}"
                        )

                    table = physical_table(prepared)
                    if expected_schema is None:
                        expected_schema = table.schema.remove_metadata()
                    _validate_schema(table.schema, expected_schema, source)
                    if writer is None:
                        writer = pq.ParquetWriter(destination, table.schema, compression="zstd")
                    writer.write_table(table)

                    rows = len(prepared)
                    source_rows += rows
                    added_rows += rows
                    print(
                        f"  chunk {chunk_number}: {rows:,} baris "
                        f"(total file {source_rows:,})",
                        flush=True,
                    )
                    del raw, prepared, table
            finally:
                if writer is not None:
                    writer.close()

            if source_month is None:
                raise ValueError(f"{source.name} tidak berisi data")
            imported_months[source_month] = source.name
            print(
                f"Selesai {source.name}: {format_month(source_month)}, "
                f"{source_rows:,} event",
                flush=True,
            )

        total_events = _validate_staged_dataset(staged_output)
        atomic_replace_dataset(staged_output, managed_output)
        publish_dataset(managed_output, dashboard_output)

        return {
            "added_events": added_rows,
            "total_events": total_events,
            "months": sorted(imported_months),
            "mode": "append" if append else "replace",
            "managed_output": managed_output,
            "dashboard_output": dashboard_output,
        }
    finally:
        shutil.rmtree(staged_output, ignore_errors=True)


def main() -> None:
    parser = argparse.ArgumentParser(
        description=(
            "Impor CSV lokal ke dataset Parquet berpartisi bulanan. Default append; "
            "bulan existing dan duplicate event_id ditolak."
        )
    )
    parser.add_argument(
        "sources",
        nargs="*",
        type=Path,
        default=DEFAULT_SOURCES,
        help="CSV bulanan. Default: data Mei, Juni, dan Juli di output-data.",
    )
    parser.add_argument("--chunk-size", type=int, default=100_000)
    parser.add_argument("--managed-output", type=Path, default=DEFAULT_MANAGED_DATASET)
    parser.add_argument("--dashboard-output", type=Path, default=DEFAULT_DATASET)
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--append", action="store_true", help="Tambahkan bulan baru (default).")
    mode.add_argument("--replace", action="store_true", help="Rebuild dari file yang diberikan.")
    args = parser.parse_args()

    try:
        result = import_local_months(
            sources=args.sources,
            managed_output=args.managed_output,
            dashboard_output=args.dashboard_output,
            chunk_size=args.chunk_size,
            append=not args.replace,
        )
    except (DuplicateMonthError, FileNotFoundError, ValueError) as error:
        parser.exit(1, f"Impor gagal: {error}\n")

    labels = ", ".join(format_month(month) for month in result["months"])
    print("\nImpor lokal selesai.")
    print(f"Mode       : {result['mode']}")
    print(f"Bulan baru : {labels}")
    print(f"Event baru : {result['added_events']:,}")
    print(f"Total event: {result['total_events']:,}")
    print(f"Managed    : {result['managed_output']}")
    print(f"Dashboard  : {result['dashboard_output']}")


if __name__ == "__main__":
    main()
