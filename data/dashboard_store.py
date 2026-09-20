from __future__ import annotations

import os
import threading
import uuid
from io import BytesIO
from pathlib import Path

import pandas as pd

from data.dashboard_repository import DEFAULT_DATASET, format_month
from pipeline.build_dashboard_data import build_dashboard_frame

DEFAULT_MANAGED_DATASET = DEFAULT_DATASET.with_name("managed_activity.parquet")


class DuplicateMonthError(ValueError):
    pass


class DashboardDataStore:
    """Owns safe monthly imports and deletes for the dashboard fact table."""

    def __init__(self, dataset_path: Path = DEFAULT_MANAGED_DATASET, dashboard_path: Path | None = None) -> None:
        self.dataset_path = dataset_path
        self.dashboard_path = dashboard_path or (
            DEFAULT_DATASET
            if dataset_path == DEFAULT_MANAGED_DATASET
            else dataset_path.with_name(f"published_{dataset_path.name}")
        )
        self._lock = threading.Lock()
        if not self.dataset_path.exists() and self.dashboard_path.exists():
            self._atomic_write(pd.read_parquet(self.dashboard_path), self.dataset_path)

    def _read(self) -> pd.DataFrame:
        if not self.dataset_path.exists():
            return pd.DataFrame()
        return pd.read_parquet(self.dataset_path)

    @staticmethod
    def _atomic_write(frame: pd.DataFrame, destination: Path) -> None:
        destination.parent.mkdir(parents=True, exist_ok=True)
        temporary = destination.with_name(f".{destination.stem}-{uuid.uuid4().hex}.tmp.parquet")
        try:
            frame.to_parquet(temporary, index=False, compression="zstd")
            os.replace(temporary, destination)
        finally:
            temporary.unlink(missing_ok=True)

    @staticmethod
    def _read_upload(filename: str, content: bytes) -> pd.DataFrame:
        extension = Path(filename).suffix.lower()
        if extension == ".csv":
            return pd.read_csv(BytesIO(content), low_memory=False)
        if extension == ".xlsx":
            return pd.read_excel(BytesIO(content))
        raise ValueError("Format file tidak didukung. Gunakan CSV atau Excel (.xlsx).")

    def import_bytes(self, filename: str, content: bytes) -> dict[str, object]:
        raw = self._read_upload(filename, content)
        prepared = build_dashboard_frame(raw, source_name=filename)
        months = sorted(prepared["activity_month"].dropna().unique().tolist())
        if len(months) != 1:
            readable = ", ".join(format_month(month) for month in months) or "tidak terdeteksi"
            raise ValueError(f"Satu file harus berisi tepat satu bulan. Bulan terdeteksi: {readable}.")
        month = months[0]

        with self._lock:
            existing = self._read()
            if not existing.empty and month in set(existing["activity_month"]):
                raise DuplicateMonthError(
                    f"Data {format_month(month)} sudah ada. Hapus bulan tersebut terlebih dahulu atau batalkan upload."
                )
            if not existing.empty:
                duplicate_events = set(prepared["event_id"]).intersection(existing["event_id"])
                if duplicate_events:
                    raise ValueError(f"Ditemukan {len(duplicate_events):,} event yang sudah tersimpan.")
                combined = pd.concat([existing, prepared], ignore_index=True)
            else:
                combined = prepared
            combined = combined.sort_values(["activity_date", "event_id"]).reset_index(drop=True)
            self._atomic_write(combined, self.dataset_path)

        return {
            "month": month,
            "month_label": format_month(month),
            "events": len(prepared),
            "containers": prepared["container_no"].nunique(),
            "filename": filename,
        }

    def delete_month(self, month: str) -> int:
        with self._lock:
            existing = self._read()
            if existing.empty or month not in set(existing["activity_month"]):
                raise ValueError(f"Data {format_month(month)} tidak ditemukan.")
            deleted = int((existing["activity_month"] == month).sum())
            remaining = existing[existing["activity_month"] != month].copy()
            self._atomic_write(remaining, self.dataset_path)
        return deleted

    def publish(self) -> dict[str, object]:
        with self._lock:
            managed = self._read()
            if managed.empty and not self.dataset_path.exists():
                raise ValueError("Belum ada data yang dapat di-refresh ke dashboard.")
            self._atomic_write(managed, self.dashboard_path)
        refreshed_at = pd.Timestamp.fromtimestamp(self.dashboard_path.stat().st_mtime)
        return {
            "events": len(managed),
            "months": managed["activity_month"].nunique() if not managed.empty else 0,
            "refreshed_at": refreshed_at,
        }

    @property
    def last_refreshed(self) -> pd.Timestamp:
        if not self.dashboard_path.exists():
            return pd.NaT
        return pd.Timestamp.fromtimestamp(self.dashboard_path.stat().st_mtime)

    def monthly_summary(self) -> list[dict[str, object]]:
        frame = self._read()
        if frame.empty:
            return []
        published = pd.read_parquet(self.dashboard_path) if self.dashboard_path.exists() else pd.DataFrame()
        frame["ingested_at"] = pd.to_datetime(frame.get("ingested_at"), errors="coerce")
        rows = []
        for month, group in frame.groupby("activity_month", sort=True):
            sources = sorted(group.get("source_file", pd.Series(dtype="string")).dropna().unique().tolist())
            ingested = group["ingested_at"].max()
            published_group = (
                published[published["activity_month"] == month]
                if not published.empty and "activity_month" in published
                else pd.DataFrame()
            )
            is_published = (
                len(group) == len(published_group)
                and set(group["event_id"]) == set(published_group.get("event_id", []))
            )
            rows.append(
                {
                    "month": month,
                    "month_label": format_month(month),
                    "events": len(group),
                    "containers": group["container_no"].nunique(),
                    "ports": ", ".join(sorted(group["port"].dropna().unique().tolist())),
                    "source_file": ", ".join(sources) if sources else "Data lama",
                    "ingested_at": ingested.strftime("%d %b %Y %H:%M") if pd.notna(ingested) else "-",
                    "refresh_status": "Sudah refresh" if is_published else "Belum refresh",
                    "is_published": is_published,
                }
            )
        return list(reversed(rows))

    def preview_month(self, month: str, limit: int = 50) -> pd.DataFrame:
        frame = self._read()
        return frame[frame["activity_month"] == month].head(limit).copy()
