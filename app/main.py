"""
Employee Attendance & Analytics API — Contract v2.0

Run:  uvicorn app.main:app --port 8000
Env:  MONGO_URI, MONGO_DB (a local .env is loaded for convenience; real env vars win)

All application code lives in this single file.
"""

import calendar
import logging
import os
import re
from contextlib import asynccontextmanager
from datetime import datetime, date, timedelta, timezone
from decimal import Decimal, ROUND_HALF_UP
from typing import Any, Optional

from bson import json_util
from dotenv import load_dotenv
from fastapi import FastAPI, HTTPException, Query, Path, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from pydantic import (
    BaseModel,
    StrictInt,
    field_validator,
    model_validator,
    ConfigDict,
)
from pymongo import MongoClient, ASCENDING, DESCENDING
from pymongo.errors import DuplicateKeyError, ConnectionFailure, ServerSelectionTimeoutError

# ─── configuration ────────────────────────────────────────────────────────────
load_dotenv()  # override=False by default — real env vars win

MONGO_URI = os.getenv("MONGO_URI", "mongodb://localhost:27017")
MONGO_DB = os.getenv("MONGO_DB", "attendance_db")

logger = logging.getLogger("attendance_api")
logging.basicConfig(level=logging.INFO)

# ─── constants & enums ────────────────────────────────────────────────────────
IST = timezone(timedelta(hours=5, minutes=30))
UTC = timezone.utc

PRESENCE_STATUSES = {"PRESENT", "WFH", "ON_DUTY"}
ALL_STATUSES = {"PRESENT", "ABSENT", "LEAVE", "WFH", "ON_DUTY"}
NON_PRESENCE_STATUSES = {"ABSENT", "LEAVE"}

EPOCH_MS_MIN = 100_000_000_000
EPOCH_MS_MAX = 4_102_444_800_000

GRACE_PERIOD_SECONDS = 600  # 10 minutes
OVERTIME_THRESHOLD_MINUTES = 30
HALF_DAY_THRESHOLD_HUNDREDTHS = 450  # 4.50 hours * 100

EXPLAIN_ENDPOINTS = {
    "attendance_list",
    "employee_monthly",
    "department_summary",
    "late_leaderboard",
    "department_trend",
}

EMP_CODE_PATTERN = re.compile(r"^EMP\d{4,6}$")
EMAIL_PATTERN = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")
SHIFT_TIME_PATTERN = re.compile(r"^([01]\d|2[0-3]):[0-5]\d$")
MONTH_PATTERN = re.compile(r"^\d{4}-(0[1-9]|1[0-2])$")
DATE_PATTERN = re.compile(r"^\d{4}-\d{2}-\d{2}$")


# ═══════════════════════════════════════════════════════════════════════════════
# MongoDB client — tz_aware=True so all returned datetimes are UTC-aware
# ═══════════════════════════════════════════════════════════════════════════════
client = MongoClient(
    MONGO_URI,
    tz_aware=True,
    serverSelectionTimeoutMS=5000,
)
db = client[MONGO_DB]
employees_col = db["employees"]
attendance_col = db["attendance_logs"]


# ═══════════════════════════════════════════════════════════════════════════════
# Index creation (called from lifespan)
# ═══════════════════════════════════════════════════════════════════════════════
def create_indexes():
    """Create required indexes idempotently. Log failures but don't crash."""
    indexes = [
        # employees
        (
            employees_col,
            [("emp_code", ASCENDING)],
            {"unique": True, "name": "idx_emp_code_unique"},
        ),
        (
            employees_col,
            [("department", ASCENDING), ("joined_on", ASCENDING)],
            {"name": "idx_dept_joined"},
        ),
        # attendance_logs
        (
            attendance_col,
            [("emp_code", ASCENDING), ("date", ASCENDING)],
            {"unique": True, "name": "idx_empcode_date_unique"},
        ),
        (
            attendance_col,
            [("date", DESCENDING), ("emp_code", ASCENDING)],
            {"name": "idx_date_desc_empcode"},
        ),
    ]
    for col, keys, kwargs in indexes:
        try:
            col.create_index(keys, **kwargs)
            logger.info(f"Index {kwargs['name']} on {col.name}: OK")
        except Exception as e:
            logger.error(f"Index {kwargs['name']} on {col.name} failed: {e}")


# ═══════════════════════════════════════════════════════════════════════════════
# Lifespan: startup + shutdown
# ═══════════════════════════════════════════════════════════════════════════════
@asynccontextmanager
async def lifespan(app: FastAPI):
    """Modern lifespan handler — replaces deprecated on_event('startup')."""
    create_indexes()
    yield


# ═══════════════════════════════════════════════════════════════════════════════
# FastAPI app
# ═══════════════════════════════════════════════════════════════════════════════
app = FastAPI(
    title="Employee Attendance & Analytics API",
    version="2.0.0",
    lifespan=lifespan,
)


# ─── custom exception handler for non-validation errors ──────────────────────
@app.exception_handler(HTTPException)
async def http_exception_handler(request: Request, exc: HTTPException):
    return JSONResponse(
        status_code=exc.status_code,
        content={"detail": exc.detail},
    )


# ═══════════════════════════════════════════════════════════════════════════════
# Rounding & arithmetic helpers
# ═══════════════════════════════════════════════════════════════════════════════
def half_up_round(value: float | int, decimals: int = 2) -> float:
    """
    Round a numeric value to `decimals` places using decimal HALF_UP,
    not Python's default banker's rounding.

    Uses Decimal(str(value)) to avoid float representation issues.
    """
    if value is None:
        return None
    d = Decimal(str(value))
    quantize_str = "0." + "0" * decimals
    rounded = d.quantize(Decimal(quantize_str), rounding=ROUND_HALF_UP)
    return float(rounded)


def compute_work_hours_from_seconds(total_seconds: int) -> float:
    """
    Convert integer seconds to work hours, rounded half-up to 2 dp.
    Uses integer arithmetic to avoid float errors.

    hundredths = (sec * 200 + 3600) // 7200
    work_hours = hundredths / 100
    """
    hundredths = (total_seconds * 200 + 3600) // 7200
    return hundredths / 100


def is_half_day_from_seconds(total_seconds: int) -> bool:
    """Check if work hours (from integer seconds) result in a half day (< 4.50)."""
    hundredths = (total_seconds * 200 + 3600) // 7200
    return hundredths < HALF_DAY_THRESHOLD_HUNDREDTHS


# ═══════════════════════════════════════════════════════════════════════════════
# Timestamp & timezone helpers
# ═══════════════════════════════════════════════════════════════════════════════
def epoch_ms_to_utc_dt(epoch_ms: int) -> datetime:
    """Convert epoch milliseconds to a UTC-aware datetime, truncated to whole seconds."""
    # Truncate to whole seconds
    epoch_sec = epoch_ms // 1000
    return datetime.fromtimestamp(epoch_sec, tz=UTC)


def dt_to_epoch_ms(dt: datetime) -> int | None:
    """Convert a UTC-aware datetime to epoch milliseconds. Returns None if dt is None."""
    if dt is None:
        return None
    # Ensure UTC-aware
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=UTC)
    return int(dt.timestamp() * 1000)


def utc_now_truncated() -> datetime:
    """Current UTC time truncated to whole seconds."""
    now = datetime.now(tz=UTC)
    return now.replace(microsecond=0)


def to_ist(dt: datetime) -> datetime:
    """Convert a UTC-aware datetime to IST."""
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=UTC)
    return dt.astimezone(IST)


def parse_date_string(date_str: str) -> date:
    """Parse a YYYY-MM-DD string into a date object. Raises ValueError for invalid dates."""
    return date.fromisoformat(date_str)


def validate_date_string(date_str: str) -> date:
    """Validate and parse a date string. Raises ValueError for invalid dates like 2026-02-30."""
    if not DATE_PATTERN.match(date_str):
        raise ValueError(f"Invalid date format: {date_str}")
    return date.fromisoformat(date_str)


