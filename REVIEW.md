<!-- # REVIEW.md

## Defects found in the starter implementation

| # | Where (function / line) | What is wrong | How you'd notice it (test, input, or symptom) | How you fixed it |
|---|---|---|---|---|
| 1 | `compute_overtime()` | The shift-end datetime is timezone-aware (`IST`), while the starter's punch timestamps are naive. Comparing them can raise `TypeError`. | Punch out after shift end can produce a server error. | Normalize timestamps to UTC-aware datetimes and calculate shift-end timestamps consistently. |
| 2 | `create_employee()` | Checking for an existing employee before insertion is not atomic; concurrent requests can create duplicate employee codes without a unique index. | Send simultaneous requests with the same `emp_code`. | Create a unique index on `emp_code` and handle `DuplicateKeyError` with HTTP 409. |
| 3 | `list_employees()` | Pagination skips `page * page_size` records, so page 1 starts after the first page. The total count also ignores the department filter. | Request page 1 with `page_size=20`, or filter by department and compare totals. | Use `(page - 1) * page_size` and count documents using the same filter. |
| 4 | `punch_in()` | The employee lookup result is not checked before accessing `emp["shift_start"]`. A nonexistent employee can cause a server error. | Punch in using an unknown employee code. | Validate employee existence and return HTTP 404. |
| 5 | `punch_in()` | Duplicate detection uses a separate read before insertion, which is vulnerable to concurrent requests. The starter also lacks a unique attendance key. | Submit simultaneous punch-in requests for the same employee and date. | Enforce a unique compound index on `(emp_code, date)` and handle duplicate-key errors with HTTP 409. |
| 6 | `punch_in()` | Timestamps are created using local system time without explicit timezone handling. This can produce inconsistent attendance dates and timestamp values across environments. | Compare attendance dates and API timestamps under different machine timezones. | Accept epoch milliseconds, normalize stored timestamps to UTC, and derive attendance dates using IST. |
| 7 | `list_attendance()` | It fetches all matching records and sorts and paginates them in Python, increasing memory use and query cost as data grows. | Populate a large attendance collection and inspect response latency and database reads. | Apply sorting, pagination, and filtering in MongoDB, with appropriate indexes. |
| 8 | Analytics and remaining endpoints | Several contract-required endpoints are missing from the starter, including punch-out, regularization, monthly analytics, department summary, late leaderboard, department trends, and endpoint explanations. | Request the documented paths in `openapi.yaml`. | Implement the missing endpoints and validate their behavior against the API contract. |

## Other observations

The starter's two-step duplicate checks are not sufficient concurrency controls. Database-enforced uniqueness is needed for employee codes and employee/date attendance records. Contract-specific business rules should also be tested against `openapi.yaml` and `PROBLEM_STATEMENT.docx` before submission. -->


# REVIEW.md

Each entry: **where**, **what is wrong**, **how you would notice it**, **fix**.

## Defects found and fixed

