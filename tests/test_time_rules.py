"""
Unit tests for time, rounding, and calculation helpers.
These do NOT require MongoDB — they test pure functions.
"""
import pytest
from datetime import datetime, timezone, timedelta

# Import helpers directly from main
import sys
import os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

# We need to set env vars before importing main
os.environ.setdefault("MONGO_URI", "mongodb://localhost:27017")
os.environ.setdefault("MONGO_DB", "attendance_test_unit")


class TestHalfUpRounding:
    """Test the half-up rounding helper."""

    def test_basic_rounding(self):
        from app.main import half_up_round
        assert half_up_round(9.125, 2) == 9.13  # half-up, not banker's 9.12
        assert half_up_round(9.124, 2) == 9.12
        assert half_up_round(9.126, 2) == 9.13

    def test_half_up_not_bankers(self):
        """450 seconds = 0.125 hours. round() gives 0.12, half-up gives 0.13."""
        from app.main import half_up_round
        assert half_up_round(0.125, 2) == 0.13
        assert half_up_round(0.135, 2) == 0.14
        assert half_up_round(0.145, 2) == 0.15

    def test_four_decimal_rates(self):
        from app.main import half_up_round
        assert half_up_round(0.12345, 4) == 0.1235
        assert half_up_round(0.12344, 4) == 0.1234

    def test_none_returns_none(self):
        from app.main import half_up_round
        assert half_up_round(None, 2) is None


class TestWorkHoursFromSeconds:
    """Test the integer-arithmetic work_hours computation."""

    def test_sample_1_emp0001(self):
        """09:28 IST to 18:35 IST = 9h7m = 32820s → 9.12 hours."""
        from app.main import compute_work_hours_from_seconds
        secs = 9 * 3600 + 7 * 60  # 32820
        assert compute_work_hours_from_seconds(secs) == 9.12

    def test_sample_2_emp0003(self):
        """10:05 IST to 19:10 IST = 9h5m = 32700s → 9.08 hours."""
        from app.main import compute_work_hours_from_seconds
        secs = 9 * 3600 + 5 * 60  # 32700
        assert compute_work_hours_from_seconds(secs) == 9.08

    def test_half_up_boundary_450_seconds(self):
        """450s = 0.125h. round() gives 0.12, half-up gives 0.13."""
        from app.main import compute_work_hours_from_seconds
        assert compute_work_hours_from_seconds(450) == 0.13

    def test_half_day_boundary_16200(self):
        """16200s = 4.50h exactly → NOT half day."""
        from app.main import compute_work_hours_from_seconds, is_half_day_from_seconds
        assert compute_work_hours_from_seconds(16200) == 4.50
        assert is_half_day_from_seconds(16200) == False

    def test_half_day_boundary_16218(self):
        """16218s rounds to 4.51 → NOT half day. round() would give 4.50."""
        from app.main import compute_work_hours_from_seconds, is_half_day_from_seconds
        result = compute_work_hours_from_seconds(16218)
        assert result == 4.51  # half-up
        assert is_half_day_from_seconds(16218) == False

    def test_half_day_boundary_16178(self):
        """16178s → rounds to 4.49 → IS half day."""
        from app.main import compute_work_hours_from_seconds, is_half_day_from_seconds
        result = compute_work_hours_from_seconds(16178)
        assert result == 4.49
        assert is_half_day_from_seconds(16178) == True

    def test_sample_4_half_day(self):
        """3.5 hours = 12600s → 3.50."""
        from app.main import compute_work_hours_from_seconds, is_half_day_from_seconds
        assert compute_work_hours_from_seconds(12600) == 3.50
        assert is_half_day_from_seconds(12600) == True

    def test_sample_7_overnight(self):
        """21:55 to 06:40 next day = 8h45m = 31500s → 8.75."""
        from app.main import compute_work_hours_from_seconds
        secs = 8 * 3600 + 45 * 60  # 31500
        assert compute_work_hours_from_seconds(secs) == 8.75