def compute_attendance_date(punch_in_utc: datetime, shift_start: str, shift_end: str) -> str:
    """
    Compute the attendance date (YYYY-MM-DD) from the punch-in instant.

    R1: date = IST calendar date of punch_in.
    Exception for overnight shifts (shift_end <= shift_start):
      If the IST time of day of punch_in is earlier than shift_end,
      the record belongs to the previous day's shift.
    """
    ist_dt = to_ist(punch_in_utc)
    ist_date = ist_dt.date()

    is_overnight = shift_end <= shift_start

    if is_overnight:
        # Parse shift_end time
        se_h, se_m = map(int, shift_end.split(":"))
        punch_time_minutes = ist_dt.hour * 60 + ist_dt.minute
        shift_end_minutes = se_h * 60 + se_m

        # If punch-in IST time is earlier than shift_end, belongs to previous day
        if punch_time_minutes < shift_end_minutes or (
            punch_time_minutes == shift_end_minutes and ist_dt.second == 0
        ):
            ist_date = ist_date - timedelta(days=1)

    return ist_date.isoformat()


def anchor_shift_datetime(shift_time_str: str, attendance_date_str: str, add_day: bool = False) -> datetime:
    """
    Anchor a shift time (HH:MM) on the attendance date in IST, then convert to UTC.
    If add_day is True, add one day (for overnight shift_end).
    """
    h, m = map(int, shift_time_str.split(":"))
    d = date.fromisoformat(attendance_date_str)
    if add_day:
        d = d + timedelta(days=1)
    ist_dt = datetime(d.year, d.month, d.day, h, m, 0, tzinfo=IST)
    return ist_dt.astimezone(UTC)


# ═══════════════════════════════════════════════════════════════════════════════
# Derived field computation helpers
# ═══════════════════════════════════════════════════════════════════════════════
def compute_late_minutes(punch_in_utc: datetime, shift_start: str, attendance_date: str) -> int:
    """
    R2: Late minutes.

    Grace period is 10 minutes. Late only if punch_in is STRICTLY more than
    600 seconds after shift_start.
    When late, late_minutes = floor(elapsed_seconds / 60) from shift_start.
    """
    shift_start_utc = anchor_shift_datetime(shift_start, attendance_date)
    elapsed_sec = int((punch_in_utc - shift_start_utc).total_seconds())

    if elapsed_sec <= GRACE_PERIOD_SECONDS:
        return 0

    return elapsed_sec // 60


def compute_overtime_minutes(
    punch_out_utc: datetime,
    shift_end: str,
    shift_start: str,
    attendance_date: str,
) -> int:
    """
    R3: Overtime minutes.

    Whole minutes (floor) from shift_end to punch_out.
    Counted only if >= 30 minutes; otherwise 0.
    For overnight shifts, shift_end falls on date + 1.
    """
    is_overnight = shift_end <= shift_start
    shift_end_utc = anchor_shift_datetime(
        shift_end, attendance_date, add_day=is_overnight
    )
    elapsed_sec = int((punch_out_utc - shift_end_utc).total_seconds())

    if elapsed_sec < 0:
        return 0

    minutes = elapsed_sec // 60

    if minutes < OVERTIME_THRESHOLD_MINUTES:
        return 0

    return minutes


def compute_work_hours(punch_in_utc: datetime, punch_out_utc: datetime) -> float | None:
    """R4: Work hours — half-up rounded to 2 dp. None if punch_out is None."""
    if punch_out_utc is None:
        return None
    total_sec = int((punch_out_utc - punch_in_utc).total_seconds())
    return compute_work_hours_from_seconds(total_sec)


def compute_half_day(punch_in_utc: datetime, punch_out_utc: datetime) -> bool:
    """R5: Half day — true when rounded work_hours < 4.50."""
    if punch_out_utc is None:
        return False
    total_sec = int((punch_out_utc - punch_in_utc).total_seconds())
    return is_half_day_from_seconds(total_sec)


def compute_all_derived(
    status: str,
    punch_in_utc: datetime | None,
    punch_out_utc: datetime | None,
    shift_start: str,
    shift_end: str,
    attendance_date: str,
) -> dict:
    """Compute all four derived fields for a given state."""
    if status in NON_PRESENCE_STATUSES:
        return {
            "work_hours": None,
            "late_minutes": 0,
            "overtime_minutes": 0,
            "half_day": False,
        }

    late = 0
    work_hours = None
    overtime = 0
    half_day = False

    if punch_in_utc is not None:
        late = compute_late_minutes(punch_in_utc, shift_start, attendance_date)

    if punch_in_utc is not None and punch_out_utc is not None:
        work_hours = compute_work_hours(punch_in_utc, punch_out_utc)
        overtime = compute_overtime_minutes(punch_out_utc, shift_end, shift_start, attendance_date)
        half_day = compute_half_day(punch_in_utc, punch_out_utc)

    return {
        "work_hours": work_hours,
        "late_minutes": late,
        "overtime_minutes": overtime,
        "half_day": half_day,
    }


# ═══════════════════════════════════════════════════════════════════════════════
# Validation helpers
# ═══════════════════════════════════════════════════════════════════════════════
def validate_epoch_ms(value: int, field_name: str = "punched_at") -> None:
    """Validate that an epoch-ms value is a strict integer in the valid range."""
    if isinstance(value, bool):
        raise RequestValidationError(
            [{"loc": ["body", field_name], "msg": "value must be an integer, not boolean", "type": "type_error"}]
        )
    if not isinstance(value, int):
        raise RequestValidationError(
            [{"loc": ["body", field_name], "msg": "value must be an integer", "type": "type_error"}]
        )
    if value < EPOCH_MS_MIN or value > EPOCH_MS_MAX:
        raise RequestValidationError(
            [{"loc": ["body", field_name], "msg": f"value must be between {EPOCH_MS_MIN} and {EPOCH_MS_MAX}", "type": "value_error"}]
        )


def validate_month_param(month: str) -> tuple[str, str]:
    """Validate and parse month parameter. Returns (month_start_date, next_month_start_date)."""
    if not MONTH_PATTERN.match(month):
        raise RequestValidationError(
            [{"loc": ["query", "month"], "msg": "month must match YYYY-MM format", "type": "value_error"}]
        )
    year, mon = int(month[:4]), int(month[5:7])
    first_day = date(year, mon, 1)
    _, last_day_num = calendar.monthrange(year, mon)
    last_day = date(year, mon, last_day_num)

    if mon == 12:
        next_month_start = date(year + 1, 1, 1)
    else:
        next_month_start = date(year, mon + 1, 1)

    return first_day.isoformat(), next_month_start.isoformat()


def validate_pagination(page: int, page_size: int) -> None:
    """Validate page and page_size params."""
    errors = []
    if page < 1:
        errors.append({"loc": ["query", "page"], "msg": "page must be >= 1", "type": "value_error"})
    if page_size < 1 or page_size > 100:
        errors.append({"loc": ["query", "page_size"], "msg": "page_size must be 1-100", "type": "value_error"})
    if errors:
        raise RequestValidationError(errors)


# ═══════════════════════════════════════════════════════════════════════════════
# Response formatting helpers
# ═══════════════════════════════════════════════════════════════════════════════
def format_employee(doc: dict) -> dict:
    """Format an employee document for API response. Remove _id, convert created_at to epoch ms."""
    result = {
        "emp_code": doc["emp_code"],
        "name": doc["name"],
        "email": doc["email"],
        "department": doc["department"],
        "shift_start": doc.get("shift_start", "09:30"),
        "shift_end": doc.get("shift_end", "18:30"),
        "joined_on": doc["joined_on"],
        "created_at": dt_to_epoch_ms(doc["created_at"]),
    }
    return result


def format_attendance_record(doc: dict) -> dict:
    """
    Format an attendance record for API response.
    - Remove _id
    - Convert punch_in/punch_out to epoch ms
    - Default missing fields (history -> [], half_day -> false, etc.)
    - Convert history entries' datetime fields to epoch ms
    """
    result = {
        "emp_code": doc["emp_code"],
        "date": doc["date"],
        "status": doc["status"],
        "punch_in": dt_to_epoch_ms(doc.get("punch_in")),
        "punch_out": dt_to_epoch_ms(doc.get("punch_out")),
        "work_hours": doc.get("work_hours"),
        "late_minutes": doc.get("late_minutes", 0),
        "overtime_minutes": doc.get("overtime_minutes", 0),
        "half_day": doc.get("half_day", False),
        "history": format_history(doc.get("history", [])),
    }
    return result


