"""
Analytics endpoint tests.
Tests:
- GET /analytics/employees/{emp_code}/monthly
- GET /analytics/departments/summary
- GET /analytics/leaderboard/late
- GET /analytics/departments/{department}/trend
"""
import pytest
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
os.environ.setdefault("MONGO_URI", "mongodb://localhost:27017")
os.environ.setdefault("MONGO_DB", "attendance_test_analytics")


class TestEmployeeMonthly:
    """Test GET /analytics/employees/{emp_code}/monthly."""

    def test_unknown_employee_404(self, app_client):
        resp = app_client.get("/analytics/employees/EMP9999/monthly?month=2026-07")
        assert resp.status_code == 404
        assert "not found" in resp.json()["detail"].lower()

    def test_invalid_month_format_422(self, app_client):
        resp = app_client.get("/analytics/employees/EMP0001/monthly?month=2026-13")
        assert resp.status_code == 422

    def test_monthly_summary_calculation(self, app_client):
        # Create employee who joined before July 2026
        app_client.post("/employees", json={
            "emp_code": "EMP0001",
            "name": "Arun Kumar",
            "email": "arun@example.com",
            "department": "Engineering",
            "shift_start": "09:30",
            "shift_end": "18:30",
            "joined_on": "2026-01-05",
        })

        # Punch in and punch out for 2026-07-06 (Mon) - Full day (9.00 hrs)
        app_client.post("/attendance/punch-in", json={
            "emp_code": "EMP0001",
            "punched_at": 1783310280000,  # 09:28 IST (0 late)
            "status": "PRESENT",
        })
        app_client.post("/attendance/punch-out", json={
            "emp_code": "EMP0001",
            "punched_at": 1783342680000,  # 18:28 IST (9.00 hrs, not half day, 0 OT)
        })

        # Punch in and punch out for 2026-07-07 (Tue) - Half day (4.00 hrs)
        app_client.post("/attendance/punch-in", json={
            "emp_code": "EMP0001",
            "punched_at": 1783396680000,  # 09:28 IST
            "status": "PRESENT",
        })
        app_client.post("/attendance/punch-out", json={
            "emp_code": "EMP0001",
            "punched_at": 1783411080000,  # 13:28 IST (4.00 hrs < 4.50 -> half day)
        })

        # Punch in for 2026-07-08 (Wed) - Late punch in (10:15 IST -> 45 min late)
        app_client.post("/attendance/punch-in", json={
            "emp_code": "EMP0001",
            "punched_at": 1783485900000,  # 10:15 IST (45 min late)
            "status": "PRESENT",
        })
        app_client.post("/attendance/punch-out", json={
            "emp_code": "EMP0001",
            "punched_at": 1783518300000,  # 19:15 IST (OT >= 30 min -> 45 min OT)
        })

        resp = app_client.get("/analytics/employees/EMP0001/monthly?month=2026-07")
        assert resp.status_code == 200
        data = resp.json()

        assert data["emp_code"] == "EMP0001"
        assert data["month"] == "2026-07"
        assert data["working_days"] == 23  # July 2026 has 23 weekdays
        # 1 full day (1.0) + 1 half day (0.5) + 1 full day (1.0) = 2.5
        assert data["present_days"] == 2.5
        assert data["late_count"] == 1
        assert data["total_late_minutes"] == 45
        assert data["total_overtime_minutes"] == 45
        # Attendance % = 2.5 / 23 * 100 = 10.8695... -> 10.87 (half-up)
        assert data["attendance_pct"] == 10.87

    def test_mid_month_joiner_working_days(self, app_client):
        # Joined mid-month: July 13, 2026 (Mon). Working days: 15
        app_client.post("/employees", json={
            "emp_code": "EMP0002",
            "name": "Bhavna Patel",
            "email": "bhavna@example.com",
            "department": "Design",
            "shift_start": "09:30",
            "shift_end": "18:30",
            "joined_on": "2026-07-13",
        })

        resp = app_client.get("/analytics/employees/EMP0002/monthly?month=2026-07")
        assert resp.status_code == 200
        data = resp.json()
        assert data["working_days"] == 15
        assert data["present_days"] == 0
        assert data["attendance_pct"] == 0.0

    def test_joined_after_month_zero_working_days_null_pct(self, app_client):
        # Joined Aug 1, 2026. For July 2026: 0 working days -> attendance_pct is null
        app_client.post("/employees", json={
            "emp_code": "EMP0003",
            "name": "Chirag Shah",
            "email": "chirag@example.com",
            "department": "QA",
            "shift_start": "09:30",
            "shift_end": "18:30",
            "joined_on": "2026-08-01",
        })

        resp = app_client.get("/analytics/employees/EMP0003/monthly?month=2026-07")
        assert resp.status_code == 200
        data = resp.json()
        assert data["working_days"] == 0
        assert data["attendance_pct"] is None


