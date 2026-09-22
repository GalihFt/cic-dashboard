from __future__ import annotations

from pathlib import Path

import numpy as np
import pandas as pd

from data.duckdb_repository import DuckDBParquetRepository


PROJECT_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_DATASET = PROJECT_ROOT / "data" / "dashboard" / "dashboard_activity"

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
    """Query dashboard aggregates directly from partitioned Parquet with DuckDB."""

    def __init__(self, dataset_path: Path = DEFAULT_DATASET, keep_fact: bool = True) -> None:
        self.dataset_path = dataset_path
        self.keep_fact = keep_fact
        self.query = DuckDBParquetRepository(dataset_path)
        self.frame = pd.DataFrame()
        self._version: tuple[tuple[str, int, int], ...] = ()
        self._ports: list[str] = []
        self._periods: list[str] = []
        self.reload()

    def reload(self) -> None:
        self.query = DuckDBParquetRepository(self.dataset_path)
        self._version = self.query.version
        if not self.query.exists:
            self._ports = []
            self._periods = []
            self.frame = pd.DataFrame()
            return

        metadata = self.query.dataframe(
            f"""
            SELECT
                LIST(DISTINCT port ORDER BY port) AS ports,
                LIST(DISTINCT activity_month ORDER BY activity_month DESC) AS periods
            FROM {self.query.source_sql}
            """,
            [self.query.source_parameter],
        ).iloc[0]
        self._ports = list(metadata["ports"]) if metadata["ports"] is not None else []
        self._periods = (
            [str(period) for period in metadata["periods"]]
            if metadata["periods"] is not None
            else []
        )

        if self.keep_fact:
            self.frame = self.query.dataframe(
                f"SELECT * FROM {self.query.source_sql}",
                [self.query.source_parameter],
            )
            self.frame["activity_date"] = pd.to_datetime(self.frame["activity_date"])
            self.frame["updated_at"] = pd.to_datetime(self.frame["updated_at"])
        else:
            self.frame = pd.DataFrame()

    def reload_if_changed(self) -> bool:
        if self.query.version == self._version:
            return False
        self.reload()
        return True

    @property
    def ports(self) -> list[str]:
        return self._ports

    @property
    def periods(self) -> list[str]:
        return self._periods

    @property
    def last_updated(self) -> pd.Timestamp:
        files = self.query.version
        if not files:
            return pd.NaT
        return pd.Timestamp.fromtimestamp(max(item[1] for item in files) / 1_000_000_000)

    @staticmethod
    def _normalise_ports(port: str | list[str]) -> list[str]:
        if port == "Semua":
            return []
        return [port] if isinstance(port, str) else list(port)

    def _where(
        self,
        port: str | list[str],
        start_period: str,
        end_period: str | None,
    ) -> tuple[str, list[object]]:
        end_period = end_period or start_period
        clauses = ["activity_month BETWEEN ? AND ?"]
        parameters: list[object] = [start_period, end_period]
        ports = self._normalise_ports(port)
        if ports:
            clauses.append(f"port IN ({', '.join('?' for _ in ports)})")
            parameters.extend(ports)
        return " AND ".join(clauses), parameters

    def _dataframe(
        self,
        sql: str,
        parameters: list[object] | None = None,
    ) -> pd.DataFrame:
        if not self.query.exists:
            return pd.DataFrame()
        return self.query.dataframe(
            sql,
            [self.query.source_parameter, *(parameters or [])],
        )

    def filtered(self, port: str, start_period: str, end_period: str | None = None) -> pd.DataFrame:
        """Compatibility path used by tests and non-dashboard callers."""
        data = self.frame
        if port != "Semua":
            data = data[data["port"] == port]
        if start_period:
            end_period = end_period or start_period
            if start_period > end_period:
                raise ValueError("Periode mulai tidak boleh setelah periode selesai.")
            data = data[data["activity_month"].between(start_period, end_period)]
        return data

    def summary_kpis(
        self,
        port: str | list[str],
        start_period: str,
        end_period: str | None = None,
    ) -> dict[str, int]:
        where, parameters = self._where(port, start_period, end_period)
        totals = self._dataframe(
            f"""
            SELECT activity_name, CAST(SUM(teu) AS BIGINT) AS teu
            FROM {self.query.source_sql}
            WHERE {where}
            GROUP BY activity_name
            """,
            parameters,
        )
        totals_by_activity = dict(zip(totals.get("activity_name", []), totals.get("teu", [])))
        values = {
            key: int(totals_by_activity.get(activity, 0))
            for key, activity in KPI_ACTIVITY.items()
        }
        arrivals = self._dataframe(
            f"""
            SELECT COUNT(*) AS total
            FROM (
                SELECT DISTINCT port, vessel_voyage
                FROM {self.query.source_sql}
                WHERE {where}
                  AND is_vessel_arrival
                  AND vessel_voyage <> 'UNKNOWN'
            )
            """,
            parameters,
        )
        values["vessel_arrivals"] = int(arrivals.iloc[0]["total"]) if not arrivals.empty else 0
        return values

    def summary_kpi_comparison(
        self,
        port: str | list[str],
        start_period: str,
        end_period: str | None = None,
    ) -> tuple[dict[str, int], dict[str, float]]:
        end_period = end_period or start_period
        current = self.summary_kpis(port, start_period, end_period)
        start = pd.Period(start_period, freq="M")
        end = pd.Period(end_period, freq="M")
        period_count = end.ordinal - start.ordinal + 1
        previous_end = start - 1
        previous_start = previous_end - (period_count - 1)
        previous = self.summary_kpis(port, str(previous_start), str(previous_end))
        changes = {
            key: ((value - previous[key]) / previous[key]) * 100 if previous[key] else 0.0
            for key, value in current.items()
        }
        return current, changes

    def summary_trend(
        self,
        port: str | list[str],
        start_period: str,
        end_period: str,
        mode: str,
    ) -> tuple[list[str], dict[str, list[int]]]:
        where, parameters = self._where(port, start_period, end_period)
        rows = self._dataframe(
            f"""
            SELECT
                activity_month,
                is_transshipment,
                load_condition,
                CAST(SUM(teu) AS BIGINT) AS teu
            FROM {self.query.source_sql}
            WHERE {where} AND activity_group = ?
            GROUP BY activity_month, is_transshipment, load_condition
            """,
            [*parameters, mode],
        )
        periods = [str(period) for period in pd.period_range(start_period, end_period, freq="M")]

        def values(transshipment: bool, condition: str | None = None) -> list[int]:
            selected = rows[rows["is_transshipment"].eq(transshipment)]
            if condition is not None:
                selected = selected[selected["load_condition"].eq(condition)]
            totals = selected.groupby("activity_month")["teu"].sum()
            return [int(totals.get(period, 0)) for period in periods]

        return [format_month(period) for period in periods], {
            "Full": values(False, "Full"),
            "Empty": values(False, "Empty"),
            "Transhipment": values(True),
        }

    def summary_cargo_ranking(
        self,
        port: str | list[str],
        start_period: str,
        end_period: str,
        mode: str,
        limit: int = 5,
    ) -> tuple[list[str], list[int]]:
        where, parameters = self._where(port, start_period, end_period)
        activity = "MUAT FULL" if mode == "Muat" else "BONGKAR FULL"
        rows = self._dataframe(
            f"""
            SELECT cargo, CAST(SUM(teu) AS BIGINT) AS teu
            FROM {self.query.source_sql}
            WHERE {where} AND activity_name = ?
            GROUP BY cargo
            ORDER BY teu DESC
            LIMIT ?
            """,
            [*parameters, activity, limit],
        ).sort_values("teu")
        return rows.get("cargo", pd.Series(dtype="string")).tolist(), rows.get(
            "teu", pd.Series(dtype="int64")
        ).astype(int).tolist()

    def summary_composition(
        self,
        port: str | list[str],
        start_period: str,
        end_period: str,
        view: str,
    ) -> tuple[list[str], dict[str, list[int]]]:
        categories = ["FXD", "MXD", "FOB", "MOB"]
        partition_column = "grade" if view == "Grade" else "size_group"
        partitions = ["A", "B", "C", "Other"] if view == "Grade" else ["20", "40", "Other"]
        where, parameters = self._where(port, start_period, end_period)
        rows = self._dataframe(
            f"""
            SELECT curr_state, {partition_column} AS partition, CAST(SUM(teu) AS BIGINT) AS teu
            FROM {self.query.source_sql}
            WHERE {where} AND curr_state IN ('FXD', 'MXD', 'FOB', 'MOB')
            GROUP BY curr_state, {partition_column}
            """,
            parameters,
        )
        lookup = {
            (row.curr_state, row.partition): int(row.teu)
            for row in rows.itertuples()
        }
        return categories, {
            partition: [lookup.get((category, partition), 0) for category in categories]
            for partition in partitions
        }

    def summary_stuffing_stripping(
        self,
        port: str | list[str],
        start_period: str,
        end_period: str,
    ) -> dict[str, list[int]]:
        where, parameters = self._where(port, start_period, end_period)
        rows = self._dataframe(
            f"""
            SELECT activity_name, CAST(SUM(teu) AS BIGINT) AS teu
            FROM {self.query.source_sql}
            WHERE {where}
              AND activity_name IN (
                  'STUFFING DALAM', 'STUFFING LUAR',
                  'STRIPPING DALAM', 'STRIPPING LUAR'
              )
            GROUP BY activity_name
            """,
            parameters,
        )
        totals = dict(zip(rows.get("activity_name", []), rows.get("teu", [])))
        mapping = {
            "Stuffing": ["STUFFING DALAM", "STUFFING LUAR"],
            "Stripping": ["STRIPPING DALAM", "STRIPPING LUAR"],
        }
        return {
            group: [int(totals.get(activity, 0)) for activity in activities]
            for group, activities in mapping.items()
        }

    def summary_vessel_detail(
        self,
        port: str | list[str],
        start_period: str,
        end_period: str,
    ) -> list[dict[str, object]]:
        where, parameters = self._where(port, start_period, end_period)
        activity_map = {
            ("bongkar", "fl"): "BONGKAR FULL",
            ("bongkar", "mt"): "BONGKAR MT",
            ("muat", "fl"): "MUAT FULL",
            ("muat", "mt"): "MUAT MT",
            ("bongkar_trans", "fl"): "BONGKAR FULL TRANSHIPMENT",
            ("bongkar_trans", "mt"): "BONGKAR MT TRANSHIPMENT",
            ("muat_trans", "fl"): "MUAT FULL TRANSHIPMENT",
            ("muat_trans", "mt"): "MUAT MT TRANSHIPMENT",
        }
        expressions = []
        metric_columns = []
        for group in ["bongkar", "muat", "bongkar_trans", "muat_trans"]:
            for size in ["20", "40"]:
                size_filter = "size_ft >= 40" if size == "40" else "(size_ft < 40 OR size_ft IS NULL)"
                for condition in ["fl", "mt"]:
                    metric = f"{group}_{size}_{condition}"
                    metric_columns.append(metric)
                    activity = activity_map[(group, condition)]
                    expressions.append(
                        f"CAST(SUM(CASE WHEN activity_name = '{activity}' "
                        f"AND {size_filter} THEN quantity ELSE 0 END) AS BIGINT) AS {metric}"
                    )
        activities = ", ".join(f"'{activity}'" for activity in activity_map.values())
        rows = self._dataframe(
            f"""
            SELECT
                port,
                vessel_voyage,
                activity_month,
                {', '.join(expressions)},
                CAST(SUM(quantity) AS BIGINT) AS total
            FROM {self.query.source_sql}
            WHERE {where}
              AND vessel_voyage <> 'UNKNOWN'
              AND activity_name IN ({activities})
            GROUP BY port, vessel_voyage, activity_month
            ORDER BY activity_month DESC, port, total DESC, vessel_voyage
            """,
            parameters,
        )
        records: list[dict[str, object]] = []
        for row in rows.to_dict("records"):
            record = {
                "port": row["port"],
                "vessel_voyage": row["vessel_voyage"],
                "td_month": format_month(str(row["activity_month"])),
            }
            record.update({column: int(row[column]) for column in metric_columns})
            records.append(record)
        return records

    def report_activity_summary(
        self,
        port: str | list[str],
        start_period: str,
        end_period: str,
    ) -> pd.DataFrame:
        where, parameters = self._where(port, start_period, end_period)
        return self._dataframe(
            f"""
            SELECT
                port AS Cabang,
                activity_month AS Periode,
                activity_group AS Kelompok,
                activity_name AS Aktivitas,
                CONCAT(prev_state, ' - ', curr_state) AS "Transisi Status",
                load_condition AS Kondisi,
                movement_scope AS Scope,
                CAST(SUM(quantity) AS BIGINT) AS "Jumlah Event",
                COUNT(DISTINCT container_no) AS "Container Unik",
                CAST(SUM(teu) AS BIGINT) AS TEU
            FROM {self.query.source_sql}
            WHERE {where}
            GROUP BY
                port, activity_month, activity_group, activity_name,
                prev_state, curr_state, load_condition, movement_scope
            ORDER BY port, activity_month, activity_group, activity_name, prev_state, curr_state
            """,
            parameters,
        )

    def report_vessel_arrivals(
        self,
        port: str | list[str],
        start_period: str,
        end_period: str,
    ) -> pd.DataFrame:
        where, parameters = self._where(port, start_period, end_period)
        return self._dataframe(
            f"""
            SELECT port AS Cabang, COUNT(*) AS "Kedatangan Vessel"
            FROM (
                SELECT DISTINCT port, vessel_voyage
                FROM {self.query.source_sql}
                WHERE {where}
                  AND is_vessel_arrival
                  AND vessel_voyage <> 'UNKNOWN'
            )
            GROUP BY port
            ORDER BY port
            """,
            parameters,
        )

    # Compatibility helpers retained for tests and callers holding a DataFrame.
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
        self, port: str, start_period: str, end_period: str | None = None
    ) -> tuple[dict[str, int], dict[str, float]]:
        end_period = end_period or start_period
        current = self.kpis(self.filtered(port, start_period, end_period))
        start = pd.Period(start_period, freq="M")
        end = pd.Period(end_period, freq="M")
        count = end.ordinal - start.ordinal + 1
        previous_end = start - 1
        previous_start = previous_end - (count - 1)
        previous_data = self.filtered(port, str(previous_start), str(previous_end))
        previous = self.kpis(previous_data)
        changes = {
            key: ((value - previous[key]) / previous[key]) * 100
            if not previous_data.empty and previous[key]
            else 0.0
            for key, value in current.items()
        }
        return current, changes

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
            .sort_values("teu")
        )
        return ranked["cargo"].tolist(), ranked["teu"].astype(int).tolist()

    @staticmethod
    def composition(data: pd.DataFrame, view: str) -> tuple[list[str], dict[str, list[int]]]:
        categories = ["FXD", "MXD", "FOB", "MOB"]
        selected = data[data["curr_state"].isin(categories)]
        column = "grade" if view == "Grade" else "size_group"
        partitions = ["A", "B", "C", "Other"] if view == "Grade" else ["20", "40", "Other"]
        pivot = selected.pivot_table(
            index="curr_state", columns=column, values="teu", aggfunc="sum", fill_value=0
        )
        return categories, {
            partition: [
                int(pivot.loc[category, partition])
                if category in pivot.index and partition in pivot.columns
                else 0
                for category in categories
            ]
            for partition in partitions
        }

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
        selected["size_bucket"] = np.where(selected["size_ft"].ge(40), "40", "20")
        group_map = {activity: values[0] for activity, values in bucket_map.items()}
        condition_map = {activity: values[1] for activity, values in bucket_map.items()}
        selected["metric"] = (
            selected["activity_name"].map(group_map)
            + "_" + selected["size_bucket"] + "_"
            + selected["activity_name"].map(condition_map)
        )
        grouped = selected.pivot_table(
            index=["port", "vessel_voyage", "activity_month"],
            columns="metric",
            values="quantity",
            aggfunc="sum",
            fill_value=0,
        ).reset_index()
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
        grouped = grouped.sort_values(
            ["activity_month", "port", "total"], ascending=[False, True, False]
        )
        records = []
        for row in grouped.to_dict("records"):
            record = {
                "port": row["port"],
                "vessel_voyage": row["vessel_voyage"],
                "td_month": format_month(row["activity_month"]),
            }
            record.update({column: int(row[column]) for column in metric_columns})
            records.append(record)
        return records