def format_history(history: list) -> list:
    """Convert history entries' BSON dates to epoch ms recursively."""
    result = []
    for entry in history:
        formatted = {
            "at": dt_to_epoch_ms(entry.get("at")),
            "by": entry.get("by", ""),
            "reason": entry.get("reason", ""),
            "changes": {},
        }
        for field_name, change in entry.get("changes", {}).items():
            formatted_change = {}
            if field_name in ("punch_in", "punch_out"):
                # These are BSON dates in MongoDB, epoch ms in API
                formatted_change["from"] = dt_to_epoch_ms(change.get("from"))
                formatted_change["to"] = dt_to_epoch_ms(change.get("to"))
            else:
                formatted_change["from"] = change.get("from")
                formatted_change["to"] = change.get("to")
            formatted["changes"][field_name] = formatted_change
        result.append(formatted)
    return result


# ═══════════════════════════════════════════════════════════════════════════════
# Pydantic request models
# ═══════════════════════════════════════════════════════════════════════════════
class EmployeeCreate(BaseModel):
    """Request body for POST /employees."""
    model_config = ConfigDict(extra="ignore")

    emp_code: str
    name: str
    email: str
    department: str
    shift_start: str = "09:30"
    shift_end: str = "18:30"
    joined_on: str

    @field_validator("emp_code")
    @classmethod
    def validate_emp_code(cls, v: str) -> str:
        if not EMP_CODE_PATTERN.match(v):
            raise ValueError("emp_code must match ^EMP\\d{4,6}$")
        return v

    @field_validator("name")
    @classmethod
    def validate_name(cls, v: str) -> str:
        if len(v) < 1 or len(v) > 100:
            raise ValueError("name must be 1-100 characters")
        return v

    @field_validator("email")
    @classmethod
    def validate_email(cls, v: str) -> str:
        if len(v) > 120:
            raise ValueError("email must be <= 120 characters")
        if not EMAIL_PATTERN.match(v):
            raise ValueError("invalid email format")
        return v

    @field_validator("department")
    @classmethod
    def validate_department(cls, v: str) -> str:
        if len(v) < 1 or len(v) > 50:
            raise ValueError("department must be 1-50 characters")
        return v

    @field_validator("shift_start", "shift_end")
    @classmethod
    def validate_shift_time(cls, v: str) -> str:
        if not SHIFT_TIME_PATTERN.match(v):
            raise ValueError("shift time must match HH:MM 24-hour format")
        return v

    @field_validator("joined_on")
    @classmethod
    def validate_joined_on(cls, v: str) -> str:
        try:
            validate_date_string(v)
        except (ValueError, TypeError):
            raise ValueError("joined_on must be a valid YYYY-MM-DD date")
        return v

    @model_validator(mode="after")
    def validate_shift_not_equal(self):
        if self.shift_start == self.shift_end:
            raise ValueError("shift_start must differ from shift_end")
        return self


class PunchInRequest(BaseModel):
    """Request body for POST /attendance/punch-in."""
    model_config = ConfigDict(extra="ignore")

    emp_code: str
    punched_at: Optional[StrictInt] = None
    status: str = "PRESENT"

    @field_validator("punched_at")
    @classmethod
    def validate_punched_at(cls, v):
        if v is None:
            return v
        if isinstance(v, bool):
            raise ValueError("punched_at must be an integer, not boolean")
        if v < EPOCH_MS_MIN or v > EPOCH_MS_MAX:
            raise ValueError(f"punched_at must be between {EPOCH_MS_MIN} and {EPOCH_MS_MAX}")
        return v

    @field_validator("status")
    @classmethod
    def validate_status(cls, v: str) -> str:
        if v not in PRESENCE_STATUSES:
            raise ValueError(f"status must be one of {sorted(PRESENCE_STATUSES)}")
        return v


class PunchOutRequest(BaseModel):
    """Request body for POST /attendance/punch-out."""
    model_config = ConfigDict(extra="ignore")

    emp_code: str
    punched_at: Optional[StrictInt] = None

    @field_validator("punched_at")
    @classmethod
    def validate_punched_at(cls, v):
        if v is None:
            return v
        if isinstance(v, bool):
            raise ValueError("punched_at must be an integer, not boolean")
        if v < EPOCH_MS_MIN or v > EPOCH_MS_MAX:
            raise ValueError(f"punched_at must be between {EPOCH_MS_MIN} and {EPOCH_MS_MAX}")
        return v


class RegularizeRequest(BaseModel):
    """Request body for PATCH /attendance/{emp_code}/{date}."""
    model_config = ConfigDict(extra="ignore")

    status: Optional[str] = None
    punch_in: Optional[StrictInt] = None
    punch_out: Optional[StrictInt] = None
    reason: str
    regularized_by: str

    @field_validator("status")
    @classmethod
    def validate_status(cls, v):
        if v is not None and v not in ALL_STATUSES:
            raise ValueError(f"status must be one of {sorted(ALL_STATUSES)}")
        return v

    @field_validator("punch_in", "punch_out")
    @classmethod
    def validate_punch_times(cls, v):
        if v is None:
            return v
        if isinstance(v, bool):
            raise ValueError("punch time must be an integer, not boolean")
        if v < EPOCH_MS_MIN or v > EPOCH_MS_MAX:
            raise ValueError(f"punch time must be between {EPOCH_MS_MIN} and {EPOCH_MS_MAX}")
        return v

    @field_validator("reason")
    @classmethod
    def validate_reason(cls, v: str) -> str:
        if len(v) < 5 or len(v) > 200:
            raise ValueError("reason must be 5-200 characters")
        return v

    @field_validator("regularized_by")
    @classmethod
    def validate_regularized_by(cls, v: str) -> str:
        if len(v) < 1 or len(v) > 50:
            raise ValueError("regularized_by must be 1-50 characters")
        return v


# ═══════════════════════════════════════════════════════════════════════════════
# GET /health
# ═══════════════════════════════════════════════════════════════════════════════
@app.get("/health")
def health():
    """Readiness probe: returns 200 only if MongoDB answers a ping, else 503."""
    try:
        client.admin.command(
            "ping",
            serverSelectionTimeoutMS=3000,
        )
        return {"status": "ok"}
    except Exception as e:
        raise HTTPException(503, detail=f"MongoDB unavailable: {e}")


# ═══════════════════════════════════════════════════════════════════════════════
# POST /employees — create an employee
# ═══════════════════════════════════════════════════════════════════════════════
@app.post("/employees", status_code=201)
def create_employee(body: EmployeeCreate):
    """
    Create an employee. Duplicate emp_code → 409 via unique index.
    Server sets created_at.
    """
    now = utc_now_truncated()
    doc = {
        "emp_code": body.emp_code,
        "name": body.name,
        "email": body.email,
        "department": body.department,
        "shift_start": body.shift_start,
        "shift_end": body.shift_end,
        "joined_on": body.joined_on,
        "created_at": now,
    }
    try:
        employees_col.insert_one(doc)
    except DuplicateKeyError:
        raise HTTPException(409, detail="emp_code already exists")

    doc.pop("_id", None)
    return format_employee(doc)


# ═══════════════════════════════════════════════════════════════════════════════
# GET /employees — list employees
# ═══════════════════════════════════════════════════════════════════════════════
@app.get("/employees")
def list_employees(
    department: Optional[str] = None,
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=20, ge=1, le=100),
):
    """
    List employees, sorted by emp_code ascending.
    Filter by department (exact, case-sensitive). total reflects filtered count.
    """
    q: dict = {}
    if department is not None:
        q["department"] = department

    total = employees_col.count_documents(q)
    skip = (page - 1) * page_size
    cursor = employees_col.find(q, {"_id": 0}).sort("emp_code", ASCENDING).skip(skip).limit(page_size)

    items = [format_employee(doc) for doc in cursor]

    return {"items": items, "total": total, "page": page, "page_size": page_size}