class TestEpochMsConversion:
    """Test epoch ms ↔ UTC datetime conversion."""

    def test_known_epoch(self):
        """1783312500000 ms = 2026-07-06T04:35:00Z (= 10:05 IST)."""
        from app.main import epoch_ms_to_utc_dt, dt_to_epoch_ms
        dt = epoch_ms_to_utc_dt(1783312500000)
        assert dt.year == 2026
        assert dt.month == 7
        assert dt.day == 6
        assert dt.hour == 4
        assert dt.minute == 35
        assert dt.second == 0
        assert dt.tzinfo is not None
        # Round-trip
        assert dt_to_epoch_ms(dt) == 1783312500000

    def test_truncation_to_seconds(self):
        """Sub-second part is dropped."""
        from app.main import epoch_ms_to_utc_dt
        # 1783310400900 = ...00:00.900
        dt = epoch_ms_to_utc_dt(1783310400900)
        assert dt.microsecond == 0

    def test_none_returns_none(self):
        from app.main import dt_to_epoch_ms
        assert dt_to_epoch_ms(None) is None


class TestAttendanceDate:
    """Test the R1 attendance date computation."""

    def test_normal_day_shift(self):
        """09:28 IST on July 6th → date 2026-07-06."""
        from app.main import compute_attendance_date, epoch_ms_to_utc_dt
        # 09:28 IST = 03:58 UTC on 2026-07-06
        dt = datetime(2026, 7, 6, 3, 58, 0, tzinfo=timezone.utc)
        result = compute_attendance_date(dt, "09:30", "18:30")
        assert result == "2026-07-06"

    def test_overnight_normal_punch(self):
        """Overnight 22:00-06:00, punch in at 21:55 IST → date same day."""
        from app.main import compute_attendance_date
        # 21:55 IST = 16:25 UTC on 2026-07-06
        dt = datetime(2026, 7, 6, 16, 25, 0, tzinfo=timezone.utc)
        result = compute_attendance_date(dt, "22:00", "06:00")
        assert result == "2026-07-06"

    def test_overnight_early_morning_punch(self):
        """Overnight 22:00-06:00, punch in at 02:00 IST on D+1 → date D."""
        from app.main import compute_attendance_date
        # 02:00 IST July 7th = 20:30 UTC July 6th
        dt = datetime(2026, 7, 6, 20, 30, 0, tzinfo=timezone.utc)
        result = compute_attendance_date(dt, "22:00", "06:00")
        assert result == "2026-07-06"


class TestLateMinutes:
    """Test the R2 late minutes computation."""

    def test_within_grace_09_40_00(self):
        """Shift 09:30, punch at 09:40:00 → exactly 600s → not late → 0."""
        from app.main import compute_late_minutes, anchor_shift_datetime
        # 09:40:00 IST = 04:10:00 UTC
        punch = datetime(2026, 7, 6, 4, 10, 0, tzinfo=timezone.utc)
        result = compute_late_minutes(punch, "09:30", "2026-07-06")
        assert result == 0

    def test_one_second_late_09_40_01(self):
        """Shift 09:30, punch at 09:40:01 → 601s > 600 → late_minutes=10."""
        from app.main import compute_late_minutes
        # 09:40:01 IST = 04:10:01 UTC
        punch = datetime(2026, 7, 6, 4, 10, 1, tzinfo=timezone.utc)
        result = compute_late_minutes(punch, "09:30", "2026-07-06")
        assert result == 10

    def test_very_late_10_15_59(self):
        """Shift 09:30, punch at 10:15:59 → 2759s → 45 min late."""
        from app.main import compute_late_minutes
        # 10:15:59 IST = 04:45:59 UTC
        punch = datetime(2026, 7, 6, 4, 45, 59, tzinfo=timezone.utc)
        result = compute_late_minutes(punch, "09:30", "2026-07-06")
        assert result == 45

    def test_early_punch_gives_zero(self):
        """Punch before shift start → 0."""
        from app.main import compute_late_minutes
        # 09:28 IST = 03:58 UTC
        punch = datetime(2026, 7, 6, 3, 58, 0, tzinfo=timezone.utc)
        result = compute_late_minutes(punch, "09:30", "2026-07-06")
        assert result == 0

    def test_sample_2_emp0003(self):
        """10:05 IST for 09:30 shift → 35 min late."""
        from app.main import compute_late_minutes
        # 10:05 IST = 04:35 UTC
        punch = datetime(2026, 7, 6, 4, 35, 0, tzinfo=timezone.utc)
        result = compute_late_minutes(punch, "09:30", "2026-07-06")
        assert result == 35


