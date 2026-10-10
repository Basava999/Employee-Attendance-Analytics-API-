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

    def test_explain_attendance_list(self, app_client):
        resp = app_client.get("/admin/explain/attendance_list?emp_code=EMP0001&date_from=2026-07-01&date_to=2026-07-31")
        assert resp.status_code == 200
        data = resp.json()
        assert data["endpoint"] == "attendance_list"
        assert data["collection"] == "attendance_logs"
        assert "explain" in data
        assert isinstance(data["explain"], dict)

    def test_explain_employee_monthly(self, app_client):
        resp = app_client.get("/admin/explain/employee_monthly?emp_code=EMP0001&month=2026-07")
        assert resp.status_code == 200
        data = resp.json()
        assert data["endpoint"] == "employee_monthly"
        assert data["collection"] == "attendance_logs"
        assert "explain" in data
        assert isinstance(data["explain"], dict)

    def test_explain_department_summary(self, app_client):
        resp = app_client.get("/admin/explain/department_summary?month=2026-07")
        assert resp.status_code == 200
        data = resp.json()
        assert data["endpoint"] == "department_summary"
        assert data["collection"] == "employees"
        assert "explain" in data
        assert isinstance(data["explain"], dict)

    def test_explain_late_leaderboard(self, app_client):
        resp = app_client.get("/admin/explain/late_leaderboard?month=2026-07")
        assert resp.status_code == 200
        data = resp.json()
        assert data["endpoint"] == "late_leaderboard"
        assert data["collection"] == "attendance_logs"
        assert "explain" in data
        assert isinstance(data["explain"], dict)

    def test_explain_department_trend(self, app_client):
        resp = app_client.get("/admin/explain/department_trend?department=Engineering&from=2026-07-01&to=2026-07-07")
        assert resp.status_code == 200
        data = resp.json()
        assert data["endpoint"] == "department_trend"
        assert data["collection"] == "attendance_logs"
        assert "explain" in data
        assert isinstance(data["explain"], dict)