# ═══════════════════════════════════════════════════════════════════════════════
# POST /attendance/punch-in
# ═══════════════════════════════════════════════════════════════════════════════
@app.post("/attendance/punch-in", status_code=201)
def punch_in(body: PunchInRequest):
    """
    Record a punch-in. Creates the day's attendance record.
    Order: body validation (422) → employee exists (404) → insert (409 on dup).
    """
    # Resolve timestamp
    if body.punched_at is not None:
        ts = epoch_ms_to_utc_dt(body.punched_at)
    else:
        ts = utc_now_truncated()

    # Employee must exist
    emp = employees_col.find_one({"emp_code": body.emp_code}, {"_id": 0})
    if emp is None:
        raise HTTPException(404, detail="Employee not found")

    # Compute attendance date per R1
    attendance_date = compute_attendance_date(
        ts, emp["shift_start"], emp["shift_end"]
    )

    # Compute late_minutes
    late = compute_late_minutes(ts, emp["shift_start"], attendance_date)

    doc = {
        "emp_code": body.emp_code,
        "date": attendance_date,
        "status": body.status,
        "punch_in": ts,
        "punch_out": None,
        "work_hours": None,
        "late_minutes": late,
        "overtime_minutes": 0,
        "half_day": False,
        "history": [],
    }

    try:
        attendance_col.insert_one(doc)
    except DuplicateKeyError:
        raise HTTPException(409, detail="Already punched in for this date")

    doc.pop("_id", None)
    return format_attendance_record(doc)


# ═══════════════════════════════════════════════════════════════════════════════
# POST /attendance/punch-out
# ═══════════════════════════════════════════════════════════════════════════════
@app.post("/attendance/punch-out")
def punch_out(body: PunchOutRequest):
    """
    Close the employee's most recent open record whose punch_in <= punched_at.
    Atomic conditional update ensures exactly one 200 for simultaneous requests.
    """
    # Resolve timestamp
    if body.punched_at is not None:
        ts = epoch_ms_to_utc_dt(body.punched_at)
    else:
        ts = utc_now_truncated()

    # Employee must exist
    emp = employees_col.find_one({"emp_code": body.emp_code}, {"_id": 0})
    if emp is None:
        raise HTTPException(404, detail="Employee not found")

    # Find the most recent record by punch_in with punch_in <= punched_at
    # Records with null punch_in cannot match
    record = attendance_col.find_one(
        {
            "emp_code": body.emp_code,
            "punch_in": {"$ne": None, "$lte": ts},
        },
        sort=[("punch_in", DESCENDING)],
    )

    if record is None:
        raise HTTPException(404, detail="No eligible punch-in record found")

    punch_in_dt = record["punch_in"]

    # Validate: punched_at must be > punch_in
    if ts <= punch_in_dt:
        raise RequestValidationError(
            [{"loc": ["body", "punched_at"], "msg": "punch_out must be after punch_in", "type": "value_error"}]
        )

    # Validate: within 24 hours of punch_in
    if (ts - punch_in_dt).total_seconds() > 86400:
        raise RequestValidationError(
            [{"loc": ["body", "punched_at"], "msg": "punch_out must be within 24 hours of punch_in", "type": "value_error"}]
        )

    # Check if already closed — 409 if punch_out already set
    if record.get("punch_out") is not None:
        raise HTTPException(409, detail="Already punched out")

    # Compute derived fields
    work_hours = compute_work_hours(punch_in_dt, ts)
    overtime = compute_overtime_minutes(
        ts, emp["shift_end"], emp["shift_start"], record["date"]
    )
    half_day = compute_half_day(punch_in_dt, ts)

    # Atomic conditional update: only matches if punch_out is still null/missing
    result = attendance_col.update_one(
        {
            "_id": record["_id"],
            "$or": [
                {"punch_out": None},
                {"punch_out": {"$exists": False}},
            ],
        },
        {
            "$set": {
                "punch_out": ts,
                "work_hours": work_hours,
                "overtime_minutes": overtime,
                "half_day": half_day,
            }
        },
    )

    if result.modified_count == 0:
        raise HTTPException(409, detail="Already punched out (concurrent request)")

    # Re-read the updated doc for response
    updated = attendance_col.find_one({"_id": record["_id"]})
    return format_attendance_record(updated)


# ═══════════════════════════════════════════════════════════════════════════════
# GET /attendance — list attendance records
# ═══════════════════════════════════════════════════════════════════════════════
@app.get("/attendance")
def list_attendance(
    emp_code: Optional[str] = None,
    date_from: Optional[str] = None,
    date_to: Optional[str] = None,
    status: Optional[str] = None,
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=20, ge=1, le=100),
):
    """
    List attendance records sorted by date desc, emp_code asc.
    All filtering done in MongoDB with pagination.
    """
    # Validate status
    if status is not None and status not in ALL_STATUSES:
        raise RequestValidationError(
            [{"loc": ["query", "status"], "msg": f"status must be one of {sorted(ALL_STATUSES)}", "type": "value_error"}]
        )

    # Validate date params
    if date_from is not None:
        try:
            validate_date_string(date_from)
        except ValueError:
            raise RequestValidationError(
                [{"loc": ["query", "date_from"], "msg": "invalid date", "type": "value_error"}]
            )
    if date_to is not None:
        try:
            validate_date_string(date_to)
        except ValueError:
            raise RequestValidationError(
                [{"loc": ["query", "date_to"], "msg": "invalid date", "type": "value_error"}]
            )

    if date_from is not None and date_to is not None and date_from > date_to:
        raise RequestValidationError(
            [{"loc": ["query", "date_from"], "msg": "date_from must not be greater than date_to", "type": "value_error"}]
        )

    # Build filter
    q: dict = {}
    if emp_code is not None:
        q["emp_code"] = emp_code
    if date_from is not None or date_to is not None:
        date_filter: dict = {}
        if date_from is not None:
            date_filter["$gte"] = date_from
        if date_to is not None:
            date_filter["$lte"] = date_to
        q["date"] = date_filter
    if status is not None:
        q["status"] = status

    total = attendance_col.count_documents(q)
    skip = (page - 1) * page_size
    cursor = (
        attendance_col.find(q, {"_id": 0})
        .sort([("date", DESCENDING), ("emp_code", ASCENDING)])
        .skip(skip)
        .limit(page_size)
    )

    items = [format_attendance_record(doc) for doc in cursor]

    return {"items": items, "total": total, "page": page, "page_size": page_size}


# ═══════════════════════════════════════════════════════════════════════════════
# PATCH /attendance/{emp_code}/{date} — regularize
# ═══════════════════════════════════════════════════════════════════════════════
MAX_REGULARIZE_RETRIES = 3