class TestOvertimeMinutes:
    """Test the R3 overtime computation."""

    def test_under_threshold_29_min(self):
        """29 min after shift end → 0 (under 30 min threshold)."""
        from app.main import compute_overtime_minutes
        # Shift end 18:30 IST = 13:00 UTC. Punch out at 18:59 IST = 13:29 UTC
        punch_out = datetime(2026, 7, 6, 13, 29, 0, tzinfo=timezone.utc)
        result = compute_overtime_minutes(punch_out, "18:30", "09:30", "2026-07-06")
        assert result == 0

    def test_exactly_30_min(self):
        """Exactly 30 min after shift end → 30."""
        from app.main import compute_overtime_minutes
        # Shift end 18:30 IST = 13:00 UTC. Punch out at 19:00 IST = 13:30 UTC
        punch_out = datetime(2026, 7, 6, 13, 30, 0, tzinfo=timezone.utc)
        result = compute_overtime_minutes(punch_out, "18:30", "09:30", "2026-07-06")
        assert result == 30

    def test_sample_2_emp0003(self):
        """19:10 IST with shift end 18:30 → 40 min overtime."""
        from app.main import compute_overtime_minutes
        # 19:10 IST = 13:40 UTC
        punch_out = datetime(2026, 7, 6, 13, 40, 0, tzinfo=timezone.utc)
        result = compute_overtime_minutes(punch_out, "18:30", "09:30", "2026-07-06")
        assert result == 40

    def test_overnight_shift_overtime(self):
        """Overnight 22:00-06:00, punch out at 06:40 IST next day → 40 min OT."""
        from app.main import compute_overtime_minutes
        # 06:40 IST July 7th = 01:10 UTC July 7th
        punch_out = datetime(2026, 7, 7, 1, 10, 0, tzinfo=timezone.utc)
        result = compute_overtime_minutes(punch_out, "06:00", "22:00", "2026-07-06")
        assert result == 40

    def test_punch_out_before_shift_end(self):
        """Punch out before shift end → 0."""
        from app.main import compute_overtime_minutes
        # 18:00 IST = 12:30 UTC
        punch_out = datetime(2026, 7, 6, 12, 30, 0, tzinfo=timezone.utc)
        result = compute_overtime_minutes(punch_out, "18:30", "09:30", "2026-07-06")
        assert result == 0


class TestCountWorkingDays:
    """Test the working days counter."""

    def test_full_month_july_2026(self):
        """July 2026: starts Wed, 23 working days."""
        from app.main import count_working_days
        from datetime import date
        start = date(2026, 7, 1)
        end = date(2026, 7, 31)
        joined = date(2026, 1, 1)
        assert count_working_days(start, end, joined) == 23

    def test_mid_month_joiner(self):
        """EMP0002 joined July 13 (Monday). Working days 13-31 = 15."""
        from app.main import count_working_days
        from datetime import date
        start = date(2026, 7, 1)
        end = date(2026, 7, 31)
        joined = date(2026, 7, 13)
        assert count_working_days(start, end, joined) == 15

    def test_joined_after_month_end(self):
        """Joined after the month → 0 working days."""
        from app.main import count_working_days
        from datetime import date
        start = date(2026, 7, 1)
        end = date(2026, 7, 31)
        joined = date(2026, 8, 1)
        assert count_working_days(start, end, joined) == 0

    def test_joined_exactly_on_last_day(self):
        """Joined on the last day of the month — if it's a weekday, 1 day."""
        from app.main import count_working_days
        from datetime import date
        start = date(2026, 7, 1)
        end = date(2026, 7, 31)  # July 31 2026 = Friday
        joined = date(2026, 7, 31)
        assert count_working_days(start, end, joined) == 1
