# DECISIONS.md

1. **Indexes:** Five indexes: `{emp_code: 1}` (unique) on `employees` for identity; `{department: 1, joined_on: 1}` on `employees` for department filtering/headcount; `{joined_on: 1}` on `employees` for unfiltered summary scans; `{emp_code: 1, date: 1}` (unique) on `attendance_logs` to block duplicate daily punches and power `$lookup`; `{date: -1, emp_code: 1}` on `attendance_logs` for sorted range scans. Verified via `index_information()` at lifespan startup (missing indexes fail fast). Rejected: `{status: 1, date: -1}`—status queries are secondary and covered by date scans.

2. **Punch-in race:** Both concurrent requests pass validation. The unique index `{emp_code, date}` admits exactly one insertion (201); conflicting writes throw `DuplicateKeyError`, returning 409. Concurrent `punch_out` and regularization enforce atomic conditional updates guarding `status`, `punch_in`, `punch_out`, and history length to prevent stale overwrites.

3. **Ties:** The pipeline uses `$setWindowFields` with `$rank` (1,2,2,4 ranking). Filtering `rank <= limit` returns all tied employees at the boundary, which may exceed `limit` items.

4. **Headcount:** The pipeline drives from `employees` matching `joined_on <= period_end`, followed by `$lookup` with `preserveNullAndEmptyArrays: true`. Employees with zero logs are preserved throughout.

5. **100x Data:** Shard `attendance_logs` by `emp_code`, route heavy analytics to secondary read replicas, and maintain monthly pre-aggregated rollups updated on punch events.
