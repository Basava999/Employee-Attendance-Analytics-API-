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

    def test_halfway_boundary_even_preceding_digit(self):
        """
        Exact halfway cases where Banker's rounding rounds DOWN (even preceding digit),
        but Rule R8 half-up rounding MUST round UP.
        """
        from app.main import half_up_round
        # 0.00005 -> Banker's rounds to 0.0000 (0 is even); half-up rounds to 0.0001
        assert half_up_round(0.00005, 4) == 0.0001
        # 0.00025 -> Banker's rounds to 0.0002 (2 is even); half-up rounds to 0.0003
        assert half_up_round(0.00025, 4) == 0.0003
        # 0.00045 -> Banker's rounds to 0.0004 (4 is even); half-up rounds to 0.0005
        assert half_up_round(0.00045, 4) == 0.0005
        # 0.00065 -> Banker's rounds to 0.0006 (6 is even); half-up rounds to 0.0007
        assert half_up_round(0.00065, 4) == 0.0007
        # 0.00085 -> Banker's rounds to 0.0008 (8 is even); half-up rounds to 0.0009
        assert half_up_round(0.00085, 4) == 0.0009

    def test_halfway_boundary_odd_preceding_digit(self):
        """Halfway cases where preceding digit is odd — rounds up in both."""
        from app.main import half_up_round
        assert half_up_round(0.00015, 4) == 0.0002
        assert half_up_round(0.00035, 4) == 0.0004
        assert half_up_round(0.00055, 4) == 0.0006
        assert half_up_round(0.00075, 4) == 0.0008
        assert half_up_round(0.00095, 4) == 0.0010

    def test_mongo_half_up_round_expr_structure(self):
        """Verify the MongoDB half-up aggregation expression structure uses Decimal128 without arbitrary epsilon."""
        from bson.decimal128 import Decimal128
        from app.main import mongo_half_up_round_expr

        # When to_double=True
        expr_double = mongo_half_up_round_expr({"$divide": ["$present_count", "$headcount"]}, 4, to_double=True)
        assert "$toDouble" in expr_double
        inner = expr_double["$toDouble"]
        assert "$divide" in inner
        assert inner["$divide"][1] == Decimal128("10000")

        # When to_double=False (preserving Decimal128 through moving averages)
        expr_dec = mongo_half_up_round_expr({"$divide": ["$present_count", "$headcount"]}, 4, to_double=False)
        assert "$divide" in expr_dec
        assert expr_dec["$divide"][1] == Decimal128("10000")
        floor_expr = expr_dec["$divide"][0]
        assert "$floor" in floor_expr
        add_expr = floor_expr["$floor"]
        assert "$add" in add_expr
        assert add_expr["$add"][1] == Decimal128("0.5")
        mul_expr = add_expr["$add"][0]
        assert "$multiply" in mul_expr
        assert mul_expr["$multiply"][1] == Decimal128("10000")

        # Verify operands are converted to Decimal128 BEFORE division
        div_target = mul_expr["$multiply"][0]
        assert "$divide" in div_target
        num_op, den_op = div_target["$divide"]
        assert num_op == {"$toDecimal": "$present_count"}
        assert den_op == {"$toDecimal": "$headcount"}

    def test_mongo_half_up_round_expr_pre_division_decimal_operands(self):
        """Verify division expressions convert operands to Decimal128 prior to division."""
        from app.main import mongo_half_up_round_expr

        expr = mongo_half_up_round_expr({"$divide": ["$present_count", "$headcount"]}, 4)
        div_expr = expr["$divide"][0]["$floor"]["$add"][0]["$multiply"][0]
        assert div_expr["$divide"][0] == {"$toDecimal": "$present_count"}
        assert div_expr["$divide"][1] == {"$toDecimal": "$headcount"}

    def test_half_up_boundaries_below_at_above(self):
        """
        Verify non-negative half-up rounding (R8) immediately below, exactly at,
        and immediately above halfway boundaries for 4 decimal places.
        Must round up at .xxxx5 even when preceding digit is even (unlike Banker's rounding).
        """
        from app.main import half_up_round

        # (input_val, expected_rounded, description)
        cases = [
            # 0.00005 boundary (Banker's rounds to 0.0000, half-up MUST round to 0.0001)
            (0.00004999, 0.0000, "below 0.00005"),
            (0.00005,    0.0001, "exactly at 0.00005"),
            (0.00005001, 0.0001, "above 0.00005"),

            # 0.00015 boundary
            (0.00014999, 0.0001, "below 0.00015"),
            (0.00015,    0.0002, "exactly at 0.00015"),
            (0.00015001, 0.0002, "above 0.00015"),

            # 0.00025 boundary (Banker's rounds to 0.0002, half-up MUST round to 0.0003)
            (0.00024999, 0.0002, "below 0.00025"),
            (0.00025,    0.0003, "exactly at 0.00025"),
            (0.00025001, 0.0003, "above 0.00025"),

            # 0.00035 boundary
            (0.00034999, 0.0003, "below 0.00035"),
            (0.00035,    0.0004, "exactly at 0.00035"),
            (0.00035001, 0.0004, "above 0.00035"),

            # 0.00045 boundary (Banker's rounds to 0.0004, half-up MUST round to 0.0005)
            (0.00044999, 0.0004, "below 0.00045"),
            (0.00045,    0.0005, "exactly at 0.00045"),
            (0.00045001, 0.0005, "above 0.00045"),

            # 0.00055 boundary
            (0.00054999, 0.0005, "below 0.00055"),
            (0.00055,    0.0006, "exactly at 0.00055"),
            (0.00055001, 0.0006, "above 0.00055"),

            # 0.00065 boundary (Banker's rounds to 0.0006, half-up MUST round to 0.0007)
            (0.00064999, 0.0006, "below 0.00065"),
            (0.00065,    0.0007, "exactly at 0.00065"),
            (0.00065001, 0.0007, "above 0.00065"),

            # 0.00075 boundary
            (0.00074999, 0.0007, "below 0.00075"),
            (0.00075,    0.0008, "exactly at 0.00075"),
            (0.00075001, 0.0008, "above 0.00075"),

            # 0.00085 boundary (Banker's rounds to 0.0008, half-up MUST round to 0.0009)
            (0.00084999, 0.0008, "below 0.00085"),
            (0.00085,    0.0009, "exactly at 0.00085"),
            (0.00085001, 0.0009, "above 0.00085"),

            # 0.00095 boundary
            (0.00094999, 0.0009, "below 0.00095"),
            (0.00095,    0.0010, "exactly at 0.00095"),
            (0.00095001, 0.0010, "above 0.00095"),
        ]

        for val, expected, desc in cases:
            actual = half_up_round(val, 4)
            assert actual == expected, f"Failed for {desc}: half_up_round({val}, 4) = {actual}, expected {expected}"

    def test_rate_calculations_half_up_boundaries(self):
        """
        Verify rate calculations (e.g. attendance_rate = present_count / headcount)
        rounded half-up to 4 decimal places at, below, and above boundaries.
        """
        from app.main import half_up_round

        # 1 / 32 = 0.03125 (halfway case with even 4th decimal '2' — tests non-banker rounding)
        rate_1_32 = 1 / 32
        assert rate_1_32 == 0.03125
        assert half_up_round(rate_1_32, 4) == 0.0313
        assert half_up_round(0.03124999, 4) == 0.0312
        assert half_up_round(0.03125001, 4) == 0.0313

        # 3 / 32 = 0.09375
        assert half_up_round(3 / 32, 4) == 0.0938
        assert half_up_round(0.09374999, 4) == 0.0937
        assert half_up_round(0.09375001, 4) == 0.0938

        # 5 / 32 = 0.15625 (even 4th decimal '2' — tests non-banker rounding)
        assert half_up_round(5 / 32, 4) == 0.1563
        assert half_up_round(0.15624999, 4) == 0.1562
        assert half_up_round(0.15625001, 4) == 0.1563

        # 9 / 32 = 0.28125
        assert half_up_round(9 / 32, 4) == 0.2813
        assert half_up_round(0.28124999, 4) == 0.2812
        assert half_up_round(0.28125001, 4) == 0.2813

        # Standard clean rates
        assert half_up_round(1 / 16, 4) == 0.0625
        assert half_up_round(1 / 8, 4) == 0.1250
        assert half_up_round(1 / 6, 4) == 0.1667
        assert half_up_round(1 / 3, 4) == 0.3333
        assert half_up_round(2 / 3, 4) == 0.6667
        assert half_up_round(5 / 6, 4) == 0.8333

    def test_moving_average_half_up_boundaries(self):
        """
        Verify 7-day moving average calculations rounded half-up to 4 decimal places
        immediately below, exactly at, and immediately above halfway boundaries.
        """
        from app.main import half_up_round

        # Window with 2 non-null rates: 0.0312 and 0.0313 -> avg = 0.03125 (exact halfway)
        avg_2d = (0.0312 + 0.0313) / 2
        assert avg_2d == 0.03125
        assert half_up_round(avg_2d, 4) == 0.0313
        assert half_up_round(avg_2d - 0.00000001, 4) == 0.0312
        assert half_up_round(avg_2d + 0.00000001, 4) == 0.0313

        # Window with 4 rates: sum = 0.0006 -> avg = 0.00015 (exact halfway)
        from decimal import Decimal
        avg_4d_odd = float((Decimal('0.0001') * 2 + Decimal('0.0002') * 2) / 4)
        assert avg_4d_odd == 0.00015
        assert half_up_round(avg_4d_odd, 4) == 0.0002
        assert half_up_round(0.00014999, 4) == 0.0001
        assert half_up_round(0.00015001, 4) == 0.0002

        # Window with 4 rates: sum = 0.0010 -> avg = 0.00025 (exact halfway with even '2')
        avg_4d_even = float((Decimal('0.0002') * 2 + Decimal('0.0003') * 2) / 4)
        assert avg_4d_even == 0.00025
        assert half_up_round(avg_4d_even, 4) == 0.0003
        assert half_up_round(0.00024999, 4) == 0.0002
        assert half_up_round(0.00025001, 4) == 0.0003

        # Window with 7 rates: sum = 0.00175 -> avg = 0.00025
        avg_7d = float((Decimal('0.0002') * 2 + Decimal('0.0003') * 2 + Decimal('0.00025') * 3) / 7)
        assert avg_7d == 0.00025
        assert half_up_round(avg_7d, 4) == 0.0003

    def test_mongo_live_half_up_round_expression(self):
        """
        Verify mongo_half_up_round_expr against actual MongoDB results when available.
        Skips cleanly if MongoDB is not reachable.
        """
        import os
        import pytest
        from pymongo import MongoClient
        from app.main import mongo_half_up_round_expr

        mongo_uri = os.getenv("MONGO_URI", "mongodb://localhost:27017")
        try:
            client = MongoClient(mongo_uri, serverSelectionTimeoutMS=1000)
            client.admin.command("ping")
        except Exception:
            pytest.skip("MongoDB not available for live aggregation evaluation")

        db = client["test_rounding_verify"]
        coll = db["temp_rounding_test"]
        try:
            coll.delete_many({})
            test_data = [
                {"_id": 1, "val": 0.00004999, "expected": 0.0000, "num": 1, "den": 20000},
                {"_id": 2, "val": 0.00005,    "expected": 0.0001, "num": 1, "den": 20000},
                {"_id": 3, "val": 0.00005001, "expected": 0.0001, "num": 1, "den": 19996},
                {"_id": 4, "val": 0.00014999, "expected": 0.0001, "num": 3, "den": 20001},
                {"_id": 5, "val": 0.00015,    "expected": 0.0002, "num": 3, "den": 20000},
                {"_id": 6, "val": 0.00015001, "expected": 0.0002, "num": 3, "den": 19998},
                {"_id": 7, "val": 0.00024999, "expected": 0.0002, "num": 1, "den": 4000},
                {"_id": 8, "val": 0.00025,    "expected": 0.0003, "num": 1, "den": 4000},
                {"_id": 9, "val": 0.00025001, "expected": 0.0003, "num": 1, "den": 3999},
                {"_id": 10, "val": 0.03125,   "expected": 0.0313, "num": 1, "den": 32},
                {"_id": 11, "val": 0.09375,   "expected": 0.0938, "num": 3, "den": 32},
                {"_id": 12, "val": 0.15625,   "expected": 0.1563, "num": 5, "den": 32},
            ]
            coll.insert_many(test_data)
            pipeline = [
                {
                    "$project": {
                        "val": 1,
                        "expected": 1,
                        "rounded_direct": mongo_half_up_round_expr("$val", 4, to_double=True),
                        "rounded_divide": mongo_half_up_round_expr({"$divide": ["$num", "$den"]}, 4, to_double=True),
                    }
                },
                {"$sort": {"_id": 1}}
            ]
            results = list(coll.aggregate(pipeline))
            for doc in results:
                assert abs(doc["rounded_direct"] - doc["expected"]) < 1e-9, \
                    f"Live MongoDB direct rounding mismatch for val {doc['val']}: got {doc['rounded_direct']}, expected {doc['expected']}"
            # Specific division test check
            assert abs(results[9]["rounded_divide"] - 0.0313) < 1e-9  # 1/32
            assert abs(results[10]["rounded_divide"] - 0.0938) < 1e-9  # 3/32
            assert abs(results[11]["rounded_divide"] - 0.1563) < 1e-9  # 5/32
        finally:
            coll.drop()
            client.close()

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

    def test_overnight_boundary_instant_before(self):
        """Overnight 22:00-06:00, punch in at 05:59:59 IST on July 7th → date 2026-07-06."""
        from app.main import compute_attendance_date
        # 05:59:59 IST July 7th = 00:29:59 UTC July 7th
        dt = datetime(2026, 7, 7, 0, 29, 59, tzinfo=timezone.utc)
        result = compute_attendance_date(dt, "22:00", "06:00")
        assert result == "2026-07-06"

    def test_overnight_boundary_exact_instant(self):
        """Overnight 22:00-06:00, punch in at 06:00:00 IST on July 7th → date 2026-07-07."""
        from app.main import compute_attendance_date
        # 06:00:00 IST July 7th = 00:30:00 UTC July 7th
        dt = datetime(2026, 7, 7, 0, 30, 0, tzinfo=timezone.utc)
        result = compute_attendance_date(dt, "22:00", "06:00")
        assert result == "2026-07-07"

    def test_overnight_boundary_instant_after(self):
        """Overnight 22:00-06:00, punch in at 06:00:01 IST on July 7th → date 2026-07-07."""
        from app.main import compute_attendance_date
        # 06:00:01 IST July 7th = 00:30:01 UTC July 7th
        dt = datetime(2026, 7, 7, 0, 30, 1, tzinfo=timezone.utc)
        result = compute_attendance_date(dt, "22:00", "06:00")
        assert result == "2026-07-07"


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


