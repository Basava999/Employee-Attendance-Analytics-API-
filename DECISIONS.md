# DECISIONS.md  (about 150-200 words in total - plain language, your own words)

1. **Indexes.** Four indexes: (a) unique `{emp_code:1}` on employees — enforces uniqueness for concurrent creates; (b) `{department:1, joined_on:1}` on employees — serves department filtering and headcount queries (`joined_on <= X`); (c) unique `{emp_code:1, date:1}` on attendance_logs — enforces one record per employee per day and supports employee+month lookups and `$lookup` joins; (d) `{date:-1, emp_code:1}` on attendance_logs — serves the attendance list sort and date-range analytics without a blocking sort. I considered a `{status:1, date:-1}` index for status-only queries but rejected it: the date-based index already covers the sort, and adding a status-leading index would slow writes for a rare filter.

2. **Punch-in race.** Both requests pass body validation, find the employee, and call `insert_one`. The unique `{emp_code, date}` index allows exactly one. The loser gets a `DuplicateKeyError`, caught and returned as 409.

3. **Ties.** The pipeline uses `$setWindowFields` with `$rank` (competition ranking: 1,2,2,4). The filter is `rank <= limit`, so both tied employees at the cutoff are returned. Output may exceed `limit` rows.

4. **Headcount.** The department summary pipeline starts from `employees` with `joined_on <= month_end`, then `$lookup` attendance. Employees with zero logs still appear because the `$unwind` uses `preserveNullAndEmptyArrays: true`. They contribute to headcount but not to attendance totals.

5. **One thing you would change** at 100x data: add read replicas for analytics queries, shard `attendance_logs` by `emp_code` for write distribution, and pre-aggregate monthly summaries into a materialized collection updated on each write to avoid scanning millions of records per analytics request.
