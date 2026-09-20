from __future__ import annotations

from pathlib import Path

import pandas as pd


PROJECT_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_DATASET = PROJECT_ROOT / "data" / "dashboard" / "dashboard_activity.parquet"

MONTH_NAMES = {
    1: "Jan",
    2: "Feb",
    3: "Mar",
    4: "Apr",
    5: "Mei",
    6: "Jun",
    7: "Jul",
    8: "Agu",
    9: "Sep",
    10: "Okt",
    11: "Nov",
    12: "Des",
}

KPI_ACTIVITY = {
    "muat_full": "MUAT FULL",
    "muat_empty": "MUAT MT",
    "muat_full_trans": "MUAT FULL TRANSHIPMENT",
    "muat_empty_trans": "MUAT MT TRANSHIPMENT",
    "bongkar_full": "BONGKAR FULL",
    "bongkar_empty": "BONGKAR MT",
    "bongkar_full_trans": "BONGKAR FULL TRANSHIPMENT",
    "bongkar_empty_trans": "BONGKAR MT TRANSHIPMENT",
}


def format_month(period_key: str) -> str:
    year, month = period_key.split("-")
    return f"{MONTH_NAMES[int(month)]} {year}"


class DashboardRepository:
    def __init__(self, dataset_path: Path = DEFAULT_DATASET) -> None:
        self.dataset_path = dataset_path
        self.frame = pd.DataFrame()
        self.reload()

    def reload(self) -> None:
        if not self.dataset_path.exists():
            self.frame = pd.DataFrame(
                columns=[
                    "activity_date", "activity_month", "updated_at", "port", "activity_name",
                    "activity_group", "load_condition", "is_transshipment", "vessel_voyage",
                    "is_vessel_arrival", "curr_state", "grade", "size_group", "size_ft",
                    "cargo", "teu", "quantity",
                ]
            )
            return
        self.frame = pd.read_parquet(self.dataset_path)
        self.frame["activity_date"] = pd.to_datetime(self.frame["activity_date"])
        self.frame["updated_at"] = pd.to_datetime(self.frame["updated_at"])

    @property
    def ports(self) -> list[str]:
        return sorted(self.frame["port"].dropna().unique().tolist())

    @property
    def periods(self) -> list[str]:
        return sorted(self.frame["activity_month"].dropna().unique().tolist(), reverse=True)

    @property
    def last_updated(self) -> pd.Timestamp:
        if not self.dataset_path.exists():
            return pd.NaT
        return pd.Timestamp.fromtimestamp(self.dataset_path.stat().st_mtime)

    def filtered(self, port: str, start_period: str, end_period: str | None = None) -> pd.DataFrame:
        data = self.frame
        if port != "Semua":
            data = data[data["port"] == port]
        if start_period:
            end_period = end_period or start_period
            if start_period > end_period:
                raise ValueError("Periode mulai tidak boleh setelah periode selesai.")
            data = data[data["activity_month"].between(start_period, end_period)]
        return data

    @staticmethod
    def kpis(data: pd.DataFrame) -> dict[str, int]:
        values = {
            key: int(data.loc[data["activity_name"] == activity, "teu"].sum())
            for key, activity in KPI_ACTIVITY.items()
        }
        arrivals = data[data["is_vessel_arrival"] & data["vessel_voyage"].ne("UNKNOWN")]
        values["vessel_arrivals"] = int(arrivals[["port", "vessel_voyage"]].drop_duplicates().shape[0])
        return values

    def kpi_comparison(
        self,
        port: str,
        start_period: str,
        end_period: str | None = None,
    ) -> tuple[dict[str, int], dict[str, float]]:
        end_period = end_period or start_period
        current_values = self.kpis(self.filtered(port, start_period, end_period))
        if not start_period:
            return current_values, {key: 0.0 for key in current_values}
        start = pd.Period(start_period, freq="M")
        end = pd.Period(end_period, freq="M")
        period_count = end.ordinal - start.ordinal + 1
        previous_end = start - 1
        previous_start = previous_end - (period_count - 1)
        previous_data = self.filtered(port, str(previous_start), str(previous_end))
        previous_values = self.kpis(previous_data)

        changes: dict[str, float] = {}
        for key, current_value in current_values.items():
            previous_value = previous_values.get(key, 0)
            if previous_data.empty or previous_value == 0:
                changes[key] = 0.0
            else:
                changes[key] = ((current_value - previous_value) / previous_value) * 100
        return current_values, changes

    @staticmethod
    def trend(data: pd.DataFrame, mode: str) -> tuple[list[str], dict[str, list[int]]]:
        if mode not in {"Muat", "Bongkar"}:
            raise ValueError("Mode tren harus 'Muat' atau 'Bongkar'.")
        movement = data[data["activity_group"] == mode]
        categories = sorted(data["activity_month"].unique().tolist())

        regular = movement[~movement["is_transshipment"]]
        trans = movement[movement["is_transshipment"]]

        def values(frame: pd.DataFrame, condition: str | None = None) -> list[int]:
            selected = frame if condition is None else frame[frame["load_condition"] == condition]
            grouped = selected.groupby("activity_month")["teu"].sum()
            return [int(grouped.get(month, 0)) for month in categories]

        return [format_month(month) for month in categories], {
            "Full": values(regular, "Full"),
            "Empty": values(regular, "Empty"),
            "Transhipment": values(trans),
        }

    @staticmethod
    def cargo_ranking(data: pd.DataFrame, mode: str, limit: int = 5) -> tuple[list[str], list[int]]:
        activity = "MUAT FULL" if mode == "Muat" else "BONGKAR FULL"
        ranked = (
            data[data["activity_name"] == activity]
            .groupby("cargo", as_index=False)["teu"]
            .sum()
            .sort_values("teu", ascending=False)
            .head(limit)
            .sort_values("teu", ascending=True)
        )
        return ranked["cargo"].tolist(), ranked["teu"].astype(int).tolist()

    @staticmethod
    def composition(data: pd.DataFrame, view: str) -> tuple[list[str], dict[str, list[int]]]:
        categories = ["FXD", "MXD", "FOB", "MOB"]
        selected = data[data["curr_state"].isin(categories)]
        if view == "Grade":
            partition_column = "grade"
            partitions = ["A", "B", "C", "Other"]
        else:
            partition_column = "size_group"
            partitions = ["20", "40", "Other"]

        pivot = selected.pivot_table(
            index="curr_state",
            columns=partition_column,
            values="teu",
            aggfunc="sum",
            fill_value=0,
        )
        series = {
            partition: [
                int(pivot.loc[category, partition])
                if category in pivot.index and partition in pivot.columns
                else 0
                for category in categories
            ]
            for partition in partitions
        }
        return categories, series

    @staticmethod
    def stuffing_stripping(data: pd.DataFrame) -> dict[str, list[int]]:
        mapping = {
            "Stuffing": ["STUFFING DALAM", "STUFFING LUAR"],
            "Stripping": ["STRIPPING DALAM", "STRIPPING LUAR"],
        }
        return {
            group: [int(data.loc[data["activity_name"] == activity, "teu"].sum()) for activity in activities]
            for group, activities in mapping.items()
        }

    @staticmethod
    def vessel_detail(data: pd.DataFrame) -> list[dict[str, object]]:
        bucket_map = {
            "BONGKAR FULL": ("bongkar", "fl"),
            "BONGKAR MT": ("bongkar", "mt"),
            "MUAT FULL": ("muat", "fl"),
            "MUAT MT": ("muat", "mt"),
            "BONGKAR FULL TRANSHIPMENT": ("bongkar_trans", "fl"),
            "BONGKAR MT TRANSHIPMENT": ("bongkar_trans", "mt"),
            "MUAT FULL TRANSHIPMENT": ("muat_trans", "fl"),
            "MUAT MT TRANSHIPMENT": ("muat_trans", "mt"),
        }
        selected = data[data["activity_name"].isin(bucket_map)].copy()
        if selected.empty:
            return []
        selected = selected[selected["vessel_voyage"].ne("UNKNOWN")]
        selected["size_bucket"] = selected["size_ft"].map(lambda value: "40" if value >= 40 else "20")
        selected["metric"] = selected.apply(
            lambda row: f"{bucket_map[row['activity_name']][0]}_{row['size_bucket']}_{bucket_map[row['activity_name']][1]}",
            axis=1,
        )
        grouped = (
            selected.pivot_table(
                index=["port", "vessel_voyage", "activity_month"],
                columns="metric",
                values="quantity",
                aggfunc="sum",
                fill_value=0,
            )
            .reset_index()
        )
        metric_columns = [
            f"{group}_{size}_{condition}"
            for group in ["bongkar", "muat", "bongkar_trans", "muat_trans"]
            for size in ["20", "40"]
            for condition in ["fl", "mt"]
        ]
        for column in metric_columns:
            if column not in grouped:
                grouped[column] = 0
        grouped["total"] = grouped[metric_columns].sum(axis=1)
        grouped = grouped.sort_values(["activity_month", "port", "total"], ascending=[False, True, False])

        records: list[dict[str, object]] = []
        for row in grouped.to_dict("records"):
            record = {
                "port": row["port"],
                "vessel_voyage": row["vessel_voyage"],
                "td_month": format_month(row["activity_month"]),
            }
            record.update({column: int(row[column]) for column in metric_columns})
            records.append(record)
        return records
