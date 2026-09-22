from __future__ import annotations

import os
import shutil
import uuid
from pathlib import Path

import pandas as pd
import pyarrow as pa
import pyarrow.parquet as pq


PARTITION_COLUMN = "activity_month"


def partition_directory(dataset_path: Path, month: str) -> Path:
    return dataset_path / f"{PARTITION_COLUMN}={month}"


def physical_table(frame: pd.DataFrame) -> pa.Table:
    return pa.Table.from_pandas(
        frame.drop(columns=[PARTITION_COLUMN]),
        preserve_index=False,
    )


def write_partition_frame(
    frame: pd.DataFrame,
    dataset_path: Path,
    month: str,
    filename: str = "part-00000.parquet",
) -> Path:
    destination = partition_directory(dataset_path, month) / filename
    destination.parent.mkdir(parents=True, exist_ok=True)
    pq.write_table(physical_table(frame), destination, compression="zstd")
    return destination


def new_staging_path(destination: Path, label: str = "staging") -> Path:
    return destination.with_name(f".{destination.name}-{uuid.uuid4().hex}.{label}")


def atomic_replace_dataset(staged: Path, destination: Path) -> None:
    """Swap a prepared dataset directory into place with rollback on failure."""
    destination.parent.mkdir(parents=True, exist_ok=True)
    backup = new_staging_path(destination, "backup")
    had_destination = destination.exists()
    try:
        if had_destination:
            os.replace(destination, backup)
        os.replace(staged, destination)
    except Exception:
        if had_destination and backup.exists() and not destination.exists():
            os.replace(backup, destination)
        raise
    finally:
        if backup.exists():
            shutil.rmtree(backup, ignore_errors=True)


def clone_dataset(source: Path, destination: Path) -> None:
    if not source.is_dir():
        destination.mkdir(parents=True, exist_ok=False)
        return
    try:
        shutil.copytree(source, destination, copy_function=os.link)
    except OSError:
        shutil.rmtree(destination, ignore_errors=True)
        shutil.copytree(source, destination, copy_function=shutil.copy2)


def publish_dataset(source: Path, destination: Path) -> None:
    staged = new_staging_path(destination, "publish")
    try:
        # copy() intentionally gives published files a fresh mtime for Last Refreshed.
        shutil.copytree(source, staged, copy_function=shutil.copy)
        atomic_replace_dataset(staged, destination)
    finally:
        shutil.rmtree(staged, ignore_errors=True)