@app.patch("/attendance/{emp_code}/{date}")
def regularize_attendance(
    emp_code: str = Path(...),
    date: str = Path(...),
    body: RegularizeRequest = ...,
):
    """
    Regularize (manually correct) one day's record.
    Recomputes derived fields, appends one history entry.
    Uses optimistic concurrency (retry on conflict).
    """
    # Validate date path param
    try:
        validate_date_string(date)
    except ValueError:
        raise RequestValidationError([{"loc": ["path", "date"], "msg": "Invalid date in path", "type": "value_error"}])

    # Employee must exist
    emp = employees_col.find_one({"emp_code": emp_code}, {"_id": 0})
    if emp is None:
        raise HTTPException(404, detail="Employee not found")

    for attempt in range(MAX_REGULARIZE_RETRIES):
        # Read current record
        record = attendance_col.find_one({"emp_code": emp_code, "date": date})
        if record is None:
            raise HTTPException(404, detail="No attendance record for this date")

        # ── Build the final state ──
        old_status = record["status"]
        old_punch_in = record.get("punch_in")
        old_punch_out = record.get("punch_out")

        new_status = body.status if body.status is not None else old_status
        new_punch_in = epoch_ms_to_utc_dt(body.punch_in) if body.punch_in is not None else old_punch_in
        new_punch_out = epoch_ms_to_utc_dt(body.punch_out) if body.punch_out is not None else old_punch_out

        # ── Validate final state ──
        # ABSENT/LEAVE: no punches allowed
        if new_status in NON_PRESENCE_STATUSES:
            if body.punch_in is not None or body.punch_out is not None:
                raise RequestValidationError(
                    [{"loc": ["body", "status"], "msg": f"Cannot supply punch times with status {new_status}", "type": "value_error"}]
                )
            # Clear punches
            new_punch_in = None
            new_punch_out = None

        # Presence status requires a punch_in
        if new_status in PRESENCE_STATUSES:
            if new_punch_in is None:
                raise RequestValidationError(
                    [{"loc": ["body", "punch_in"], "msg": "Presence status requires a punch_in (supplied or already stored)", "type": "value_error"}]
                )

        # punch_in must resolve to the record's date
        if new_punch_in is not None and new_status in PRESENCE_STATUSES:
            resolved_date = compute_attendance_date(
                new_punch_in, emp["shift_start"], emp["shift_end"]
            )
            if resolved_date != date:
                raise RequestValidationError(
                    [{"loc": ["body", "punch_in"], "msg": f"punch_in does not resolve to attendance date {date}", "type": "value_error"}]
                )

        # punch_out without punch_in
        if new_punch_out is not None and new_punch_in is None:
            raise RequestValidationError([{"loc": ["body", "punch_out"], "msg": "punch_out without punch_in", "type": "value_error"}])

        # punch_out must be after punch_in and within 24h
        if new_punch_out is not None and new_punch_in is not None:
            if new_punch_out <= new_punch_in:
                raise RequestValidationError([{"loc": ["body", "punch_out"], "msg": "punch_out must be after punch_in", "type": "value_error"}])
            if (new_punch_out - new_punch_in).total_seconds() > 86400:
                raise RequestValidationError([{"loc": ["body", "punch_out"], "msg": "punch_out must be within 24 hours of punch_in", "type": "value_error"}])

        # ── Compute derived fields ──
        derived = compute_all_derived(
            new_status, new_punch_in, new_punch_out,
            emp["shift_start"], emp["shift_end"], date,
        )

        old_derived = {
            "work_hours": record.get("work_hours"),
            "late_minutes": record.get("late_minutes", 0),
            "overtime_minutes": record.get("overtime_minutes", 0),
            "half_day": record.get("half_day", False),
        }

        # ── Build update and changes ──
        update_fields: dict = {}
        changes: dict = {}

        # Check each mutable field for actual changes
        field_pairs = [
            ("status", old_status, new_status),
            ("punch_in", old_punch_in, new_punch_in),
            ("punch_out", old_punch_out, new_punch_out),
            ("work_hours", old_derived["work_hours"], derived["work_hours"]),
            ("late_minutes", old_derived["late_minutes"], derived["late_minutes"]),
            ("overtime_minutes", old_derived["overtime_minutes"], derived["overtime_minutes"]),
            ("half_day", old_derived["half_day"], derived["half_day"]),
        ]

        for field_name, old_val, new_val in field_pairs:
            changed = False
            if field_name in ("punch_in", "punch_out"):
                # Compare datetimes carefully
                if old_val is None and new_val is None:
                    changed = False
                elif old_val is None or new_val is None:
                    changed = True
                else:
                    changed = abs((old_val - new_val).total_seconds()) > 0
            elif field_name == "work_hours":
                if old_val is None and new_val is None:
                    changed = False
                elif old_val is None or new_val is None:
                    changed = True
                else:
                    changed = old_val != new_val
            else:
                changed = old_val != new_val

            if changed:
                if field_name in ("punch_in", "punch_out"):
                    changes[field_name] = {
                        "from": old_val,  # Will be BSON date in MongoDB
                        "to": new_val,
                    }
                else:
                    changes[field_name] = {"from": old_val, "to": new_val}

        # Nothing actually changed → 422
        if not changes:
            raise RequestValidationError([{"loc": ["body"], "msg": "No fields changed", "type": "value_error"}])

        # Build the update set
        update_fields["status"] = new_status
        update_fields["punch_in"] = new_punch_in
        update_fields["punch_out"] = new_punch_out
        update_fields.update(derived)

        # History entry
        history_entry = {
            "at": utc_now_truncated(),
            "by": body.regularized_by,
            "reason": body.reason,
            "changes": changes,
        }

        # ── Atomic update with optimistic concurrency ──
        # Pin to the state we read: match by _id, current status, punch values, and history length
        current_history = record.get("history", [])
        history_len = len(current_history)

        filter_doc = {
            "_id": record["_id"],
            "status": old_status,
        }

        # Pin punch_in — handle null/missing
        if old_punch_in is None:
            filter_doc["$or"] = [
                {"punch_in": None},
                {"punch_in": {"$exists": False}},
            ]
        else:
            filter_doc["punch_in"] = old_punch_in

        # Use $expr to check history length for concurrency guard
        # We need to check history array length matches what we read
        filter_doc["$expr"] = {
            "$eq": [
                {"$size": {"$ifNull": ["$history", []]}},
                history_len,
            ]
        }

        # Remove the $or if we already have punch_in set (can't have both $or and $expr easily)
        # Actually, we need to combine them. Let's restructure:
        if old_punch_in is None:
            # Restructure: use $and to combine everything
            filter_doc = {
                "$and": [
                    {"_id": record["_id"]},
                    {"status": old_status},
                    {"$or": [{"punch_in": None}, {"punch_in": {"$exists": False}}]},
                    {"$expr": {"$eq": [{"$size": {"$ifNull": ["$history", []]}}, history_len]}},
                ]
            }

        result = attendance_col.update_one(
            filter_doc,
            {
                "$set": update_fields,
                "$push": {"history": history_entry},
            },
        )

        if result.modified_count == 1:
            # Success — read back and return
            updated = attendance_col.find_one({"_id": record["_id"]})
            return format_attendance_record(updated)

        # Conflict — retry
        logger.warning(f"Regularization conflict on attempt {attempt + 1} for {emp_code}/{date}")

    # All retries exhausted
    raise HTTPException(409, detail="Concurrent modification conflict")


# ═══════════════════════════════════════════════════════════════════════════════
# Working days helper (for analytics)
# ═══════════════════════════════════════════════════════════════════════════════
def count_working_days(month_start: date, month_end: date, joined_on: date) -> int:
    """
    Count Mon-Fri days in [max(month_start, joined_on), month_end].
    Returns 0 if joined_on is after month_end.
    """
    start = max(month_start, joined_on)
    if start > month_end:
        return 0

    count = 0
    current = start
    while current <= month_end:
        if current.weekday() < 5:  # Mon=0 ... Fri=4
            count += 1
        current += timedelta(days=1)
    return count


# ═══════════════════════════════════════════════════════════════════════════════
# Aggregation pipeline builders
# ═══════════════════════════════════════════════════════════════════════════════
def build_attendance_list_query(
    emp_code: str | None,
    date_from: str | None,
    date_to: str | None,
    status: str | None,
) -> dict:
    """Build the filter dict for list_attendance."""
    q: dict = {}
    if emp_code is not None:
        q["emp_code"] = emp_code
    if date_from is not None or date_to is not None:
        date_filter: dict = {}
        if date_from is not None:
            date_filter["$gte"] = date_from
        if date_to is not None:
            date_filter["$lte"] = date_to
        q["date"] = date_filter
    if status is not None:
        q["status"] = status
    return q


