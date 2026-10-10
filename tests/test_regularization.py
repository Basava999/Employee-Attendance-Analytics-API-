"""
Regularization (PATCH /attendance/{emp_code}/{date}) tests.
Requires MongoDB.
"""
import pytest
import os
import sys
from datetime import datetime, timezone

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
os.environ.setdefault("MONGO_URI", "mongodb://localhost:27017")
os.environ.setdefault("MONGO_DB", "attendance_test_regularize")


@pytest.fixture(scope="module")
def client():
    try:
        from pymongo import MongoClient
        c = MongoClient(os.environ["MONGO_URI"], serverSelectionTimeoutMS=3000)
        c.admin.command("ping")
        c.close()
    except Exception:
        pytest.skip("MongoDB not available")

    from fastapi.testclient import TestClient
    from app.main import app, employees_col, attendance_col
    employees_col.delete_many({})
    attendance_col.delete_many({})
    with TestClient(app) as tc:
        yield tc
    employees_col.delete_many({})
    attendance_col.delete_many({})


@pytest.fixture(autouse=True)
def clean(client):
    from app.main import employees_col, attendance_col
    employees_col.delete_many({})
    attendance_col.delete_many({})
    yield


def _create_emp(client, code="EMP0001", **kw):
    data = {
        "emp_code": code, "name": f"User {code}", "email": f"{code.lower()}@t.com",
        "department": "Engineering", "shift_start": kw.get("shift_start", "09:30"),
        "shift_end": kw.get("shift_end", "18:30"), "joined_on": "2026-01-05",
    }
    assert client.post("/employees", json=data).status_code == 201


def _punch_in(client, code="EMP0001", ts_ms=1783310280000, status="PRESENT"):
    resp = client.post("/attendance/punch-in", json={
        "emp_code": code, "punched_at": ts_ms, "status": status,
    })
    assert resp.status_code == 201
    return resp.json()


def _punch_out(client, code="EMP0001", ts_ms=None):
    if ts_ms is None:
        ts_ms = int(datetime(2026, 7, 6, 13, 5, 0, tzinfo=timezone.utc).timestamp() * 1000)
    resp = client.post("/attendance/punch-out", json={
        "emp_code": code, "punched_at": ts_ms,
    })
    assert resp.status_code == 200
    return resp.json()


