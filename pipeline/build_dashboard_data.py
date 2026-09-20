from __future__ import annotations

import argparse
from pathlib import Path

import numpy as np
import pandas as pd


PROJECT_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_SOURCE = PROJECT_ROOT / "source" / "data_cic_sample.csv"
DEFAULT_OUTPUT = PROJECT_ROOT / "data" / "dashboard" / "dashboard_activity.parquet"

REQUIRED_COLUMNS = {
    "NO_CONTAINER",
    "BOOKNO",
    "NO_HISTORY",
    "CTGLSTATUS",
    "CURRPORT",
    "CURRCY",
    "CURRSTATE",
    "CTSTAMP",
    "PREV_STATE",
    "PREV_TGLSTATUS",
    "CONT_TYPE",
    "CONTGRADE",
    "CARGOALIAS",
    "VVOY_",
}

ACTIVITY_MAP: dict[tuple[str, str], tuple[str, str, str, bool]] = {
    ("FTL", "FOB"): ("MUAT FULL", "Muat", "Full", False),
    ("MTL", "MOB"): ("MUAT MT", "Muat", "Empty", False),
    ("FIT", "FOB"): ("MUAT FULL TRANSHIPMENT", "Muat", "Full", True),
    ("MIT", "MOB"): ("MUAT MT TRANSHIPMENT", "Muat", "Empty", True),
    ("FOB", "FXD"): ("BONGKAR FULL", "Bongkar", "Full", False),
    ("MOB", "MXD"): ("BONGKAR MT", "Bongkar", "Empty", False),
    ("FOB", "FIT"): ("BONGKAR FULL TRANSHIPMENT", "Bongkar", "Full", True),
    ("MOB", "MIT"): ("BONGKAR MT TRANSHIPMENT", "Bongkar", "Empty", True),
    ("MTA", "MTB"): ("STUFFING DALAM", "Stuffing", "Empty", False),
    ("MTA", "MAS"): ("STUFFING LUAR", "Stuffing", "Empty", False),
    ("FXD", "STR"): ("STRIPPING DALAM", "Stripping", "Full", False),
    ("FXD", "FAC"): ("STRIPPING LUAR", "Stripping", "Full", False),
    ("FAC", "FTL"): ("ROUNDTRIP", "Roundtrip", "Full", False),
    ("FAC", "MTA"): ("SELESAI STRIPPING LUAR", "Completion", "Empty", False),
    ("STR", "MTA"): ("SELESAI STRIPPING DALAM", "Completion", "Empty", False),
    ("MAS", "FTL"): ("SELESAI STUFFING LUAR", "Completion", "Full", False),
    ("MTB", "FTL"): ("SELESAI STUFFING DALAM", "Completion", "Full", False),
    ("MTA", "MTL"): ("READY MUAT MT", "Preparation", "Empty", False),
    ("MXD", "MTA"): ("EMPTY AVAILABLE SETELAH BONGKAR MT", "Completion", "Empty", False),
    ("FXD", "FXD"): ("RELOKASI FULL DISCHARGED", "Relocation", "Full", False),
    ("FTL", "FTL"): ("RELOKASI FULL TO LOAD", "Relocation", "Full", False),
    ("MTA", "MTA"): ("RELOKASI EMPTY AVAILABLE", "Relocation", "Empty", False),
}


def _clean_text(series: pd.Series, default: str = "UNKNOWN") -> pd.Series:
    cleaned = series.astype("string").str.strip()
    return cleaned.replace({"": pd.NA, "-": pd.NA}).fillna(default)


def _clean_vessel_voyage(series: pd.Series) -> pd.Series:
    """Keep only the voyage code, e.g. TBI-13/2026 from TBI-13/2026 MKS-BMS."""
    return _clean_text(series).str.extract(r"^(\S+)", expand=False).fillna("UNKNOWN")


def _activity_attributes(prev_state: pd.Series, curr_state: pd.Series) -> pd.DataFrame:
    records = []
    for previous, current in zip(prev_state, curr_state):
        name, group, condition, transhipment = ACTIVITY_MAP.get(
            (previous, current),
            ("UNMAPPED", "Unmapped", "Unknown", False),
        )
        records.append((name, group, condition, transhipment))
    return pd.DataFrame(
        records,
        columns=["activity_name", "activity_group", "load_condition", "is_transshipment"],
    )


