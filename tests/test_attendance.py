"""
Attendance punch-in/out and list tests.
Requires MongoDB.
"""
import pytest
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
os.environ.setdefault("MONGO_URI", "mongodb://localhost:27017")
os.environ.setdefault("MONGO_DB", "attendance_test_attendance")


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


def _create_emp(client, code="EMP0001", shift_start="09:30", shift_end="18:30", joined="2026-01-05"):
    resp = client.post("/employees", json={
        "emp_code": code, "name": f"User {code}", "email": f"{code.lower()}@test.com",
        "department": "Engineering", "shift_start": shift_start, "shift_end": shift_end,
        "joined_on": joined,
    })
    assert resp.status_code == 201
    return resp.json()


class TestPunchIn:
    def test_basic_punch_in(self, client):
        _create_emp(client)
        # 09:28 IST = 03:58 UTC July 6 = 1783310280000 ms
        resp = client.post("/attendance/punch-in", json={
            "emp_code": "EMP0001",
            "punched_at": 1783310280000,  # 2026-07-06T03:58:00Z
        })
        assert resp.status_code == 201
        data = resp.json()
        assert data["emp_code"] == "EMP0001"
        assert data["date"] == "2026-07-06"
        assert data["status"] == "PRESENT"
        assert data["punch_in"] == 1783310280000
        assert data["punch_out"] is None
        assert data["work_hours"] is None
        assert data["late_minutes"] == 0
        assert data["overtime_minutes"] == 0
        assert data["half_day"] == False
        assert data["history"] == []
        assert "_id" not in data
        assert "id" not in data

    def test_late_punch_in(self, client):
        _create_emp(client)
        # 10:05 IST = 04:35 UTC = 1783312500000
        resp = client.post("/attendance/punch-in", json={
            "emp_code": "EMP0001",
            "punched_at": 1783312500000,
        })
        assert resp.status_code == 201
        data = resp.json()
        assert data["late_minutes"] == 35

    def test_duplicate_same_date_409(self, client):
        _create_emp(client)
        client.post("/attendance/punch-in", json={
            "emp_code": "EMP0001", "punched_at": 1783310280000,
        })
        resp = client.post("/attendance/punch-in", json={
            "emp_code": "EMP0001", "punched_at": 1783312500000,  # same day
        })
        assert resp.status_code == 409

    def test_unknown_employee_404(self, client):
        resp = client.post("/attendance/punch-in", json={
            "emp_code": "EMP9999",
        })
        assert resp.status_code == 404

    def test_wfh_status(self, client):
        _create_emp(client)
        resp = client.post("/attendance/punch-in", json={
            "emp_code": "EMP0001", "status": "WFH",
            "punched_at": 1783310280000,
        })
        assert resp.status_code == 201
        assert resp.json()["status"] == "WFH"

    def test_on_duty_status(self, client):
        _create_emp(client)
        resp = client.post("/attendance/punch-in", json={
            "emp_code": "EMP0001", "status": "ON_DUTY",
            "punched_at": 1783310280000,
        })
        assert resp.status_code == 201
        assert resp.json()["status"] == "ON_DUTY"

    def test_overnight_shift_punch_in(self, client):
        """Overnight 22:00-06:00, punch at 21:55 IST July 6 → date July 6."""
        _create_emp(client, "EMP0005", shift_start="22:00", shift_end="06:00")
        # 21:55 IST July 6 = 16:25 UTC July 6
        # epoch: datetime(2026,7,6,16,25,0,UTC).timestamp() * 1000
        # = 1783354700 * 1000 = ... let's compute:
        # 2026-07-06T16:25:00Z
        from datetime import datetime, timezone
        ts = int(datetime(2026, 7, 6, 16, 25, 0, tzinfo=timezone.utc).timestamp() * 1000)
        resp = client.post("/attendance/punch-in", json={
            "emp_code": "EMP0005", "punched_at": ts,
        })
        assert resp.status_code == 201
        data = resp.json()
        assert data["date"] == "2026-07-06"
        assert data["late_minutes"] == 0  # 5 min early


