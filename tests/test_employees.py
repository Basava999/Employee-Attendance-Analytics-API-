"""
Employee CRUD endpoint tests.
Requires MongoDB.
"""
import pytest
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
os.environ.setdefault("MONGO_URI", "mongodb://localhost:27017")
os.environ.setdefault("MONGO_DB", "attendance_test_employees")


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


class TestCreateEmployee:
    def test_create_success(self, client):
        resp = client.post("/employees", json={
            "emp_code": "EMP0001", "name": "Asha Rao", "email": "asha@example.com",
            "department": "Engineering", "joined_on": "2026-01-05",
        })
        assert resp.status_code == 201
        data = resp.json()
        assert data["emp_code"] == "EMP0001"
        assert data["name"] == "Asha Rao"
        assert data["shift_start"] == "09:30"  # default
        assert data["shift_end"] == "18:30"  # default
        assert isinstance(data["created_at"], int)
        assert data["created_at"] >= 100000000000  # epoch ms
        # No _id or id
        assert "_id" not in data
        assert "id" not in data

    def test_duplicate_409(self, client):
        client.post("/employees", json={
            "emp_code": "EMP0001", "name": "X", "email": "x@y.com",
            "department": "D", "joined_on": "2026-01-05",
        })
        resp = client.post("/employees", json={
            "emp_code": "EMP0001", "name": "Y", "email": "y@y.com",
            "department": "D", "joined_on": "2026-01-05",
        })
        assert resp.status_code == 409

    def test_overnight_shift_allowed(self, client):
        resp = client.post("/employees", json={
            "emp_code": "EMP0005", "name": "Night", "email": "n@y.com",
            "department": "D", "shift_start": "22:00", "shift_end": "06:00",
            "joined_on": "2026-01-05",
        })
        assert resp.status_code == 201

    def test_derived_fields_ignored(self, client):
        """Extra fields like work_hours should be silently ignored, not rejected."""
        resp = client.post("/employees", json={
            "emp_code": "EMP0002", "name": "X", "email": "x@y.com",
            "department": "D", "joined_on": "2026-01-05",
            "work_hours": 8.0,  # should be ignored
        })
        assert resp.status_code == 201


class TestListEmployees:
    def test_empty(self, client):
        resp = client.get("/employees")
        assert resp.status_code == 200
        data = resp.json()
        assert data["items"] == []
        assert data["total"] == 0
        assert data["page"] == 1
        assert data["page_size"] == 20

    def test_sorted_by_emp_code(self, client):
        for code in ["EMP0003", "EMP0001", "EMP0002"]:
            client.post("/employees", json={
                "emp_code": code, "name": f"E{code}", "email": f"{code}@y.com",
                "department": "D", "joined_on": "2026-01-05",
            })
        resp = client.get("/employees")
        codes = [e["emp_code"] for e in resp.json()["items"]]
        assert codes == ["EMP0001", "EMP0002", "EMP0003"]

    def test_department_filter(self, client):
        client.post("/employees", json={
            "emp_code": "EMP0001", "name": "A", "email": "a@y.com",
            "department": "Engineering", "joined_on": "2026-01-05",
        })
        client.post("/employees", json={
            "emp_code": "EMP0002", "name": "B", "email": "b@y.com",
            "department": "Sales", "joined_on": "2026-01-05",
        })
        resp = client.get("/employees?department=Sales")
        data = resp.json()
        assert data["total"] == 1
        assert data["items"][0]["emp_code"] == "EMP0002"

    def test_total_reflects_filter(self, client):
        """total must reflect the department filter, not the whole collection."""
        for i, dept in enumerate(["Engineering", "Engineering", "Sales"], 1):
            client.post("/employees", json={
                "emp_code": f"EMP000{i}", "name": f"E{i}", "email": f"e{i}@y.com",
                "department": dept, "joined_on": "2026-01-05",
            })
        resp = client.get("/employees?department=Engineering")
        assert resp.json()["total"] == 2

    def test_pagination(self, client):
        for i in range(1, 6):
            client.post("/employees", json={
                "emp_code": f"EMP000{i}", "name": f"E{i}", "email": f"e{i}@y.com",
                "department": "D", "joined_on": "2026-01-05",
            })
        # Page 1, size 2
        resp = client.get("/employees?page=1&page_size=2")
        data = resp.json()
        assert len(data["items"]) == 2
        assert data["total"] == 5
        assert data["items"][0]["emp_code"] == "EMP0001"

        # Page 2, size 2
        resp = client.get("/employees?page=2&page_size=2")
        data = resp.json()
        assert len(data["items"]) == 2
        assert data["items"][0]["emp_code"] == "EMP0003"

    def test_no_id_in_response(self, client):
        client.post("/employees", json={
            "emp_code": "EMP0001", "name": "A", "email": "a@y.com",
            "department": "D", "joined_on": "2026-01-05",
        })
        resp = client.get("/employees")
        for item in resp.json()["items"]:
            assert "_id" not in item
            assert "id" not in item

    def test_created_at_is_epoch_ms(self, client):
        client.post("/employees", json={
            "emp_code": "EMP0001", "name": "A", "email": "a@y.com",
            "department": "D", "joined_on": "2026-01-05",
        })
        resp = client.get("/employees")
        item = resp.json()["items"][0]
        assert isinstance(item["created_at"], int)
        assert item["created_at"] >= 100000000000