def build_employee_monthly_pipeline(emp_code: str, month_start: str, month_end_exclusive: str) -> list:
    """Build the aggregation pipeline for employee monthly summary."""
    return [
        {
            "$match": {
                "emp_code": emp_code,
                "date": {"$gte": month_start, "$lt": month_end_exclusive},
            }
        },
        {
            "$addFields": {
                "is_presence": {"$in": ["$status", list(PRESENCE_STATUSES)]},
                "safe_late_minutes": {"$ifNull": ["$late_minutes", 0]},
                "safe_overtime_minutes": {"$ifNull": ["$overtime_minutes", 0]},
                "safe_half_day": {"$ifNull": ["$half_day", False]},
                "date_obj": {"$dateFromString": {"dateString": "$date", "format": "%Y-%m-%d"}},
            }
        },
        {
            "$addFields": {
                "day_of_week": {"$dayOfWeek": "$date_obj"},
                # $dayOfWeek: 1=Sun, 2=Mon, ..., 7=Sat
                "is_weekday": {
                    "$and": [
                        {"$gte": [{"$dayOfWeek": "$date_obj"}, 2]},
                        {"$lte": [{"$dayOfWeek": "$date_obj"}, 6]},
                    ]
                },
            }
        },
        {
            "$group": {
                "_id": None,
                "present_days_raw": {
                    "$sum": {
                        "$cond": [
                            {
                                "$and": [
                                    "$is_presence",
                                    "$is_weekday",
                                ]
                            },
                            {
                                "$cond": ["$safe_half_day", 0.5, 1.0]
                            },
                            0,
                        ]
                    }
                },
                "leave_days": {
                    "$sum": {
                        "$cond": [{"$eq": ["$status", "LEAVE"]}, 1, 0]
                    }
                },
                "late_count": {
                    "$sum": {
                        "$cond": [{"$gt": ["$safe_late_minutes", 0]}, 1, 0]
                    }
                },
                "total_late_minutes": {
                    "$sum": "$safe_late_minutes"
                },
                "total_overtime_minutes": {
                    "$sum": "$safe_overtime_minutes"
                },
            }
        },
    ]


def build_department_summary_pipeline(month_start: str, month_end_exclusive: str, month_end: str, department: str | None = None) -> list:
    """
    Department summary pipeline — drives from employees collection.
    Employees with joined_on <= month_end are included (R9).
    """
    emp_match: dict = {"joined_on": {"$lte": month_end}}
    if department is not None:
        emp_match["department"] = department

    pipeline = [
        {"$match": emp_match},
        # Lookup attendance for each employee in the month
        {
            "$lookup": {
                "from": "attendance_logs",
                "let": {"ec": "$emp_code"},
                "pipeline": [
                    {
                        "$match": {
                            "$expr": {"$eq": ["$emp_code", "$$ec"]},
                            "date": {"$gte": month_start, "$lt": month_end_exclusive},
                        }
                    },
                ],
                "as": "logs",
            }
        },
        {"$unwind": {"path": "$logs", "preserveNullAndEmptyArrays": True}},
        # Compute per-log fields
        {
            "$addFields": {
                "has_log": {"$cond": [{"$ifNull": ["$logs", False]}, True, False]},
                "log_status": {"$ifNull": ["$logs.status", None]},
                "log_is_presence": {
                    "$cond": [
                        {"$ifNull": ["$logs", False]},
                        {"$in": [{"$ifNull": ["$logs.status", ""]}, list(PRESENCE_STATUSES)]},
                        False,
                    ]
                },
                "log_half_day": {"$ifNull": ["$logs.half_day", False]},
                "log_work_hours": "$logs.work_hours",
                "log_late_minutes": {"$ifNull": ["$logs.late_minutes", 0]},
                "log_date_obj": {
                    "$cond": [
                        {"$ifNull": ["$logs.date", False]},
                        {"$dateFromString": {"dateString": {"$ifNull": ["$logs.date", "2000-01-01"]}, "format": "%Y-%m-%d"}},
                        None,
                    ]
                },
            }
        },
        {
            "$addFields": {
                "log_is_weekday": {
                    "$cond": [
                        {"$ifNull": ["$log_date_obj", False]},
                        {
                            "$and": [
                                {"$gte": [{"$dayOfWeek": "$log_date_obj"}, 2]},
                                {"$lte": [{"$dayOfWeek": "$log_date_obj"}, 6]},
                            ]
                        },
                        False,
                    ]
                },
            }
        },
        # Group by department
        {
            "$group": {
                "_id": "$department",
                "emp_codes": {"$addToSet": "$emp_code"},
                "present_days": {
                    "$sum": {
                        "$cond": [
                            {"$and": ["$log_is_presence", "$log_is_weekday"]},
                            {"$cond": ["$log_half_day", 0.5, 1.0]},
                            0,
                        ]
                    }
                },
                "work_hours_sum": {
                    "$sum": {
                        "$cond": [
                            {
                                "$and": [
                                    "$log_is_presence",
                                    {"$ne": [{"$ifNull": ["$log_work_hours", None]}, None]},
                                ]
                            },
                            "$log_work_hours",
                            0,
                        ]
                    }
                },
                "work_hours_count": {
                    "$sum": {
                        "$cond": [
                            {
                                "$and": [
                                    "$log_is_presence",
                                    {"$ne": [{"$ifNull": ["$log_work_hours", None]}, None]},
                                ]
                            },
                            1,
                            0,
                        ]
                    }
                },
                "late_count": {
                    "$sum": {
                        "$cond": [
                            {"$and": ["$has_log", {"$gt": ["$log_late_minutes", 0]}]},
                            1, 0,
                        ]
                    }
                },
                "total_late_minutes": {
                    "$sum": {
                        "$cond": ["$has_log", "$log_late_minutes", 0]
                    }
                },
                "leave_count": {
                    "$sum": {
                        "$cond": [
                            {"$eq": ["$log_status", "LEAVE"]},
                            1, 0,
                        ]
                    }
                },
                "on_duty_count": {
                    "$sum": {
                        "$cond": [
                            {"$eq": ["$log_status", "ON_DUTY"]},
                            1, 0,
                        ]
                    }
                },
            }
        },
        # Compute headcount and avg_work_hours
        {
            "$addFields": {
                "headcount": {"$size": "$emp_codes"},
                "avg_work_hours_raw": {
                    "$cond": [
                        {"$gt": ["$work_hours_count", 0]},
                        {"$divide": ["$work_hours_sum", "$work_hours_count"]},
                        None,
                    ]
                },
            }
        },
        # Filter out zero-headcount departments
        {"$match": {"headcount": {"$gt": 0}}},
        # Project final shape
        {
            "$project": {
                "_id": 0,
                "department": "$_id",
                "headcount": 1,
                "present_days": 1,
                "avg_work_hours_raw": 1,
                "late_count": 1,
                "total_late_minutes": 1,
                "leave_count": 1,
                "on_duty_count": 1,
            }
        },
        {"$sort": {"department": 1}},
    ]
    return pipeline


def build_late_leaderboard_pipeline(month_start: str, month_end_exclusive: str, limit: int, department: str | None = None) -> list:
    """
    Late leaderboard pipeline.
    Competition ranking via $setWindowFields + $rank.
    """
    # Start from attendance_logs in the month range
    pipeline: list = [
        {
            "$match": {
                "date": {"$gte": month_start, "$lt": month_end_exclusive},
            }
        },
        # Add safe late_minutes
        {"$addFields": {"safe_late_minutes": {"$ifNull": ["$late_minutes", 0]}}},
        # Group by emp_code
        {
            "$group": {
                "_id": "$emp_code",
                "total_late_minutes": {"$sum": "$safe_late_minutes"},
                "late_count": {
                    "$sum": {"$cond": [{"$gt": ["$safe_late_minutes", 0]}, 1, 0]}
                },
            }
        },
        # Filter out zero totals
        {"$match": {"total_late_minutes": {"$gt": 0}}},
        # Lookup employee details
        {
            "$lookup": {
                "from": "employees",
                "localField": "_id",
                "foreignField": "emp_code",
                "as": "emp_info",
            }
        },
        # Only keep employees that exist
        {"$match": {"emp_info": {"$ne": []}}},
        {"$unwind": "$emp_info"},
    ]

    # Apply department filter BEFORE ranking
    if department is not None:
        pipeline.append({"$match": {"emp_info.department": department}})

    # Competition ranking with $setWindowFields
    pipeline.extend([
        {
            "$setWindowFields": {
                "sortBy": {"total_late_minutes": -1},
                "output": {
                    "rank": {"$rank": {}},
                },
            }
        },
        # Filter by rank <= limit (keeps all ties at the cutoff)
        {"$match": {"rank": {"$lte": limit}}},
        # Final sort and projection
        {"$sort": {"total_late_minutes": -1, "_id": 1}},
        {
            "$project": {
                "_id": 0,
                "rank": 1,
                "emp_code": "$_id",
                "name": "$emp_info.name",
                "department": "$emp_info.department",
                "total_late_minutes": 1,
                "late_count": 1,
            }
        },
    ])
    return pipeline


