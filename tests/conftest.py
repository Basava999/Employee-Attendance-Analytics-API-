"""
Pytest configuration and shared fixtures.
Uses a dedicated test database that is dropped on teardown.
"""
import os
import random
import string
import pytest
from unittest.mock import patch


def generate_test_db_name():
    suffix = "".join(random.choices(string.ascii_lowercase + string.digits, k=8))
    return f"attendance_test_{suffix}"


@pytest.fixture(scope="session")
def test_db_name():
    return generate_test_db_name()


@pytest.fixture(scope="session", autouse=True)
def set_test_env(test_db_name):
    """Set environment variables before importing the app."""
    mongo_uri = os.getenv("MONGO_URI", "mongodb://localhost:27017")
    os.environ["MONGO_URI"] = mongo_uri
    os.environ["MONGO_DB"] = test_db_name
    yield
    # Cleanup: drop the test database
    try:
        from pymongo import MongoClient
        client = MongoClient(mongo_uri, serverSelectionTimeoutMS=3000)
        client.drop_database(test_db_name)
        client.close()
    except Exception:
        pass


@pytest.fixture(scope="session")
def app_client(set_test_env):
    """Create a TestClient for the FastAPI app."""
    try:
        from pymongo import MongoClient
        client = MongoClient(
            os.environ["MONGO_URI"],
            serverSelectionTimeoutMS=3000,
        )
        client.admin.command("ping")
        client.close()
    except Exception:
        pytest.skip("MongoDB not available")

    from fastapi.testclient import TestClient
    from app.main import app
    with TestClient(app) as client:
        yield client


@pytest.fixture(autouse=True)
def clean_collections(request):
    """Clean test collections before each test that uses app_client."""
    if "app_client" in request.fixturenames:
        request.getfixturevalue("app_client")
        try:
            from app.main import employees_col, attendance_col
            employees_col.delete_many({})
            attendance_col.delete_many({})
        except Exception:
            pass
    yield


def create_test_employee(app_client, emp_code="EMP0001", **overrides):
    """Helper to create a test employee."""
    data = {
        "emp_code": emp_code,
        "name": overrides.get("name", "Test User"),
        "email": overrides.get("email", f"{emp_code.lower()}@test.com"),
        "department": overrides.get("department", "Engineering"),
        "shift_start": overrides.get("shift_start", "09:30"),
        "shift_end": overrides.get("shift_end", "18:30"),
        "joined_on": overrides.get("joined_on", "2026-01-05"),
    }
    resp = app_client.post("/employees", json=data)
    assert resp.status_code == 201, f"Failed to create employee: {resp.json()}"
    return resp.json()
