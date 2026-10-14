"""
Concurrency tests for Employee Attendance & Analytics API.
Tests:
- N parallel duplicate creates -> exactly one 201, rest 409
- N parallel duplicate punch-ins -> exactly one 201, rest 409
- 2 parallel punch-outs -> exactly one 200, one 409
- Parallel regularizations -> no lost history, verified from stored document
"""
import concurrent.futures
import os
import sys
import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
os.environ.setdefault("MONGO_URI", "mongodb://localhost:27017")
os.environ.setdefault("MONGO_DB", "attendance_test_concurrency")


class TestConcurrency:
    """Test concurrent operations against the API."""

    def test_concurrent_employee_creation(self, app_client):
        """
        N parallel requests attempting to create the same emp_code.
        The database unique index must ensure exactly one 201 and the rest 409.
        """
        emp_code = "EMP9001"
        payload = {
            "emp_code": emp_code,
            "name": "Concurrent User",
            "email": "concurrent@example.com",
            "department": "Engineering",
            "shift_start": "09:30",
            "shift_end": "18:30",
            "joined_on": "2026-01-05",
        }

        concurrency = 10
        responses = []

        def do_create():
            return app_client.post("/employees", json=payload)

        with concurrent.futures.ThreadPoolExecutor(max_workers=concurrency) as executor:
            futures = [executor.submit(do_create) for _ in range(concurrency)]
            for f in concurrent.futures.as_completed(futures):
                responses.append(f.result())

        status_codes = [r.status_code for r in responses]
        success_count = status_codes.count(201)
        conflict_count = status_codes.count(409)

        assert success_count == 1, f"Expected exactly 1 201, got {success_count}. Codes: {status_codes}"
        assert conflict_count == concurrency - 1, f"Expected {concurrency - 1} 409s, got {conflict_count}"

    def test_concurrent_punch_in(self, app_client):
        """
        N parallel punch-ins for the same employee on the same date.
        Exactly one 201, rest 409.
        """
        emp_code = "EMP9002"
        # Create employee
        app_client.post("/employees", json={
            "emp_code": emp_code,
            "name": "Punch In Racer",
            "email": "racer@example.com",
            "department": "Engineering",
            "shift_start": "09:30",
            "shift_end": "18:30",
            "joined_on": "2026-01-05",
        })

        # Punch in instant for 2026-07-06
        payload = {
            "emp_code": emp_code,
            "punched_at": 1783310280000,
            "status": "PRESENT",
        }

        concurrency = 8
        responses = []

        def do_punch_in():
            return app_client.post("/attendance/punch-in", json=payload)

        with concurrent.futures.ThreadPoolExecutor(max_workers=concurrency) as executor:
            futures = [executor.submit(do_punch_in) for _ in range(concurrency)]
            for f in concurrent.futures.as_completed(futures):
                responses.append(f.result())

        status_codes = [r.status_code for r in responses]
        success_count = status_codes.count(201)
        conflict_count = status_codes.count(409)

        assert success_count == 1, f"Expected exactly 1 201, got {success_count}. Codes: {status_codes}"
        assert conflict_count == concurrency - 1, f"Expected {concurrency - 1} 409s, got {conflict_count}"

    def test_concurrent_punch_out(self, app_client):
        """
        Two simultaneous punch-outs against the same open record.
        Exactly one 200, one 409.
        """
        emp_code = "EMP9003"
        # Create employee
        app_client.post("/employees", json={
            "emp_code": emp_code,
            "name": "Punch Out Racer",
            "email": "outracer@example.com",
            "department": "Engineering",
            "shift_start": "09:30",
            "shift_end": "18:30",
            "joined_on": "2026-01-05",
        })

        # Punch in
        in_resp = app_client.post("/attendance/punch-in", json={
            "emp_code": emp_code,
            "punched_at": 1783310280000,
            "status": "PRESENT",
        })
        assert in_resp.status_code == 201

        # Two simultaneous punch-outs
        payload = {
            "emp_code": emp_code,
            "punched_at": 1783342680000,
        }

        responses = []

        def do_punch_out():
            return app_client.post("/attendance/punch-out", json=payload)

        with concurrent.futures.ThreadPoolExecutor(max_workers=2) as executor:
            futures = [executor.submit(do_punch_out) for _ in range(2)]
            for f in concurrent.futures.as_completed(futures):
                responses.append(f.result())

        status_codes = [r.status_code for r in responses]
        assert status_codes.count(200) == 1, f"Expected 1 200, got: {status_codes}"
        assert status_codes.count(409) == 1, f"Expected 1 409, got: {status_codes}"

    def test_concurrent_regularization(self, app_client):
        """
        Parallel regularization requests on the same record.
        Optimistic concurrency control must prevent lost updates:
        history count in the stored document must equal the number of 200 responses.
        """
        emp_code = "EMP9004"
        app_client.post("/employees", json={
            "emp_code": emp_code,
            "name": "Regularize Racer",
            "email": "regracer@example.com",
            "department": "Engineering",
            "shift_start": "09:30",
            "shift_end": "18:30",
            "joined_on": "2026-01-05",
        })

        app_client.post("/attendance/punch-in", json={
            "emp_code": emp_code,
            "punched_at": 1783310280000,
            "status": "PRESENT",
        })
        app_client.post("/attendance/punch-out", json={
            "emp_code": emp_code,
            "punched_at": 1783342680000,
        })

        # Send concurrent PATCH requests with different notes/times
        payloads = [
            {
                "punch_out": 1783342680000 + (i + 1) * 60000,
                "reason": f"Correction reason number {i + 1}",
                "regularized_by": f"HR_Manager_{i + 1}",
            }
            for i in range(5)
        ]

        responses = []

        def do_patch(p):
            return app_client.patch(f"/attendance/{emp_code}/2026-07-06", json=p)

        with concurrent.futures.ThreadPoolExecutor(max_workers=5) as executor:
            futures = [executor.submit(do_patch, p) for p in payloads]
            for f in concurrent.futures.as_completed(futures):
                responses.append(f.result())

        status_codes = [r.status_code for r in responses]
        # All responses should be either 200 (succeeded with retry) or 409 (conflict)
        for code in status_codes:
            assert code in (200, 409), f"Unexpected status code: {code}"

        successful_edits = status_codes.count(200)
        assert successful_edits >= 1

        # Verify the persisted document in DB
        get_resp = app_client.get(f"/attendance?emp_code={emp_code}")
        assert get_resp.status_code == 200
        records = get_resp.json()["items"]
        assert len(records) == 1
        record = records[0]

        # History entries must exactly match successful edits (no lost history)
        assert len(record["history"]) == successful_edits

    def test_concurrent_regularization_and_punch_out(self, app_client):
        """
        Concurrency test between regularize_attendance() and punch_out().
        Ensures neither operation can silently overwrite the other's state or derived values:
        - If punch-out succeeds first, regularization must either observe the updated punch_out
          or retry/conflict cleanly without clobbering the punch-out state.
        - If regularization succeeds first, punch-out must not overwrite an already regularized punch_out.
        """
        emp_code = "EMP9005"
        app_client.post("/employees", json={
            "emp_code": emp_code,
            "name": "Race Regularize Punchout",
            "email": "race_reg_out@example.com",
            "department": "Engineering",
            "shift_start": "09:30",
            "shift_end": "18:30",
            "joined_on": "2026-01-05",
        })

        # Punch in (creates open record)
        punch_in_ms = 1783310280000  # 2026-07-06 09:28 IST
        app_client.post("/attendance/punch-in", json={
            "emp_code": emp_code,
            "punched_at": punch_in_ms,
            "status": "PRESENT",
        })

        punch_out_payload = {
            "emp_code": emp_code,
            "punched_at": 1783342680000,  # 18:28 IST
        }
        regularize_payload = {
            "punch_out": 1783346280000,  # 19:28 IST
            "reason": "Correcting punch out time",
            "regularized_by": "HR_Manager",
        }

        # Fire simultaneously in threads
        def do_punch_out():
            return ("punch_out", app_client.post("/attendance/punch-out", json=punch_out_payload))

        def do_regularize():
            return ("regularize", app_client.patch(f"/attendance/{emp_code}/2026-07-06", json=regularize_payload))

        with concurrent.futures.ThreadPoolExecutor(max_workers=2) as executor:
            f1 = executor.submit(do_punch_out)
            f2 = executor.submit(do_regularize)
            results = dict([f1.result(), f2.result()])

        punch_out_res = results["punch_out"]
        regularize_res = results["regularize"]

        assert punch_out_res.status_code in (200, 409)
        assert regularize_res.status_code in (200, 409)

        # Check DB state
        get_resp = app_client.get(f"/attendance?emp_code={emp_code}")
        assert get_resp.status_code == 200
        records = get_resp.json()["items"]
        assert len(records) == 1
        record = records[0]

        # punch_out must be non-null and work_hours calculated
        assert record["punch_out"] is not None
        assert record["work_hours"] is not None
        if regularize_res.status_code == 200:
            assert len(record["history"]) >= 1
            assert any(h["by"] == "HR_Manager" for h in record["history"])
