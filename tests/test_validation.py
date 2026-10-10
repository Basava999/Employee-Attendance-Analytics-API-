"""
Validation tests — test all 422 rejection paths.
These require MongoDB for integration testing.
"""
import pytest
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
os.environ.setdefault("MONGO_URI", "mongodb://localhost:27017")
os.environ.setdefault("MONGO_DB", "attendance_test_validation")


@pytest.fixture(scope="module")
def client():
    """Get a test client. Uses real MongoDB if available, otherwise mocks for pure validation testing."""
    mongo_up = False
    try:
        from pymongo import MongoClient
        c = MongoClient(os.environ["MONGO_URI"], serverSelectionTimeoutMS=1000)
        c.admin.command("ping")
        c.close()
        mongo_up = True
    except Exception:
        mongo_up = False

    from fastapi.testclient import TestClient
    from app.main import app, employees_col, attendance_col

    if mongo_up:
        employees_col.delete_many({})
        attendance_col.delete_many({})
        with TestClient(app) as tc:
            yield tc
        employees_col.delete_many({})
        attendance_col.delete_many({})
    else:
        from unittest.mock import MagicMock, patch
        with patch.object(employees_col, "find_one", return_value=None), \
             patch.object(employees_col, "insert_one", return_value=MagicMock()), \
             patch.object(attendance_col, "find_one", return_value=None), \
             patch.object(attendance_col, "insert_one", return_value=MagicMock()):
            with TestClient(app) as tc:
                yield tc


class TestEmployeeValidation:
    """Test POST /employees validation → 422."""

    def test_bad_emp_code_format(self, client):
        resp = client.post("/employees", json={
            "emp_code": "BAD001", "name": "X", "email": "x@y.com",
            "department": "D", "joined_on": "2026-01-05",
        })
        assert resp.status_code == 422

    def test_emp_code_too_short(self, client):
        resp = client.post("/employees", json={
            "emp_code": "EMP01", "name": "X", "email": "x@y.com",
            "department": "D", "joined_on": "2026-01-05",
        })
        assert resp.status_code == 422  # EMP01 is only 2 digits

    def test_empty_name(self, client):
        resp = client.post("/employees", json={
            "emp_code": "EMP0001", "name": "", "email": "x@y.com",
            "department": "D", "joined_on": "2026-01-05",
        })
        assert resp.status_code == 422

    def test_bad_email(self, client):
        resp = client.post("/employees", json={
            "emp_code": "EMP0001", "name": "X", "email": "notanemail",
            "department": "D", "joined_on": "2026-01-05",
        })
        assert resp.status_code == 422

    def test_shift_start_equals_end(self, client):
        resp = client.post("/employees", json={
            "emp_code": "EMP0001", "name": "X", "email": "x@y.com",
            "department": "D", "shift_start": "09:30", "shift_end": "09:30",
            "joined_on": "2026-01-05",
        })
        assert resp.status_code == 422

    def test_invalid_joined_on_date(self, client):
        """Feb 30 doesn't exist."""
        resp = client.post("/employees", json={
            "emp_code": "EMP0001", "name": "X", "email": "x@y.com",
            "department": "D", "joined_on": "2026-02-30",
        })
        assert resp.status_code == 422

    def test_bad_shift_time_format(self, client):
        resp = client.post("/employees", json={
            "emp_code": "EMP0001", "name": "X", "email": "x@y.com",
            "department": "D", "shift_start": "9:30", "joined_on": "2026-01-05",
        })
        assert resp.status_code == 422

    def test_name_too_long(self, client):
        resp = client.post("/employees", json={
            "emp_code": "EMP0001", "name": "X" * 101, "email": "x@y.com",
            "department": "D", "joined_on": "2026-01-05",
        })
        assert resp.status_code == 422


class TestPunchInValidation:
    """Test POST /attendance/punch-in validation."""

    def test_seconds_instead_of_ms(self, client):
        """Seconds-looking value (< 100 billion) → 422."""
        # Create employee first
        client.post("/employees", json={
            "emp_code": "EMP1001", "name": "Test", "email": "t@t.com",
            "department": "D", "joined_on": "2026-01-05",
        })
        resp = client.post("/attendance/punch-in", json={
            "emp_code": "EMP1001", "punched_at": 1609459200,  # seconds, not ms
        })
        assert resp.status_code == 422

    def test_float_timestamp(self, client):
        """Float instead of int → 422 (StrictInt)."""
        resp = client.post("/attendance/punch-in", json={
            "emp_code": "EMP1001", "punched_at": 1783312500000.0,
        })
        assert resp.status_code == 422

    def test_string_timestamp(self, client):
        """String timestamp → 422."""
        resp = client.post("/attendance/punch-in", json={
            "emp_code": "EMP1001", "punched_at": "1783312500000",
        })
        assert resp.status_code == 422

    def test_boolean_timestamp(self, client):
        """Boolean → 422."""
        resp = client.post("/attendance/punch-in", json={
            "emp_code": "EMP1001", "punched_at": True,
        })
        assert resp.status_code == 422

    def test_out_of_range_high(self, client):
        resp = client.post("/attendance/punch-in", json={
            "emp_code": "EMP1001", "punched_at": 5000000000000,
        })
        assert resp.status_code == 422

    def test_absent_status_rejected(self, client):
        """ABSENT is not a valid punch-in status."""
        resp = client.post("/attendance/punch-in", json={
            "emp_code": "EMP1001", "status": "ABSENT",
        })
        assert resp.status_code == 422

    def test_leave_status_rejected(self, client):
        resp = client.post("/attendance/punch-in", json={
            "emp_code": "EMP1001", "status": "LEAVE",
        })
        assert resp.status_code == 422

    def test_unknown_employee_404(self, client):
        resp = client.post("/attendance/punch-in", json={
            "emp_code": "EMP9999",
        })
        assert resp.status_code == 404


class TestPaginationValidation:
    """Test pagination parameter validation."""

    def test_page_size_over_100(self, client):
        resp = client.get("/employees?page_size=101")
        assert resp.status_code == 422

    def test_page_zero(self, client):
        resp = client.get("/employees?page=0")
        assert resp.status_code == 422

    def test_attendance_page_size_over_100(self, client):
        resp = client.get("/attendance?page_size=101")
        assert resp.status_code == 422

    def test_date_from_gt_date_to(self, client):
        resp = client.get("/attendance?date_from=2026-07-08&date_to=2026-07-06")
        assert resp.status_code == 422


class TestExplainValidation:
    """Test GET /admin/explain/{endpoint} validation."""

    def test_unknown_endpoint_422(self, client):
        resp = client.get("/admin/explain/unknown_endpoint")
        assert resp.status_code == 422

    def test_employee_monthly_missing_params(self, client):
        resp = client.get("/admin/explain/employee_monthly")
        assert resp.status_code == 422

    def test_department_trend_missing_params(self, client):
        resp = client.get("/admin/explain/department_trend")
        assert resp.status_code == 422