def build_dashboard_frame(
    raw: pd.DataFrame,
    source_name: str = "UNKNOWN",
    ingested_at: pd.Timestamp | None = None,
) -> pd.DataFrame:
    missing = sorted(REQUIRED_COLUMNS.difference(raw.columns))
    if missing:
        raise ValueError(f"Missing required columns: {', '.join(missing)}")
    if raw["NO_HISTORY"].duplicated().any():
        raise ValueError("NO_HISTORY must be unique; duplicate event IDs were found")

    activity_date = pd.to_datetime(raw["CTGLSTATUS"], dayfirst=True, errors="coerce")
    previous_date = pd.to_datetime(raw["PREV_TGLSTATUS"], dayfirst=True, errors="coerce")
    updated_at = pd.to_datetime(raw["CTSTAMP"], dayfirst=True, errors="coerce")
    if activity_date.isna().any():
        raise ValueError("CTGLSTATUS contains values that cannot be parsed as dates")
    if updated_at.isna().any():
        raise ValueError("CTSTAMP contains values that cannot be parsed as timestamps")

    previous = _clean_text(raw["PREV_STATE"], default="UNKNOWN")
    current = _clean_text(raw["CURRSTATE"], default="UNKNOWN")
    attributes = _activity_attributes(previous, current)

    size_ft = pd.to_numeric(
        _clean_text(raw["CONT_TYPE"]).str.extract(r"^(\d+)", expand=False),
        errors="coerce",
    ).astype("Int16")
    teu = np.select([size_ft >= 40, size_ft.notna()], [2, 1], default=0).astype("int8")
    size_group = np.select([size_ft >= 40, size_ft.notna()], ["40", "20"], default="Other")

    grade_raw = _clean_text(raw["CONTGRADE"], default="Other").str.upper()
    grade = grade_raw.where(grade_raw.isin(["A", "B", "C"]), "Other")

    ingestion_time = ingested_at or pd.Timestamp.now().floor("s")
    result = pd.DataFrame(
        {
            "event_id": _clean_text(raw["NO_HISTORY"]),
            "container_no": _clean_text(raw["NO_CONTAINER"]),
            "booking_no": _clean_text(raw["BOOKNO"]),
            "activity_date": activity_date,
            "activity_month": activity_date.dt.strftime("%Y-%m"),
            "updated_at": updated_at,
            "ingested_at": ingestion_time,
            "source_file": source_name,
            "previous_date": previous_date,
            "port": _clean_text(raw["CURRPORT"]),
            "cy": _clean_text(raw["CURRCY"]),
            "vessel_voyage": _clean_vessel_voyage(raw["VVOY_"]),
            "prev_state": previous,
            "curr_state": current,
            "cargo": _clean_text(raw["CARGOALIAS"], default="UNKNOWN CARGO"),
            "container_type": _clean_text(raw["CONT_TYPE"]),
            "size_ft": size_ft,
            "size_group": size_group,
            "grade": grade,
            "quantity": np.ones(len(raw), dtype="int8"),
            "teu": teu,
        }
    )
    result = pd.concat([result, attributes], axis=1)
    result["movement_scope"] = np.where(result["is_transshipment"], "Transhipment", "Regular")
    result["is_vessel_arrival"] = result["activity_name"].isin(
        [
            "BONGKAR FULL",
            "BONGKAR MT",
            "BONGKAR FULL TRANSHIPMENT",
            "BONGKAR MT TRANSHIPMENT",
        ]
    )
    return result


def build_dashboard_data(source: Path = DEFAULT_SOURCE, output: Path = DEFAULT_OUTPUT) -> pd.DataFrame:
    if not source.exists():
        raise FileNotFoundError(f"Source CSV not found: {source}")
    raw = pd.read_csv(source, low_memory=False)
    dashboard = build_dashboard_frame(raw, source_name=source.name)
    output.parent.mkdir(parents=True, exist_ok=True)
    dashboard.to_parquet(output, index=False, compression="zstd")
    return dashboard


def main() -> None:
    parser = argparse.ArgumentParser(description="Build the dashboard-ready Parquet dataset")
    parser.add_argument("--source", type=Path, default=DEFAULT_SOURCE)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    args = parser.parse_args()

    dashboard = build_dashboard_data(args.source, args.output)
    print(f"Created {args.output.resolve()}")
    print(f"Rows: {len(dashboard):,}")
    print(f"Period: {dashboard['activity_date'].min():%d %b %Y} - {dashboard['activity_date'].max():%d %b %Y}")
    print(f"Size: {args.output.stat().st_size / 1024 / 1024:.2f} MB")


if __name__ == "__main__":
    main()
