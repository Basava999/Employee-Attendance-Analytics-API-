# DECISIONS.md

1. **Indexes:** Four indexes:
- `{emp_code: 1}` (unique) on `employees`: enforces uniqueness on creation.
- `{department: 1, joined_on: 1}` on `employees`: covers department filters and headcount queries (`joined_on <= end`).
- `{emp_code: 1, date: 1}` (unique) on `attendance_logs`: prevents duplicate punches and optimizes `$lookup` joins.
- `{date: -1, emp_code: 1}` on `attendance_logs`: serves list sorting and date ranges without in-memory sorts.
*Rejected:* `{status: 1, date: -1}`—rejected because status filters are rare and date-leading scans satisfy sorting without burdening write throughput.

2. **Punch-in race:** Both concurrent requests pass validation. The compound unique index `{emp_code, date}` admits exactly one `insert_one`. The winner returns 201; the loser triggers `DuplicateKeyError`, caught and returned as 409.

3. **Ties:** The pipeline uses `$setWindowFields` with `$rank` (1,2,2,4 ranking). Filtering `rank <= limit` returns all tied employees at the cutoff, which can exceed `limit` rows.

4. **Headcount:** The pipeline drives from `employees` filtering `joined_on <= month_end`, then `$lookup` joins logs with `preserveNullAndEmptyArrays: true`. Zero-log employees are preserved, contributing to headcount.

5. **100x Data:** Shard `attendance_logs` by `emp_code`, route analytics to read replicas, and maintain materialized monthly rollups updated on write to eliminate multi-million document scans.
