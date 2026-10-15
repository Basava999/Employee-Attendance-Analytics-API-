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
             patch.object(attendance_col, "insert_one", return_value=MagicMock()), \
             patch("app.main.create_indexes"):
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
        assert resp.status_code == 201
        data = resp.json()
        assert data["shift_start"] == "09:30"
        assert data["shift_end"] == "09:30"

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

    def test_department_trend_reversed_dates(self, client):
        resp = client.get("/admin/explain/department_trend?department=Engineering&from=2026-07-07&to=2026-07-01")
        assert resp.status_code == 422

    def test_department_trend_range_over_92_days(self, client):
        resp = client.get("/admin/explain/department_trend?department=Engineering&from=2026-01-01&to=2026-04-15")
        assert resp.status_code == 422


class TestRegularizeValidation:
    """Test PATCH /attendance/{emp_code}/{date} request body validation -> 422."""

    def test_explicit_null_punch_in_rejected_422(self, client):
        resp = client.patch("/attendance/EMP0001/2026-07-06", json={
            "punch_in": None,
            "reason": "Biometric glitch",
            "regularized_by": "hr.admin",
        })
        assert resp.status_code == 422

    def test_explicit_null_punch_out_rejected_422(self, client):
        resp = client.patch("/attendance/EMP0001/2026-07-06", json={
            "punch_out": None,
            "reason": "Biometric glitch",
            "regularized_by": "hr.admin",
        })
        assert resp.status_code == 422

    def test_explicit_null_status_rejected_422(self, client):
        resp = client.patch("/attendance/EMP0001/2026-07-06", json={
            "status": None,
            "reason": "Biometric glitch",
            "regularized_by": "hr.admin",
        })
        assert resp.status_code == 422

    def test_invalid_status_rejected_422(self, client):
        resp = client.patch("/attendance/EMP0001/2026-07-06", json={
            "status": "VACATION",
            "reason": "Biometric glitch",
            "regularized_by": "hr.admin",
        })
        assert resp.status_code == 422

    def test_punch_in_seconds_scale_rejected_422(self, client):
        resp = client.patch("/attendance/EMP0001/2026-07-06", json={
            "punch_in": 1783310280,  # seconds instead of epoch-ms (< 100 billion)
            "reason": "Biometric glitch",
            "regularized_by": "hr.admin",
        })
        assert resp.status_code == 422

    def test_short_reason_rejected_422(self, client):
        resp = client.patch("/attendance/EMP0001/2026-07-06", json={
            "reason": "bad",
            "regularized_by": "hr.admin",
        })
        assert resp.status_code == 422

    def test_reason_too_long_rejected_422(self, client):
        resp = client.patch("/attendance/EMP0001/2026-07-06", json={
            "reason": "X" * 201,
            "regularized_by": "hr.admin",
        })
        assert resp.status_code == 422

    def test_empty_regularized_by_rejected_422(self, client):
        resp = client.patch("/attendance/EMP0001/2026-07-06", json={
            "reason": "Valid reason here",
            "regularized_by": "",
        })
        assert resp.status_code == 422

    def test_regularized_by_too_long_rejected_422(self, client):
        resp = client.patch("/attendance/EMP0001/2026-07-06", json={
            "reason": "Valid reason here",
            "regularized_by": "A" * 51,
        })
        assert resp.status_code == 422


class TestMonthParamValidation:
    """Test validate_month_param() against malformed, boundary, and out-of-range calendar months."""

    def test_year_zero_rejected_422(self, client):
        """0000-01 has year 0, out of calendar range -> 422, never 500."""
        from app.main import validate_month_param
        from fastapi.exceptions import RequestValidationError
        with pytest.raises(RequestValidationError):
            validate_month_param("0000-01")

        resp = client.get("/analytics/departments/summary?month=0000-01")
        assert resp.status_code == 422
        resp2 = client.get("/analytics/employees/EMP0001/monthly?month=0000-01")
        assert resp2.status_code == 422

    def test_year_9999_december_rejected_422(self, client):
        """9999-12 would roll next month into year 10000 -> 422, never 500."""
        from app.main import validate_month_param
        from fastapi.exceptions import RequestValidationError
        with pytest.raises(RequestValidationError):
            validate_month_param("9999-12")

        resp = client.get("/analytics/departments/summary?month=9999-12")
        assert resp.status_code == 422
        resp2 = client.get("/analytics/employees/EMP0001/monthly?month=9999-12")
        assert resp2.status_code == 422

    @pytest.mark.parametrize("bad_month", [
        "2026-13",
        "2026-00",
        "2026-2",
        "2026/07",
        "July 2026",
        "not-a-month",
        "",
        "99999-01",
    ])
    def test_malformed_month_strings_rejected_422(self, client, bad_month):
        """Invalid month strings must return 422, never 500."""
        resp = client.get(f"/analytics/departments/summary?month={bad_month}")
        assert resp.status_code == 422

    def test_ordinary_valid_months_accepted(self):
        """Ordinary valid calendar months succeed and return expected start and exclusive-end ISO dates."""
        from app.main import validate_month_param
        start, end = validate_month_param("2026-07")
        assert start == "2026-07-01"
        assert end == "2026-08-01"

        start_dec, end_dec = validate_month_param("2026-12")
        assert start_dec == "2026-12-01"
        assert end_dec == "2027-01-01"

        start_jan, end_jan = validate_month_param("2026-01")
        assert start_jan == "2026-01-01"
        assert end_jan == "2026-02-01"

        start_9999, end_9999 = validate_month_param("9999-11")
        assert start_9999 == "9999-11-01"
        assert end_9999 == "9999-12-01"