class TestRegularize:
    def test_change_status_to_leave(self, client):
        """Change a PRESENT record to LEAVE — clears punches, recomputes derived."""
        _create_emp(client)
        _punch_in(client)
        _punch_out(client)

        resp = client.patch("/attendance/EMP0001/2026-07-06", json={
            "status": "LEAVE",
            "reason": "Approved leave retroactively",
            "regularized_by": "hr.admin",
        })
        assert resp.status_code == 200
        data = resp.json()
        assert data["status"] == "LEAVE"
        assert data["punch_in"] is None
        assert data["punch_out"] is None
        assert data["work_hours"] is None
        assert data["late_minutes"] == 0
        assert data["overtime_minutes"] == 0
        assert data["half_day"] == False
        assert len(data["history"]) == 1
        h = data["history"][0]
        assert h["by"] == "hr.admin"
        assert h["reason"] == "Approved leave retroactively"
        assert "status" in h["changes"]
        assert h["changes"]["status"]["from"] == "PRESENT"
        assert h["changes"]["status"]["to"] == "LEAVE"

    def test_change_punch_in_recomputes_late(self, client):
        """Change punch_in from late to early — late_minutes should change."""
        _create_emp(client)
        # Punch in late: 10:05 IST
        _punch_in(client, ts_ms=1783312500000)
        _punch_out(client, ts_ms=int(datetime(2026, 7, 6, 13, 40, 0, tzinfo=timezone.utc).timestamp() * 1000))

        # Regularize to 09:28 IST
        resp = client.patch("/attendance/EMP0001/2026-07-06", json={
            "punch_in": 1783310280000,  # 09:28 IST = 03:58 UTC
            "reason": "Biometric glitch correction",
            "regularized_by": "hr.admin",
        })
        assert resp.status_code == 200
        data = resp.json()
        assert data["late_minutes"] == 0  # Was 35, now 0
        assert "late_minutes" in data["history"][-1]["changes"]
        assert data["history"][-1]["changes"]["late_minutes"]["from"] == 35
        assert data["history"][-1]["changes"]["late_minutes"]["to"] == 0

    def test_no_change_is_422(self, client):
        """Submitting same values → 422."""
        _create_emp(client)
        _punch_in(client)
        resp = client.patch("/attendance/EMP0001/2026-07-06", json={
            "status": "PRESENT",
            "reason": "Just checking",
            "regularized_by": "admin",
        })
        assert resp.status_code == 422

    def test_absent_with_punch_422(self, client):
        """Setting ABSENT while supplying punch_in → 422."""
        _create_emp(client)
        _punch_in(client)
        resp = client.patch("/attendance/EMP0001/2026-07-06", json={
            "status": "ABSENT",
            "punch_in": 1783310280000,
            "reason": "Testing rejection",
            "regularized_by": "admin",
        })
        assert resp.status_code == 422

    def test_punch_out_without_punch_in_422(self, client):
        """Setting punch_out when no punch_in exists → 422."""
        from app.main import attendance_col, employees_col
        _create_emp(client)
        # Insert an ABSENT record directly
        attendance_col.insert_one({
            "emp_code": "EMP0001", "date": "2026-07-06", "status": "ABSENT",
            "punch_in": None, "punch_out": None,
            "work_hours": None, "late_minutes": 0, "overtime_minutes": 0,
            "half_day": False, "history": [],
        })
        resp = client.patch("/attendance/EMP0001/2026-07-06", json={
            "punch_out": int(datetime(2026, 7, 6, 13, 0, 0, tzinfo=timezone.utc).timestamp() * 1000),
            "reason": "Trying to add punch out",
            "regularized_by": "admin",
        })
        assert resp.status_code == 422

    def test_unknown_employee_404(self, client):
        resp = client.patch("/attendance/EMP9999/2026-07-06", json={
            "status": "LEAVE",
            "reason": "Testing 404",
            "regularized_by": "admin",
        })
        assert resp.status_code == 404

    def test_no_record_404(self, client):
        _create_emp(client)
        resp = client.patch("/attendance/EMP0001/2026-07-06", json={
            "status": "LEAVE",
            "reason": "No record exists",
            "regularized_by": "admin",
        })
        assert resp.status_code == 404

    def test_history_preserves_prior_entries(self, client):
        """Two consecutive regularizations — both history entries preserved."""
        _create_emp(client)
        _punch_in(client, ts_ms=1783312500000)  # late
        _punch_out(client, ts_ms=int(datetime(2026, 7, 6, 13, 40, 0, tzinfo=timezone.utc).timestamp() * 1000))

        # First correction
        client.patch("/attendance/EMP0001/2026-07-06", json={
            "punch_in": 1783310280000,
            "reason": "First fix",
            "regularized_by": "admin1",
        })

        # Second correction
        resp = client.patch("/attendance/EMP0001/2026-07-06", json={
            "status": "WFH",
            "reason": "Actually WFH",
            "regularized_by": "admin2",
        })
        assert resp.status_code == 200
        data = resp.json()
        assert len(data["history"]) == 2
        assert data["history"][0]["by"] == "admin1"
        assert data["history"][1]["by"] == "admin2"

    def test_short_reason_422(self, client):
        _create_emp(client)
        _punch_in(client)
        resp = client.patch("/attendance/EMP0001/2026-07-06", json={
            "status": "LEAVE",
            "reason": "abc",  # too short (< 5)
            "regularized_by": "admin",
        })
        assert resp.status_code == 422

    def test_history_punch_times_as_epoch_ms(self, client):
        """History changes for punch_in should be epoch ms, not raw datetimes."""
        _create_emp(client)
        _punch_in(client, ts_ms=1783312500000)
        _punch_out(client, ts_ms=int(datetime(2026, 7, 6, 13, 40, 0, tzinfo=timezone.utc).timestamp() * 1000))

        resp = client.patch("/attendance/EMP0001/2026-07-06", json={
            "punch_in": 1783310280000,
            "reason": "Time correction",
            "regularized_by": "admin",
        })
        assert resp.status_code == 200
        changes = resp.json()["history"][-1]["changes"]
        assert "punch_in" in changes
        assert isinstance(changes["punch_in"]["from"], int)
        assert isinstance(changes["punch_in"]["to"], int)
