# DECISIONS.md

**1. Indexes.** `employees`: unique `emp_code` (identity, 409 on duplicates); `(department, emp_code)` for the filtered, sorted list; `(department, joined_on)` for headcount and trend. `attendance_logs`: unique `(emp_code, date)` (natural key, race safety, monthly queries); `(emp_code, punch_in desc)` for punch-out lookup; `(date desc, emp_code)` and `(status, date desc, emp_code)` for the sorted list; `(date, late_minutes desc, emp_code)` for the leaderboard.

**2. Identical punch-ins.** Both requests compute the same attendance date and call `insert_one`. The unique `(emp_code, date)` index lets exactly one succeed; the other raises `DuplicateKeyError` and returns 409. There is no check-then-insert.

**3. Leaderboard tie at the cutoff.** `$rank` gives competition ranking (1, 2, 2, 4) and I filter `rank <= limit` afterwards, so every tied employee is returned, possibly more than `limit` rows, ordered by `emp_code`.

**4. Employees with no logs.** The summary starts from `employees` (joined on or before month end), left-joins logs, and counts distinct `emp_code`s, so zero-log people count and later joiners do not.

**5. 100x data.** Pre-aggregate monthly rollups on write, copy `department` onto logs to avoid `$lookup`, use keyset instead of skip pagination, and shard `attendance_logs` by `emp_code`.