| # | Where | What is wrong | How you would notice | Fix |
|---|---|---|---|---|
| 1 | `late_minutes()` | Floors to whole minutes *before* the grace check (`difference > 10`), so a punch-in 10:01 after shift start counts as 0. R2 says strictly more than 10:00. | Shift 09:30, punch-in 09:40:01 returns 0, expected 10. | Compare seconds: `late = floor(seconds/60) if seconds > 600 else 0`. |
| 2 | `api_record()` | Looks for `changed_at`, but history uses `at`, so `at` is never converted. `changes[*].from/to` for punch times stay datetimes. Also leaks `created_at` / `updated_at`, and legacy docs without `half_day` / `history` / `late_minutes` return incomplete bodies. | PATCH or GET on a record with history returns ISO strings instead of epoch ms; GET `/attendance` on a legacy record lacks `half_day` and `history`. | Rebuilt as an explicit contract-shaped serialiser: ms conversion for `at` and punch `from/to`, defaults for missing fields, no internal fields. |
| 3 | PATCH `/attendance/{emp_code}/{date}`, concurrency lock | Lock is `{"history": record.get("history", [])}`. `{history: []}` does not match a document with no `history` field, so every legacy record returns 409 forever. Full-array equality is also heavy. | PATCH on a seeded record without `history` always gives 409. | Lock on history length (`$size`), or `history: null` for a missing field (then `$set` the array). Concurrent edits still produce one 200 and 409 for the loser. |
| 4 | PATCH, history entry | `changes` only holds `status`, `punch_in`, `punch_out`; derived fields that changed (`late_minutes`, `work_hours`, `overtime_minutes`, `half_day`) are missing. Punch `from/to` were stored as integers, but DATA_MODEL.md requires BSON dates in MongoDB. | Compare with DATA_MODEL.md sample 8: `late_minutes` and `work_hours` appear in `changes`. | Compute old and new state for all seven fields, record every field that differs, store instants as datetimes (converted to ms only at the API boundary). |
| 5 | PATCH, attendance-date check | Requires the IST date of the new `punch_in` to equal the record date, ignoring R1's overnight rule. | Overnight shift 22:00-06:00, correct punch-in to 03:00 on the 7th (record date the 6th): wrongly 422. | Use `attendance_day(new_punch_in, employee)`. |
| 6 | POST `/attendance/punch-out` | Searches only open records dated today/yesterday, never checks `punch_in <= punched_at`, allows `punched_at == punch_in`, and has no 24-hour limit. A repeat punch-out returns 404 instead of 409. | Punch out 3 days after punch-in: 404 instead of 422. Second sequential punch-out: 404 instead of 409. | Find the most recent record with `punch_in <= punched_at`; 404 if none; 409 if already closed; 422 if elapsed <= 0 or > 24 h. Atomic `update_one` guard on `punch_out: null` remains for simultaneous requests. New index `(emp_code, punch_in desc)` supports the lookup. |
| 7 | Trend pipeline, rate rounding | `$round` in MongoDB is half-even, then Python re-rounds the already-rounded value. R8 requires half-up. | 1 present of 32 headcount: 0.03125 gives 0.0312 instead of 0.0313. | Exact integer half-up inside the pipeline (`floor((4*present*10000 + 2*hc) / (4*hc)) / 10000`), checked against `Decimal` for headcounts 1-79 with no mismatches. |
| 8 | `/admin/explain/{endpoint}` | Returns the raw `db.command` result, which contains BSON types (`Timestamp`, `ObjectId`); FastAPI cannot serialise them, so the endpoint can return 500. | Call it on an Atlas or replica-set deployment. | `json_util.dumps` / `json.loads` round-trip (`clean_explain`). |
| 9 | Employee monthly | `attendance_pct` rounded to 4 decimals (R8: 2). Response invents fields (`absent_days`, `late_days`, `half_days`, `work_hours`, ...); `half_days` and `work_hours` are always 0 because the pipeline never computes them. | Compare response keys with `EmployeeMonthly` in openapi.yaml. | Return exactly the contract fields; compute the percentage with `Decimal`, half-up, 2 decimals. |
| 10 | Department summary | `avg_work_hours` returned unrounded. | Averages like `8.833333333`. | Round `avg_work_hours` and `present_days` half-up to 2 decimals. |
| 11 | Leaderboard pipeline | `$lookup` runs once per late log, not once per employee. | Slow on ~100k records. | `$group` by `emp_code` first, then `$lookup` employees. Ranking, tie order and department scoping unchanged. |
| 12 | Trend pipeline | Builds a date array per employee and unwinds (employees x days documents) before grouping. | Large department x 92 days. | `$limit: 1` seed employee, `$replaceRoot` to the date list. |
| 13 | Small items | `if department` / `if emp_code` treat an empty string as "no filter"; `to_ms` used float arithmetic; `millis_dt` used float division; unused `ensure_db()` and `date_range_for_month()`. | Code review. | `is not None` checks, integer arithmetic for ms, dead code removed. |

## Examined and decided fine
- Punch-in race safety: unique `(emp_code, date)` index and `DuplicateKeyError` to 409.
- R1 `attendance_day()`, R3 overtime, R4 work hours, R5 half day (rounded value, boundary 4.50) all pass spot checks.
- `$rank` gives 1, 2, 2, 4; `limit` applied after ranking; ties ordered by `emp_code`.
- Department summary headcount: starts from `employees`, includes people with no logs, excludes later joiners.
- `load_dotenv()` does not override real environment variables.
- Synchronous PyMongo inside sync endpoints runs in FastAPI's thread pool, fine for concurrency.

## Not verified
Pipelines and index usage were not run against a live MongoDB here (helpers and endpoint logic were tested with a mocked database). Check `/admin/explain/*` for IXSCAN on the 100k dataset, especially the inner `$lookup` stage of the department summary, which uses `$expr` range comparisons.