class TestDepartmentSummary:
    """Test GET /analytics/departments/summary."""

    def test_headcount_includes_employees_with_zero_logs(self, app_client):
        # Create 2 employees in Sales, but only 1 has attendance logs
        app_client.post("/employees", json={
            "emp_code": "EMP0010",
            "name": "Sales Rep 1",
            "email": "rep1@example.com",
            "department": "Sales",
            "shift_start": "09:30",
            "shift_end": "18:30",
            "joined_on": "2026-01-01",
        })
        app_client.post("/employees", json={
            "emp_code": "EMP0011",
            "name": "Sales Rep 2",
            "email": "rep2@example.com",
            "department": "Sales",
            "shift_start": "09:30",
            "shift_end": "18:30",
            "joined_on": "2026-01-01",
        })

        # Only EMP0010 logs attendance
        app_client.post("/attendance/punch-in", json={
            "emp_code": "EMP0010",
            "punched_at": 1783310280000,
            "status": "PRESENT",
        })
        app_client.post("/attendance/punch-out", json={
            "emp_code": "EMP0010",
            "punched_at": 1783342680000,
        })

        resp = app_client.get("/analytics/departments/summary?month=2026-07&department=Sales")
        assert resp.status_code == 200
        data = resp.json()
        assert len(data["items"]) == 1
        sales = data["items"][0]
        assert sales["department"] == "Sales"
        assert sales["headcount"] == 2  # Both reps count in headcount!
        assert sales["present_days"] == 1.0
        assert sales["avg_work_hours"] == 9.0

    def test_future_joiners_excluded_from_headcount(self, app_client):
        # Create employee joining in August 2026 in HR
        app_client.post("/employees", json={
            "emp_code": "EMP0020",
            "name": "Future HR",
            "email": "hr@example.com",
            "department": "HR",
            "shift_start": "09:30",
            "shift_end": "18:30",
            "joined_on": "2026-08-01",
        })

        resp = app_client.get("/analytics/departments/summary?month=2026-07&department=HR")
        assert resp.status_code == 200
        data = resp.json()
        # Headcount is 0 -> department is omitted per R9/contract
        assert len(data["items"]) == 0

    def test_department_summary_sorting(self, app_client):
        # Create employees in different departments
        for dept in ["Operations", "Finance"]:
            app_client.post("/employees", json={
                "emp_code": f"EMP{dept[:4].upper()}",
                "name": f"User {dept}",
                "email": f"{dept.lower()}@example.com",
                "department": dept,
                "shift_start": "09:30",
                "shift_end": "18:30",
                "joined_on": "2026-01-01",
            })

        resp = app_client.get("/analytics/departments/summary?month=2026-07")
        assert resp.status_code == 200
        items = resp.json()["items"]
        departments = [item["department"] for item in items]
        assert departments == sorted(departments)