def build_department_trend_pipeline(
    department: str,
    date_from: str,
    date_to: str,
    emp_codes: list[str],
) -> list:
    """
    Department trend pipeline.
    Uses $densify to fill in missing days.
    """
    # We'll build a pipeline that:
    # 1. Matches attendance for employees in the department and date range
    # 2. Adds fields for presence/late computations
    # 3. Groups by date
    # 4. Uses $densify to fill gaps
    # 5. Computes headcount per day from employee join dates

    pipeline = [
        # Match attendance for the department's employees in the date range
        {
            "$match": {
                "emp_code": {"$in": emp_codes},
                "date": {"$gte": date_from, "$lte": date_to},
            }
        },
        # Add helper fields
        {
            "$addFields": {
                "is_presence": {"$in": ["$status", list(PRESENCE_STATUSES)]},
                "safe_half_day": {"$ifNull": ["$half_day", False]},
                "safe_late_minutes": {"$ifNull": ["$late_minutes", 0]},
            }
        },
        # Group by date
        {
            "$group": {
                "_id": "$date",
                "present_count": {
                    "$sum": {
                        "$cond": [
                            "$is_presence",
                            {"$cond": ["$safe_half_day", 0.5, 1.0]},
                            0,
                        ]
                    }
                },
                "late_count": {
                    "$sum": {
                        "$cond": [{"$gt": ["$safe_late_minutes", 0]}, 1, 0]
                    }
                },
            }
        },
        # Rename and add date_obj for densify
        {
            "$addFields": {
                "date_str": "$_id",
                "date_obj": {"$dateFromString": {"dateString": "$_id", "format": "%Y-%m-%d"}},
            }
        },
        # Densify to fill in missing days
        {
            "$densify": {
                "field": "date_obj",
                "range": {
                    "step": 1,
                    "unit": "day",
                    "bounds": [
                        datetime.strptime(date_from, "%Y-%m-%d").replace(tzinfo=UTC),
                        datetime.strptime(date_to, "%Y-%m-%d").replace(tzinfo=UTC) + timedelta(days=1),
                    ],
                },
            }
        },
        # Fill in missing values for densified rows
        {
            "$addFields": {
                "date_str": {
                    "$ifNull": [
                        "$date_str",
                        {"$dateToString": {"format": "%Y-%m-%d", "date": "$date_obj"}},
                    ]
                },
                "present_count": {"$ifNull": ["$present_count", 0]},
                "late_count": {"$ifNull": ["$late_count", 0]},
            }
        },
        # Compute day of week for is_working_day
        {
            "$addFields": {
                "day_of_week": {"$dayOfWeek": "$date_obj"},
                "is_working_day": {
                    "$and": [
                        {"$gte": [{"$dayOfWeek": "$date_obj"}, 2]},
                        {"$lte": [{"$dayOfWeek": "$date_obj"}, 6]},
                    ]
                },
            }
        },
        # Sort by date
        {"$sort": {"date_obj": 1}},
        # Final projection
        {
            "$project": {
                "_id": 0,
                "date": "$date_str",
                "date_obj": 1,
                "is_working_day": 1,
                "present_count": 1,
                "late_count": {"$toInt": "$late_count"},
                "day_of_week": 1,
            }
        },
    ]
    return pipeline


# ═══════════════════════════════════════════════════════════════════════════════
# GET /analytics/employees/{emp_code}/monthly
# ═══════════════════════════════════════════════════════════════════════════════
@app.get("/analytics/employees/{emp_code}/monthly")
def employee_monthly(
    emp_code: str = Path(...),
    month: str = Query(...),
):
    """Monthly attendance summary for one employee."""
    # Validate month
    month_start_str, month_end_exclusive_str = validate_month_param(month)

    # Employee must exist
    emp = employees_col.find_one({"emp_code": emp_code}, {"_id": 0})
    if emp is None:
        raise HTTPException(404, detail="Employee not found")

    # Parse dates
    year, mon = int(month[:4]), int(month[5:7])
    month_start = date(year, mon, 1)
    _, last_day_num = calendar.monthrange(year, mon)
    month_end = date(year, mon, last_day_num)
    joined_on = date.fromisoformat(emp["joined_on"])

    # Working days
    working_days = count_working_days(month_start, month_end, joined_on)

    # Run aggregation
    pipeline = build_employee_monthly_pipeline(emp_code, month_start_str, month_end_exclusive_str)
    results = list(attendance_col.aggregate(pipeline))

    if results:
        agg = results[0]
        present_days = agg.get("present_days_raw", 0)
        leave_days = agg.get("leave_days", 0)
        late_count = agg.get("late_count", 0)
        total_late_minutes = agg.get("total_late_minutes", 0)
        total_overtime_minutes = agg.get("total_overtime_minutes", 0)
    else:
        present_days = 0
        leave_days = 0
        late_count = 0
        total_late_minutes = 0
        total_overtime_minutes = 0

    # Attendance percentage
    if working_days == 0:
        attendance_pct = None
    else:
        attendance_pct = half_up_round(present_days / working_days * 100, 2)

    return {
        "emp_code": emp_code,
        "month": month,
        "working_days": working_days,
        "present_days": present_days,
        "leave_days": leave_days,
        "late_count": late_count,
        "total_late_minutes": total_late_minutes,
        "total_overtime_minutes": total_overtime_minutes,
        "attendance_pct": attendance_pct,
    }


# ═══════════════════════════════════════════════════════════════════════════════
# GET /analytics/departments/summary
# ═══════════════════════════════════════════════════════════════════════════════
@app.get("/analytics/departments/summary")
def department_summary(
    month: str = Query(...),
    department: Optional[str] = None,
):
    """Department summary for a month. Sorted by department ascending."""
    month_start_str, month_end_exclusive_str = validate_month_param(month)

    year, mon = int(month[:4]), int(month[5:7])
    _, last_day_num = calendar.monthrange(year, mon)
    month_end_str = date(year, mon, last_day_num).isoformat()

    pipeline = build_department_summary_pipeline(
        month_start_str, month_end_exclusive_str, month_end_str, department
    )

    results = list(employees_col.aggregate(pipeline))

    items = []
    for row in results:
        avg_wh = row.get("avg_work_hours_raw")
        if avg_wh is not None:
            avg_wh = half_up_round(avg_wh, 2)

        items.append({
            "department": row["department"],
            "headcount": row["headcount"],
            "present_days": row["present_days"],
            "avg_work_hours": avg_wh,
            "late_count": row["late_count"],
            "total_late_minutes": row["total_late_minutes"],
            "leave_count": row["leave_count"],
            "on_duty_count": row["on_duty_count"],
        })

    return {"month": month, "items": items}


# ═══════════════════════════════════════════════════════════════════════════════
# GET /analytics/leaderboard/late
# ═══════════════════════════════════════════════════════════════════════════════
@app.get("/analytics/leaderboard/late")
def late_leaderboard(
    month: str = Query(...),
    limit: int = Query(default=10, ge=1, le=50),
    department: Optional[str] = None,
):
    """Late-comers leaderboard with competition ranking."""
    month_start_str, month_end_exclusive_str = validate_month_param(month)

    pipeline = build_late_leaderboard_pipeline(
        month_start_str, month_end_exclusive_str, limit, department
    )
    results = list(attendance_col.aggregate(pipeline))

    items = []
    for row in results:
        items.append({
            "rank": row["rank"],
            "emp_code": row["emp_code"],
            "name": row["name"],
            "department": row["department"],
            "total_late_minutes": row["total_late_minutes"],
            "late_count": row["late_count"],
        })

    return {"month": month, "items": items}


