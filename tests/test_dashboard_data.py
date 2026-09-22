import unittest
from tempfile import TemporaryDirectory
from pathlib import Path

import pandas as pd
from data.dashboard_repository import DashboardRepository
from data.dashboard_store import DashboardDataStore, DuplicateMonthError
from pipeline.build_dashboard_data import (
    DEFAULT_OUTPUT,
    DEFAULT_SOURCE,
    build_dashboard_data,
    build_dashboard_frame,
)


class DashboardDataTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        build_dashboard_data(DEFAULT_SOURCE, DEFAULT_OUTPUT)
        cls.repository = DashboardRepository(DEFAULT_OUTPUT)
        cls.data = cls.repository.frame

    def test_pipeline_preserves_unique_events(self) -> None:
        self.assertEqual(len(self.data), 38_016)
        self.assertEqual(self.data["event_id"].nunique(), 38_016)

    def test_missing_history_uses_stable_synthetic_id_and_deduplicates(self) -> None:
        raw = pd.read_csv(DEFAULT_SOURCE, nrows=2)
        raw["NO_HISTORY"] = pd.NA
        raw.loc[:, "NO_CONTAINER"] = "TEST1234567"
        raw.loc[:, "CURRSTATE"] = "FTL"
        raw.loc[:, "CTSTAMP"] = "10/06/2026 12:00:00"
        raw.loc[:, "CTGLSTATUS"] = "10/06/2026"
        raw.loc[0, "PREV_TGLSTATUS"] = "01/06/2026"
        raw.loc[1, "PREV_TGLSTATUS"] = "09/06/2026"
        raw.loc[0, "PREV_STATE"] = "MTA"
        raw.loc[1, "PREV_STATE"] = "MAS"

        first = build_dashboard_frame(raw, source_name="missing-history.csv")
        second = build_dashboard_frame(raw.iloc[::-1], source_name="missing-history.csv")

        self.assertEqual(len(first), 1)
        self.assertEqual(first.iloc[0]["prev_state"], "MAS")
        self.assertTrue(first.iloc[0]["event_id"].startswith("SYN-"))
        self.assertEqual(first.iloc[0]["event_id"], second.iloc[0]["event_id"])

    def test_vessel_voyage_route_is_removed(self) -> None:
        self.assertFalse(self.data["vessel_voyage"].str.contains(" ", regex=False).any())
        self.assertIn("TBI-13/2026", set(self.data["vessel_voyage"]))

    def test_teu_conversion(self) -> None:
        twenty = self.data[self.data["size_ft"].between(20, 39)]
        forty = self.data[self.data["size_ft"] >= 40]
        self.assertTrue((twenty["teu"] == 1).all())
        self.assertTrue((forty["teu"] == 2).all())

    def test_activity_mapping_and_kpis(self) -> None:
        stuffing_inside = self.data[self.data["activity_name"] == "STUFFING DALAM"]
        self.assertEqual(len(stuffing_inside), 1_102)
        self.assertEqual(int(stuffing_inside["teu"].sum()), 1_197)

        filtered = self.repository.filtered("Semua", "2026-06")
        kpis = self.repository.kpis(filtered)
        self.assertEqual(kpis["muat_full"], 3_240)
        self.assertEqual(kpis["bongkar_full"], 7_478)
        self.assertEqual(kpis["vessel_arrivals"], 27)

    def test_vessel_arrivals_are_unique_per_port_and_voyage(self) -> None:
        filtered = self.repository.filtered("Semua", "2026-06")
        arrivals = filtered[filtered["is_vessel_arrival"] & filtered["vessel_voyage"].ne("UNKNOWN")]
        expected = len(arrivals[["port", "vessel_voyage"]].drop_duplicates())
        self.assertEqual(self.repository.kpis(filtered)["vessel_arrivals"], expected)
        self.assertEqual(expected, 27)

    def test_period_filter_is_inclusive_range(self) -> None:
        repository = DashboardRepository.__new__(DashboardRepository)
        june = self.data.head(2).copy()
        may = june.copy()
        may["activity_month"] = "2026-05"
        repository.frame = pd.concat([may, june], ignore_index=True)

        self.assertEqual(len(repository.filtered("Semua", "2026-05", "2026-06")), 4)
        self.assertEqual(len(repository.filtered("Semua", "2026-06", "2026-06")), 2)
        with self.assertRaises(ValueError):
            repository.filtered("Semua", "2026-06", "2026-05")

    def test_vessel_detail_uses_quantity(self) -> None:
        rows = self.repository.vessel_detail(self.repository.filtered("Semua", "2026-06"))
        self.assertGreater(len(rows), 0)
        self.assertTrue(all(isinstance(row["bongkar_20_fl"], int) for row in rows))

    def test_composition_keeps_status_axis_for_grade_and_size(self) -> None:
        filtered = self.repository.filtered("Semua", "2026-06")
        expected_statuses = ["FXD", "MXD", "FOB", "MOB"]

        grade_categories, grade_series = self.repository.composition(filtered, "Grade")
        size_categories, size_series = self.repository.composition(filtered, "Size")

        self.assertEqual(grade_categories, expected_statuses)
        self.assertEqual(size_categories, expected_statuses)
        self.assertEqual(list(grade_series), ["A", "B", "C", "Other"])
        self.assertEqual(list(size_series), ["20", "40", "Other"])
        expected_teu = int(filtered.loc[filtered["curr_state"].isin(expected_statuses), "teu"].sum())
        self.assertEqual(sum(map(sum, grade_series.values())), expected_teu)
        self.assertEqual(sum(map(sum, size_series.values())), expected_teu)

    def test_trend_requires_single_activity_direction(self) -> None:
        filtered = self.repository.filtered("Semua", "2026-06")
        _, muat_series = self.repository.trend(filtered, "Muat")
        _, bongkar_series = self.repository.trend(filtered, "Bongkar")

        self.assertNotEqual(muat_series, bongkar_series)
        with self.assertRaises(ValueError):
            self.repository.trend(filtered, "Semua")

    def test_missing_previous_month_defaults_to_zero_change(self) -> None:
        current, changes = self.repository.kpi_comparison("Semua", "2026-06")
        self.assertEqual(current["muat_full"], 3_240)
        self.assertTrue(all(change == 0.0 for change in changes.values()))

    def test_monthly_store_rejects_duplicate_and_can_delete(self) -> None:
        raw = pd.read_csv(DEFAULT_SOURCE, nrows=25)
        content = raw.to_csv(index=False).encode("utf-8")
        with TemporaryDirectory() as directory:
            store = DashboardDataStore(Path(directory) / "dashboard.parquet")
            result = store.import_bytes("juni.csv", content)
            self.assertEqual(result["month"], "2026-06")
            self.assertEqual(len(store.monthly_summary()), 1)
            self.assertEqual(store.monthly_summary()[0]["refresh_status"], "Belum refresh")
            with self.assertRaises(DuplicateMonthError):
                store.import_bytes("juni-lagi.csv", content)
            published = store.publish()
            self.assertEqual(published["events"], 25)
            self.assertEqual(store.monthly_summary()[0]["refresh_status"], "Sudah refresh")
            self.assertEqual(len(DashboardRepository(store.dashboard_path).frame), 25)
            self.assertEqual(store.delete_month("2026-06"), 25)
            self.assertEqual(store.monthly_summary(), [])


if __name__ == "__main__":
    unittest.main()