class TestLateLeaderboard:
    """Test GET /analytics/leaderboard/late."""

    def test_competition_ranking_with_ties(self, app_client):
        # Create 4 employees
        for i, code in enumerate(["EMP0031", "EMP0032", "EMP0033", "EMP0034"]):
            app_client.post("/employees", json={
                "emp_code": code,
                "name": f"User {code}",
                "email": f"{code.lower()}@example.com",
                "department": "Support",
                "shift_start": "09:30",
                "shift_end": "18:30",
                "joined_on": "2026-01-01",
            })

        # EMP0031: 58 mins late
        app_client.post("/attendance/punch-in", json={
            "emp_code": "EMP0031",
            "punched_at": 1783313880000,  # 10:28 IST -> 58 min elapsed > 10 grace -> 58 mins late
            "status": "PRESENT",
        })

        # EMP0032: 30 mins late (tie with EMP0033)
        app_client.post("/attendance/punch-in", json={
            "emp_code": "EMP0032",
            "punched_at": 1783312200000,  # 10:00 IST (30 mins late)
            "status": "PRESENT",
        })

        # EMP0033: 30 mins late (tie with EMP0032)
        app_client.post("/attendance/punch-in", json={
            "emp_code": "EMP0033",
            "punched_at": 1783312200000,  # 10:00 IST (30 mins late)
            "status": "PRESENT",
        })

        # EMP0034: 0 mins late (on time) -> Should be EXCLUDED from leaderboard
        app_client.post("/attendance/punch-in", json={
            "emp_code": "EMP0034",
            "punched_at": 1783310400000,  # 09:30 IST (0 late)
            "status": "PRESENT",
        })

        resp = app_client.get("/analytics/leaderboard/late?month=2026-07&department=Support")
        assert resp.status_code == 200
        items = resp.json()["items"]

        # Only 3 employees should appear (EMP0034 has 0 late minutes)
        assert len(items) == 3

        # Rank 1: EMP0031 (58 mins)
        assert items[0]["emp_code"] == "EMP0031"
        assert items[0]["rank"] == 1

        # Rank 2 & 2: EMP0032 and EMP0033 tied!
        assert items[1]["rank"] == 2
        assert items[2]["rank"] == 2
        assert items[1]["total_late_minutes"] == items[2]["total_late_minutes"] == 30
        # Tie break display: emp_code ascending
        assert items[1]["emp_code"] < items[2]["emp_code"]

    def test_limit_applies_after_ranking(self, app_client):
        # When limit=2 and ranks are 1, 2, 2, all 3 are returned because rank <= 2
        resp = app_client.get("/analytics/leaderboard/late?month=2026-07&department=Support&limit=2")
        assert resp.status_code == 200
        items = resp.json()["items"]
        assert len(items) == 3  # Both rank 2 employees are returned!


class TestDepartmentTrend:
    """Test GET /analytics/departments/{department}/trend."""

    def test_unknown_department_404(self, app_client):
        resp = app_client.get("/analytics/departments/NonExistent/trend?from=2026-07-01&to=2026-07-07")
        assert resp.status_code == 404

    def test_reversed_dates_422(self, app_client):
        resp = app_client.get("/analytics/departments/Engineering/trend?from=2026-07-07&to=2026-07-01")
        assert resp.status_code == 422

    def test_range_over_92_days_422(self, app_client):
        resp = app_client.get("/analytics/departments/Engineering/trend?from=2026-01-01&to=2026-04-15")
        assert resp.status_code == 422

    def test_exact_daily_gap_filling_and_rates(self, app_client):
        # Create an employee in Tech
        app_client.post("/employees", json={
            "emp_code": "EMP0050",
            "name": "Tech Lead",
            "email": "lead@example.com",
            "department": "Tech",
            "shift_start": "09:30",
            "shift_end": "18:30",
            "joined_on": "2026-07-01",
        })

        # One record on July 01 (Wed)
        app_client.post("/attendance/punch-in", json={
            "emp_code": "EMP0050",
            "punched_at": 1782878280000,  # 2026-07-01 09:28 IST
            "status": "PRESENT",
        })
        app_client.post("/attendance/punch-out", json={
            "emp_code": "EMP0050",
            "punched_at": 1782910680000,
        })

        # Request 7 days: July 1 to July 7
        resp = app_client.get("/analytics/departments/Tech/trend?from=2026-07-01&to=2026-07-07")
        assert resp.status_code == 200
        data = resp.json()
        assert data["department"] == "Tech"
        items = data["items"]

        # Exactly 7 rows returned (1 per day, gaps filled)
        assert len(items) == 7
        dates = [item["date"] for item in items]
        assert dates == [
            "2026-07-01", "2026-07-02", "2026-07-03",
            "2026-07-04", "2026-07-05", "2026-07-06", "2026-07-07",
        ]

        # Day 1: Wednesday (working day) - present_count=1.0, headcount=1, rate=1.0000
        assert items[0]["is_working_day"] is True
        assert items[0]["headcount"] == 1
        assert items[0]["present_count"] == 1.0
        assert items[0]["attendance_rate"] == 1.0000

        # Day 4: Saturday (weekend) - rate must be None
        assert items[3]["is_working_day"] is False
        assert items[3]["attendance_rate"] is None

        # Day 2: Thursday (working day, no records) - rate must be 0.0000
        assert items[1]["is_working_day"] is True
        assert items[1]["present_count"] == 0
        assert items[1]["attendance_rate"] == 0.0000

        # Moving average 7d exists
        assert items[0]["moving_avg_7d"] == 1.0000
