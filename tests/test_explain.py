"""
Explain endpoint tests.
Tests:
- GET /admin/explain/{endpoint} for all supported endpoints.
"""
import pytest
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
os.environ.setdefault("MONGO_URI", "mongodb://localhost:27017")
os.environ.setdefault("MONGO_DB", "attendance_test_explain")


def find_plan_stages_with_context(plan_obj, current_coll=None) -> list[dict]:
    """Recursively traverse a MongoDB explain output dictionary to collect stage details with namespace context."""
    results = []
    if not isinstance(plan_obj, (dict, list)):
        return results

    if isinstance(plan_obj, list):
        for item in plan_obj:
            results.extend(find_plan_stages_with_context(item, current_coll))
        return results

    coll = plan_obj.get("namespace") or plan_obj.get("nss") or current_coll
    if "stage" in plan_obj:
        results.append({
            "stage": plan_obj["stage"],
            "namespace": coll,
            "indexName": plan_obj.get("indexName"),
        })

    for k, v in plan_obj.items():
        sub_coll = coll
        if k == "$lookup" and isinstance(v, dict):
            sub_coll = v.get("from", coll)
        elif k == "$unionWith" and isinstance(v, dict):
            sub_coll = v.get("coll", coll)
        results.extend(find_plan_stages_with_context(v, sub_coll))

    return results


def find_plan_stages(plan_dict: dict) -> list[str]:
    """Recursively traverse a MongoDB explain output dictionary to collect all stage names."""
    return [entry["stage"] for entry in find_plan_stages_with_context(plan_dict)]


def verify_explain_plan_efficiency(explain_data: dict, expected_endpoint: str, expected_collection: str):
    """Verify that explain output contains valid MongoDB executionStats and uses genuine index scans."""
    assert explain_data["endpoint"] == expected_endpoint
    assert explain_data["collection"] == expected_collection
    assert "explain" in explain_data
    assert isinstance(explain_data["explain"], dict)

    raw_explain = explain_data["explain"]
    # Verify authentic MongoDB explain output envelope structure
    assert any(k in raw_explain for k in ("executionStats", "queryPlanner", "stages", "explainVersion", "serverInfo")), \
        f"Explain payload lacks authentic MongoDB explain structures: {list(raw_explain.keys())}"

    stage_entries = find_plan_stages_with_context(raw_explain, expected_collection)
    stages = [entry["stage"] for entry in stage_entries]

    if "executionStats" in raw_explain or "queryPlanner" in raw_explain or "stages" in raw_explain:
        assert len(stages) > 0, f"No execution stages found in explain output for {expected_endpoint}"

        # Require genuine index-backed execution stages (do NOT treat FETCH alone as proof)
        has_genuine_index = (
            any(s in ("IXSCAN", "COUNT_SCAN", "DISTINCT_SCAN") or "INDEX" in s for s in stages)
            or any(e.get("indexName") is not None for e in stage_entries)
        )
        assert has_genuine_index, \
            f"Expected genuine index-backed execution stage (e.g. IXSCAN, COUNT_SCAN) for {expected_endpoint}, got stages: {stages}"

        # Ensure no unindexed COLLSCAN on primary collection
        primary_collscans = [
            e for e in stage_entries
            if e["stage"] == "COLLSCAN" and (e["namespace"] is None or expected_collection in str(e["namespace"]))
        ]
        assert len(primary_collscans) == 0, \
            f"Unexpected COLLSCAN stage found on primary collection {expected_collection} for {expected_endpoint}: {stage_entries}"


class TestExplainEndpoint:
    """Test GET /admin/explain/{endpoint}."""

    def test_unknown_endpoint_422(self, app_client):
        resp = app_client.get("/admin/explain/non_existent_endpoint")
        assert resp.status_code == 422

    def test_missing_params_employee_monthly_422(self, app_client):
        resp = app_client.get("/admin/explain/employee_monthly")
        assert resp.status_code == 422

    def test_missing_params_department_summary_422(self, app_client):
        resp = app_client.get("/admin/explain/department_summary")
        assert resp.status_code == 422

    def test_missing_params_department_trend_422(self, app_client):
        resp = app_client.get("/admin/explain/department_trend")
        assert resp.status_code == 422

    def test_explain_attendance_list_filtered(self, app_client):
        resp = app_client.get("/admin/explain/attendance_list?emp_code=EMP0001&date_from=2026-07-01&date_to=2026-07-31")
        assert resp.status_code == 200
        verify_explain_plan_efficiency(resp.json(), "attendance_list", "attendance_logs")

    def test_explain_attendance_list_unfiltered(self, app_client):
        resp = app_client.get("/admin/explain/attendance_list?page=1&page_size=20")
        assert resp.status_code == 200
        verify_explain_plan_efficiency(resp.json(), "attendance_list", "attendance_logs")

    def test_explain_employee_monthly(self, app_client):
        resp = app_client.get("/admin/explain/employee_monthly?emp_code=EMP0001&month=2026-07")
        assert resp.status_code == 200
        verify_explain_plan_efficiency(resp.json(), "employee_monthly", "attendance_logs")

    def test_explain_department_summary_unfiltered(self, app_client):
        resp = app_client.get("/admin/explain/department_summary?month=2026-07")
        assert resp.status_code == 200
        verify_explain_plan_efficiency(resp.json(), "department_summary", "employees")

    def test_explain_department_summary_filtered(self, app_client):
        resp = app_client.get("/admin/explain/department_summary?month=2026-07&department=Engineering")
        assert resp.status_code == 200
        verify_explain_plan_efficiency(resp.json(), "department_summary", "employees")

    def test_explain_late_leaderboard_unfiltered(self, app_client):
        resp = app_client.get("/admin/explain/late_leaderboard?month=2026-07&limit=10")
        assert resp.status_code == 200
        verify_explain_plan_efficiency(resp.json(), "late_leaderboard", "attendance_logs")

    def test_explain_late_leaderboard_filtered(self, app_client):
        resp = app_client.get("/admin/explain/late_leaderboard?month=2026-07&department=Engineering&limit=5")
        assert resp.status_code == 200
        verify_explain_plan_efficiency(resp.json(), "late_leaderboard", "attendance_logs")

    def test_explain_department_trend_short_range(self, app_client):
        resp = app_client.get("/admin/explain/department_trend?department=Engineering&from=2026-07-01&to=2026-07-07")
        assert resp.status_code == 200
        verify_explain_plan_efficiency(resp.json(), "department_trend", "attendance_logs")

    def test_explain_department_trend_monthly_range(self, app_client):
        resp = app_client.get("/admin/explain/department_trend?department=Engineering&from=2026-07-01&to=2026-07-31")
        assert resp.status_code == 200
        verify_explain_plan_efficiency(resp.json(), "department_trend", "attendance_logs")