# ═══════════════════════════════════════════════════════════════════════════════
# GET /analytics/departments/{department}/trend
# ═══════════════════════════════════════════════════════════════════════════════
@app.get("/analytics/departments/{department}/trend")
def department_trend(
    department: str = Path(...),
    date_from: str = Query(..., alias="from"),
    date_to: str = Query(..., alias="to"),
):
    """Daily attendance trend for a department with 7-day moving average."""
    # Validate dates
    try:
        from_date = validate_date_string(date_from)
    except ValueError:
        raise RequestValidationError(
            [{"loc": ["query", "from"], "msg": "invalid date", "type": "value_error"}]
        )
    try:
        to_date = validate_date_string(date_to)
    except ValueError:
        raise RequestValidationError(
            [{"loc": ["query", "to"], "msg": "invalid date", "type": "value_error"}]
        )

    if to_date < from_date:
        raise RequestValidationError(
            [{"loc": ["query", "to"], "msg": "'to' must be >= 'from'", "type": "value_error"}]
        )

    # Range must not exceed 92 days (92 inclusive is valid, 93 is not)
    day_count = (to_date - from_date).days + 1
    if day_count > 92:
        raise RequestValidationError(
            [{"loc": ["query", "to"], "msg": "date range must not exceed 92 days", "type": "value_error"}]
        )

    # Department must have at least one employee
    dept_employees = list(employees_col.find({"department": department}, {"_id": 0, "emp_code": 1, "joined_on": 1}))
    if not dept_employees:
        raise HTTPException(404, detail="Department not found (no employees)")

    emp_codes = [e["emp_code"] for e in dept_employees]

    # Run the pipeline
    pipeline = build_department_trend_pipeline(department, date_from, date_to, emp_codes)
    raw_results = list(attendance_col.aggregate(pipeline))

    # Build the result, computing headcount and rates Python-side
    # (headcount requires knowing joined_on per employee per day — easier to compute here)
    # Build a map of results by date
    result_map: dict[str, dict] = {}
    for row in raw_results:
        d = row["date"]
        result_map[d] = row

    items = []
    # Track non-null rates for moving average (window of 7 including current)
    rate_window: list[float | None] = []

    current = from_date
    while current <= to_date:
        d_str = current.isoformat()
        is_working_day = current.weekday() < 5  # Mon=0...Fri=4

        # Compute headcount for this day
        headcount = sum(
            1 for e in dept_employees
            if date.fromisoformat(e["joined_on"]) <= current
        )

        # Get attendance data
        row = result_map.get(d_str, {})
        present_count = row.get("present_count", 0)
        late_count = int(row.get("late_count", 0))

        # Attendance rate
        if is_working_day and headcount > 0:
            attendance_rate = half_up_round(present_count / headcount, 4)
        else:
            attendance_rate = None

        rate_window.append(attendance_rate)

        # Moving avg 7d: mean of non-null rates in window [-6, 0] within range
        window = rate_window[-7:]  # at most 7 elements
        non_null_rates = [r for r in window if r is not None]
        if non_null_rates:
            moving_avg = half_up_round(sum(non_null_rates) / len(non_null_rates), 4)
        else:
            moving_avg = None

        items.append({
            "date": d_str,
            "is_working_day": is_working_day,
            "headcount": headcount,
            "present_count": present_count,
            "late_count": late_count,
            "attendance_rate": attendance_rate,
            "moving_avg_7d": moving_avg,
        })

        current += timedelta(days=1)

    return {"department": department, "items": items}


# ═══════════════════════════════════════════════════════════════════════════════
# GET /admin/explain/{endpoint}
# ═══════════════════════════════════════════════════════════════════════════════
@app.get("/admin/explain/{endpoint}")
def explain_endpoint(
    endpoint: str = Path(...),
    emp_code: Optional[str] = None,
    month: Optional[str] = None,
    department: Optional[str] = None,
    limit: int = Query(default=10, ge=1, le=50),
    date_from: Optional[str] = Query(default=None, alias="date_from"),
    date_to: Optional[str] = Query(default=None, alias="date_to"),
    status: Optional[str] = None,
    from_date: Optional[str] = Query(default=None, alias="from"),
    to_date: Optional[str] = Query(default=None, alias="to"),
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=20, ge=1, le=100),
):
    """
    Run MongoDB explain with executionStats for the named endpoint's query.
    Uses the same query/pipeline builder the real endpoint uses.
    """
    if endpoint not in EXPLAIN_ENDPOINTS:
        raise RequestValidationError(
            [{"loc": ["path", "endpoint"], "msg": f"endpoint must be one of {sorted(EXPLAIN_ENDPOINTS)}", "type": "value_error"}]
        )

    try:
        if endpoint == "attendance_list":
            q = build_attendance_list_query(emp_code, date_from, date_to, status)
            skip_val = (page - 1) * page_size
            explain_cmd = {
                "find": "attendance_logs",
                "filter": q,
                "sort": {"date": -1, "emp_code": 1},
                "skip": skip_val,
                "limit": page_size,
            }
            explain_result = db.command(
                {"explain": explain_cmd, "verbosity": "executionStats"}
            )
            collection_name = "attendance_logs"

        elif endpoint == "employee_monthly":
            if not emp_code or not month:
                raise RequestValidationError(
                    [{"loc": ["query"], "msg": "emp_code and month are required for employee_monthly", "type": "value_error"}]
                )
            month_start_str, month_end_exclusive_str = validate_month_param(month)
            pipeline = build_employee_monthly_pipeline(emp_code, month_start_str, month_end_exclusive_str)
            explain_cmd = {
                "aggregate": "attendance_logs",
                "pipeline": pipeline,
                "cursor": {},
            }
            explain_result = db.command(
                {"explain": explain_cmd, "verbosity": "executionStats"}
            )
            collection_name = "attendance_logs"

        elif endpoint == "department_summary":
            if not month:
                raise RequestValidationError(
                    [{"loc": ["query"], "msg": "month is required for department_summary", "type": "value_error"}]
                )
            month_start_str, month_end_exclusive_str = validate_month_param(month)
            year, mon = int(month[:4]), int(month[5:7])
            _, last_day_num = calendar.monthrange(year, mon)
            month_end_str = date(year, mon, last_day_num).isoformat()
            pipeline = build_department_summary_pipeline(
                month_start_str, month_end_exclusive_str, month_end_str, department
            )
            explain_cmd = {
                "aggregate": "employees",
                "pipeline": pipeline,
                "cursor": {},
            }
            explain_result = db.command(
                {"explain": explain_cmd, "verbosity": "executionStats"}
            )
            collection_name = "employees"

        elif endpoint == "late_leaderboard":
            if not month:
                raise RequestValidationError(
                    [{"loc": ["query"], "msg": "month is required for late_leaderboard", "type": "value_error"}]
                )
            month_start_str, month_end_exclusive_str = validate_month_param(month)
            pipeline = build_late_leaderboard_pipeline(
                month_start_str, month_end_exclusive_str, limit, department
            )
            explain_cmd = {
                "aggregate": "attendance_logs",
                "pipeline": pipeline,
                "cursor": {},
            }
            explain_result = db.command(
                {"explain": explain_cmd, "verbosity": "executionStats"}
            )
            collection_name = "attendance_logs"

        elif endpoint == "department_trend":
            if not department or not from_date or not to_date:
                raise RequestValidationError(
                    [{"loc": ["query"], "msg": "department, from, and to are required for department_trend", "type": "value_error"}]
                )
            try:
                f_date = validate_date_string(from_date)
                t_date = validate_date_string(to_date)
            except ValueError:
                raise RequestValidationError(
                    [{"loc": ["query"], "msg": "invalid date", "type": "value_error"}]
                )

            # Get employee codes for the department
            dept_employees = list(employees_col.find(
                {"department": department}, {"_id": 0, "emp_code": 1, "joined_on": 1}
            ))
            emp_codes = [e["emp_code"] for e in dept_employees] if dept_employees else ["__NONE__"]

            pipeline = build_department_trend_pipeline(
                department, from_date, to_date, emp_codes
            )
            explain_cmd = {
                "aggregate": "attendance_logs",
                "pipeline": pipeline,
                "cursor": {},
            }
            explain_result = db.command(
                {"explain": explain_cmd, "verbosity": "executionStats"}
            )
            collection_name = "attendance_logs"

        # Convert BSON types to JSON-safe values
        explain_json = json_util.loads(json_util.dumps(explain_result))

        return {
            "endpoint": endpoint,
            "collection": collection_name,
            "explain": explain_json,
        }

    except RequestValidationError:
        raise
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Explain failed: {e}")
        raise HTTPException(500, detail=f"Explain failed: {str(e)}")