class TestPunchOut:
    def test_basic_punch_out(self, client):
        _create_emp(client)
        # Punch in at 09:28 IST = 03:58 UTC
        client.post("/attendance/punch-in", json={
            "emp_code": "EMP0001", "punched_at": 1783310280000,
        })
        # Punch out at 18:35 IST = 13:05 UTC
        # 2026-07-06T13:05:00Z
        from datetime import datetime, timezone
        ts_out = int(datetime(2026, 7, 6, 13, 5, 0, tzinfo=timezone.utc).timestamp() * 1000)
        resp = client.post("/attendance/punch-out", json={
            "emp_code": "EMP0001", "punched_at": ts_out,
        })
        assert resp.status_code == 200
        data = resp.json()
        assert data["work_hours"] == 9.12
        assert data["overtime_minutes"] == 0
        assert data["half_day"] == False

    def test_unknown_employee_404(self, client):
        resp = client.post("/attendance/punch-out", json={
            "emp_code": "EMP9999", "punched_at": 1783312500000,
        })
        assert resp.status_code == 404

    def test_no_punch_in_404(self, client):
        _create_emp(client)
        resp = client.post("/attendance/punch-out", json={
            "emp_code": "EMP0001", "punched_at": 1783312500000,
        })
        assert resp.status_code == 404

    def test_punch_out_before_punch_in_422(self, client):
        _create_emp(client)
        # Punch in
        client.post("/attendance/punch-in", json={
            "emp_code": "EMP0001", "punched_at": 1783312500000,
        })
        # Punch out BEFORE punch in
        resp = client.post("/attendance/punch-out", json={
            "emp_code": "EMP0001", "punched_at": 1783310280000,
        })
        assert resp.status_code == 422

    def test_double_punch_out_409(self, client):
        _create_emp(client)
        client.post("/attendance/punch-in", json={
            "emp_code": "EMP0001", "punched_at": 1783310280000,
        })
        from datetime import datetime, timezone
        ts_out = int(datetime(2026, 7, 6, 13, 5, 0, tzinfo=timezone.utc).timestamp() * 1000)
        client.post("/attendance/punch-out", json={
            "emp_code": "EMP0001", "punched_at": ts_out,
        })
        # Second punch-out
        ts_out2 = int(datetime(2026, 7, 6, 14, 0, 0, tzinfo=timezone.utc).timestamp() * 1000)
        resp = client.post("/attendance/punch-out", json={
            "emp_code": "EMP0001", "punched_at": ts_out2,
        })
        assert resp.status_code == 409

    def test_overtime_calculation(self, client):
        """19:10 IST with 18:30 shift end → 40 min overtime."""
        _create_emp(client)
        # Punch in at 10:05 IST = 04:35 UTC
        client.post("/attendance/punch-in", json={
            "emp_code": "EMP0001", "punched_at": 1783312500000,
        })
        # Punch out at 19:10 IST = 13:40 UTC
        from datetime import datetime, timezone
        ts_out = int(datetime(2026, 7, 6, 13, 40, 0, tzinfo=timezone.utc).timestamp() * 1000)
        resp = client.post("/attendance/punch-out", json={
            "emp_code": "EMP0001", "punched_at": ts_out,
        })
        assert resp.status_code == 200
        data = resp.json()
        assert data["overtime_minutes"] == 40
        assert data["work_hours"] == 9.08

    def test_half_day_detection(self, client):
        """3.5 hours worked → half_day true."""
        _create_emp(client)
        from datetime import datetime, timezone
        ts_in = int(datetime(2026, 7, 7, 4, 0, 0, tzinfo=timezone.utc).timestamp() * 1000)
        ts_out = int(datetime(2026, 7, 7, 7, 30, 0, tzinfo=timezone.utc).timestamp() * 1000)
        client.post("/attendance/punch-in", json={
            "emp_code": "EMP0001", "punched_at": ts_in,
        })
        resp = client.post("/attendance/punch-out", json={
            "emp_code": "EMP0001", "punched_at": ts_out,
        })
        assert resp.status_code == 200
        data = resp.json()
        assert data["work_hours"] == 3.5
        assert data["half_day"] == True

    def test_equal_shift_punch_in_and_punch_out(self, client):
        """Equal shift (09:30-09:30): 24h shift treated as overnight. Verify date attribution, hours, and 24h limit."""
        _create_emp(client, "EMP0099", shift_start="09:30", shift_end="09:30")
        from datetime import datetime, timezone
        # Punch in at 09:30 IST July 6 = 04:00 UTC July 6
        ts_in = int(datetime(2026, 7, 6, 4, 0, 0, tzinfo=timezone.utc).timestamp() * 1000)
        # Punch out at 09:30 IST July 7 = 04:00 UTC July 7 (exactly 24h later)
        ts_out = int(datetime(2026, 7, 7, 4, 0, 0, tzinfo=timezone.utc).timestamp() * 1000)
        # Punch out > 24h later (09:30:01 IST July 7)
        ts_out_too_late = ts_out + 1000

        resp_in = client.post("/attendance/punch-in", json={
            "emp_code": "EMP0099", "punched_at": ts_in,
        })
        assert resp_in.status_code == 201
        data_in = resp_in.json()
        assert data_in["date"] == "2026-07-06"
        assert data_in["late_minutes"] == 0

        # Punch out after >24h should return 422
        resp_too_late = client.post("/attendance/punch-out", json={
            "emp_code": "EMP0099", "punched_at": ts_out_too_late,
        })
        assert resp_too_late.status_code == 422

        # Valid punch out at 24h boundary returns 200
        resp_out = client.post("/attendance/punch-out", json={
            "emp_code": "EMP0099", "punched_at": ts_out,
        })
        assert resp_out.status_code == 200
        data_out = resp_out.json()
        assert data_out["date"] == "2026-07-06"
        assert data_out["work_hours"] == 24.0
        assert data_out["overtime_minutes"] == 0
        assert data_out["half_day"] == False