class TestStartupIndexChecks:
    """Test startup fail-fast behavior and index establishment verification."""

    def test_create_indexes_ping_failure_raises_runtime_error(self):
        """A failed MongoDB ping during startup index check must raise a clear RuntimeError."""
        from unittest.mock import patch
        import pymongo
        from pymongo.errors import ConnectionFailure
        from app.main import create_indexes

        with patch.object(pymongo.database.Database, "command", side_effect=ConnectionFailure("Connection refused")):
            with pytest.raises(RuntimeError, match="Startup failed: Database ping failed during index creation"):
                create_indexes()

    def test_create_indexes_missing_required_index_raises_runtime_error(self):
        """Failure to establish required unique indexes must raise RuntimeError before serving requests."""
        from unittest.mock import patch
        import pymongo
        from app.main import create_indexes, employees_col, attendance_col

        with patch.object(pymongo.database.Database, "command", return_value={"ok": 1}), \
             patch.object(employees_col, "create_index", return_value="idx_emp_code_unique"), \
             patch.object(attendance_col, "create_index", return_value="idx_empcode_date_unique"), \
             patch.object(employees_col, "index_information", return_value={}), \
             patch.object(attendance_col, "index_information", return_value={"idx_empcode_date_unique": {}}):
            with pytest.raises(RuntimeError, match="Startup failed: Required index 'idx_emp_code_unique' on collection 'employees' was not established"):
                create_indexes()

    def test_create_indexes_missing_performance_index_raises_runtime_error(self):
        """Failure to establish any required performance index must also raise RuntimeError before serving requests."""
        from unittest.mock import patch
        import pymongo
        from app.main import create_indexes, employees_col, attendance_col

        with patch.object(pymongo.database.Database, "command", return_value={"ok": 1}), \
             patch.object(employees_col, "create_index", return_value=None), \
             patch.object(attendance_col, "create_index", return_value=None), \
             patch.object(employees_col, "index_information", return_value={"_id_": {}, "idx_emp_code_unique": {}}), \
             patch.object(attendance_col, "index_information", return_value={"_id_": {}, "idx_empcode_date_unique": {}, "idx_date_desc_empcode": {}}):
            # Missing idx_dept_joined on employees
            with pytest.raises(RuntimeError, match="Startup failed: Required index 'idx_dept_joined' on collection 'employees' was not established"):
                create_indexes()

    def test_create_indexes_success_when_all_indexes_confirmed(self):
        """When ping succeeds and all five required indexes are confirmed, create_indexes completes cleanly."""
        from unittest.mock import patch
        import pymongo
        from app.main import create_indexes, employees_col, attendance_col

        emp_indexes = {
            "_id_": {},
            "idx_emp_code_unique": {},
            "idx_dept_joined": {},
            "idx_joined_on": {},
        }
        att_indexes = {
            "_id_": {},
            "idx_empcode_date_unique": {},
            "idx_date_desc_empcode": {},
        }

        with patch.object(pymongo.database.Database, "command", return_value={"ok": 1}), \
             patch.object(employees_col, "create_index", return_value=None), \
             patch.object(attendance_col, "create_index", return_value=None), \
             patch.object(employees_col, "index_information", return_value=emp_indexes), \
             patch.object(attendance_col, "index_information", return_value=att_indexes):
            # Must not raise
            create_indexes()