class TestListAttendance:
    def test_sorted_date_desc_empcode_asc(self, client):
        _create_emp(client, "EMP0001")
        _create_emp(client, "EMP0002")
        from datetime import datetime, timezone

        # Two records same day, different employees
        ts1 = int(datetime(2026, 7, 6, 4, 0, 0, tzinfo=timezone.utc).timestamp() * 1000)
        ts2 = int(datetime(2026, 7, 7, 4, 0, 0, tzinfo=timezone.utc).timestamp() * 1000)

        client.post("/attendance/punch-in", json={"emp_code": "EMP0002", "punched_at": ts1})
        client.post("/attendance/punch-in", json={"emp_code": "EMP0001", "punched_at": ts1})
        client.post("/attendance/punch-in", json={"emp_code": "EMP0001", "punched_at": ts2})

        resp = client.get("/attendance")
        items = resp.json()["items"]
        # Should be: EMP0001 July 7, then EMP0001 July 6, EMP0002 July 6
        assert items[0]["date"] == "2026-07-07"
        assert items[0]["emp_code"] == "EMP0001"
        assert items[1]["date"] == "2026-07-06"
        assert items[1]["emp_code"] == "EMP0001"
        assert items[2]["date"] == "2026-07-06"
        assert items[2]["emp_code"] == "EMP0002"

    def test_no_id_leak(self, client):
        _create_emp(client)
        from datetime import datetime, timezone
        ts = int(datetime(2026, 7, 6, 4, 0, 0, tzinfo=timezone.utc).timestamp() * 1000)
        client.post("/attendance/punch-in", json={"emp_code": "EMP0001", "punched_at": ts})
        resp = client.get("/attendance")
        for item in resp.json()["items"]:
            assert "_id" not in item
            assert "id" not in item

    def test_punch_times_as_epoch_ms(self, client):
        _create_emp(client)
        ts = 1783310280000
        client.post("/attendance/punch-in", json={"emp_code": "EMP0001", "punched_at": ts})
        resp = client.get("/attendance")
        item = resp.json()["items"][0]
        assert item["punch_in"] == ts
        assert isinstance(item["punch_in"], int)

    def test_filter_by_status(self, client):
        _create_emp(client, "EMP0001")
        _create_emp(client, "EMP0002")
        from datetime import datetime, timezone
        ts1 = int(datetime(2026, 7, 6, 4, 0, 0, tzinfo=timezone.utc).timestamp() * 1000)
        ts2 = int(datetime(2026, 7, 6, 4, 0, 0, tzinfo=timezone.utc).timestamp() * 1000)
        client.post("/attendance/punch-in", json={"emp_code": "EMP0001", "punched_at": ts1, "status": "PRESENT"})
        client.post("/attendance/punch-in", json={"emp_code": "EMP0002", "punched_at": ts2, "status": "WFH"})
        resp = client.get("/attendance?status=WFH")
        assert resp.json()["total"] == 1
        assert resp.json()["items"][0]["status"] == "WFH"

    def test_legacy_fields_defaulted(self, client):
        """Insert a legacy doc without history/half_day — API should default them."""
        from app.main import attendance_col, employees_col
        from datetime import datetime, timezone
        employees_col.insert_one({
            "emp_code": "EMP0099", "name": "Legacy", "email": "l@t.com",
            "department": "D", "shift_start": "09:30", "shift_end": "18:30",
            "joined_on": "2026-01-01",
            "created_at": datetime(2026, 1, 1, tzinfo=timezone.utc),
        })
        # Insert attendance without history, half_day, late_minutes, overtime_minutes
        attendance_col.insert_one({
            "emp_code": "EMP0099", "date": "2026-07-14", "status": "PRESENT",
            "punch_in": datetime(2026, 7, 14, 4, 2, 0, tzinfo=timezone.utc),
            "punch_out": datetime(2026, 7, 14, 13, 1, 0, tzinfo=timezone.utc),
            "work_hours": 8.98,
        })
        resp = client.get("/attendance?emp_code=EMP0099")
        item = resp.json()["items"][0]
        assert item["history"] == []
        assert item["half_day"] == False
        assert item["late_minutes"] == 0
        assert item["overtime_minutes"] == 0
