
# HROne Attendance & Analytics API

A backend attendance management and analytics API built with FastAPI, Python 3.11+, and MongoDB. The application supports employee management, attendance tracking, attendance regularization, audit history, and department-level analytics.

All application code is contained in `app/main.py`.

## Features

- **Employee Management:** Create employees and retrieve paginated employee lists with department-based filtering.
- **Attendance Tracking:** Record punch-ins and punch-outs, including overnight shifts.
- **Business Rules:** Calculate late minutes, working hours, overtime, and half-day status.
- **Attendance Regularization:** Correct attendance records while preserving an append-only audit history.
- **Employee Analytics:** Generate monthly attendance summaries for individual employees.
- **Department Analytics:** Retrieve department-level statistics and daily attendance trends.
- **Late Attendance Leaderboard:** Rank employees based on late attendance.
- **Moving Averages:** Calculate seven-day moving averages for department attendance trends.
- **Concurrency Safety:** Prevent duplicate punch-ins and punch-outs and preserve audit history during concurrent corrections.
- **Database Optimization:** Create required MongoDB indexes at startup and expose query execution statistics.
- **API Documentation:** Provide interactive Swagger UI and ReDoc documentation.
- **Automated Testing:** Include a pytest test suite covering application functionality and business rules.

## Tech Stack

- Python 3.11+
- FastAPI
- MongoDB 6.0+
- PyMongo or Motor, as configured
- Uvicorn
- Pytest

## Prerequisites

Ensure the following are installed:

- Python 3.11 or later
- MongoDB 6.0 or later
- Git
- pip or uv

## Getting Started

### 1. Clone the Repository

```bash
git clone https://github.com/Mohtashimkhan22/HROne-attendance-API.git
cd HROne-attendance-API
```

### 2. Install Dependencies

Using pip:

```bash
pip install -r requirements.txt
```

Alternatively, if the project is configured for uv:

```bash
uv sync
```

### 3. Configure Environment Variables

The application requires `MONGO_URI`. The database name defaults to `attendance_db` if `MONGO_DB` is not specified.

**Linux / macOS**

```bash
export MONGO_URI="mongodb://localhost:27017"
export MONGO_DB="attendance_db"
```

**Windows PowerShell**

```powershell
$env:MONGO_URI = "mongodb://localhost:27017"
$env:MONGO_DB = "attendance_db"
```

You can also configure these variables in a `.env` file:

```dotenv
MONGO_URI=mongodb://localhost:27017
MONGO_DB=attendance_db
```

Actual environment variables take precedence over values loaded from `.env`.

Ensure MongoDB is running and accessible before starting the application.

### 4. Start the Application

Run the following command from the repository root:

```bash
uvicorn app.main:app --port 8000
```

The application initializes the required database indexes at startup. It is designed to work with the existing seeded dataset without requiring the dataset to be recreated.

## API Documentation

Once the server is running, access the following endpoints:

- **Health Check:** http://localhost:8000/health
- **Swagger UI:** http://localhost:8000/docs
- **ReDoc:** http://localhost:8000/redoc

Swagger UI allows you to explore endpoints, inspect request and response schemas, and execute API requests interactively.

The health endpoint checks MongoDB connectivity and returns an appropriate HTTP status.

## API Endpoints

| Method | Endpoint | Description |
|---|---|---|
| POST | `/employees` | Create an employee |
| GET | `/employees` | List employees with filtering and pagination |
| POST | `/attendance/punch-in` | Record employee punch-in |
| POST | `/attendance/punch-out` | Record employee punch-out |
| GET | `/attendance` | Query attendance records |
| PATCH | `/attendance/{emp_code}/{date}` | Regularize an attendance record |
| GET | `/analytics/employees/{emp_code}/monthly` | Retrieve an employee's monthly summary |
| GET | `/analytics/departments/summary` | Retrieve department-level statistics |
| GET | `/analytics/leaderboard/late` | Rank employees by late attendance |
| GET | `/analytics/departments/{department}/trend` | Retrieve daily department trends |
| GET | `/admin/explain/{endpoint}` | Inspect query execution statistics |
| GET | `/health` | Check application and database availability |

For exact request schemas, query parameters, response fields, and status codes, refer to the interactive Swagger documentation at `/docs`.

## Business Rules and Data Handling

### Timestamp and Date Handling

- API instants use integer epoch milliseconds.
- MongoDB stores timestamps as BSON UTC datetimes.
- Calendar dates, month values, and shift times follow Indian Standard Time (IST) conventions.
- Attendance records are identified by `emp_code` and date.
- MongoDB `_id` values are not exposed through the API.

### Attendance Calculations

The application supports:

- Late arrival calculations in minutes.
- Total working hours.
- Overtime calculations.
- Half-day attendance status.
- Presence calculations that distinguish present statuses from `ABSENT` and `LEAVE`.
- Punch-in and punch-out handling for overnight shifts.

All calculations follow the business rules implemented in the application.

### Attendance Regularization and Audit History

Attendance corrections preserve audit information, including:

- The actor performing the correction.
- The correction timestamp.
- The reason for the correction.
- The affected fields and their changed values.

The audit history is designed to be append-only. Concurrent regularization operations must preserve existing audit entries and prevent lost updates.

### Pagination

List endpoints support pagination with the following conventions:

- Page numbering starts at `1`.
- Maximum page size is `100`.
- Employee lists support department-based filtering.

## MongoDB Indexing and Analytics

The application creates the required MongoDB indexes idempotently during startup.

Analytics are implemented using MongoDB aggregation pipelines rather than fetching complete attendance datasets into Python for application-side aggregation.

Supported query types for execution-plan inspection include:

- `attendance_list`
- `employee_monthly`
- `department_summary`
- `late_leaderboard`
- `department_trend`

The explain endpoint exposes MongoDB `executionStats` for supported query types. These statistics help verify index usage, inspect documents examined, and identify expensive database operations.

## Testing

Run the test suite from the repository root:

```bash
pytest -q
```

### Test Results

**Recorded local test result: 30 tests passed.**

The tests use the database configured through `MONGO_DB`. Configure a dedicated, disposable test database before running the suite to avoid modifying development or production data.

## Performance Validation

The implementation was tested locally against a seeded dataset containing approximately:

- 500 employees
- 100,000 attendance records

Recorded observations:

| Validation | Observed Result |
|---|---|
| Tested query execution plans | `IXSCAN` used without `COLLSCAN` |
| Department trend query over 92 days | Approximately 50 ms |

These results reflect the local test environment and tested query patterns. They are not guaranteed production performance benchmarks. Actual performance depends on hardware, database configuration, indexes, dataset distribution, and concurrent workload.

## Project Structure

```text
HROne-attendance-API/
├── app/
│   └── main.py
├── requirements.txt
├── README.md
├── REVIEW.md
└── DECISIONS.md
```

### Key Files

- `app/main.py` — Contains the FastAPI application and backend implementation.
- `requirements.txt` — Lists Python dependencies.
- `README.md` — Project documentation and setup instructions.
- `REVIEW.md` — Implementation review notes.
- `DECISIONS.md` — Technical decisions and their rationale.

## Known Limitations

- The application has been tested against a single MongoDB instance. Hosted and sharded MongoDB deployments have not been verified.
- Startup currently uses FastAPI's `@app.on_event` lifecycle API, which produces a deprecation warning in newer FastAPI versions.
- Performance measurements are based on the local test environment and should be revalidated under production-like workloads.

## Implementation Notes

This project was developed for the **HROne Software Engineer assignment (Contract v2.0)**.

The implementation focuses on:

- Correct attendance calculations and business-rule enforcement.
- Reliable handling of punch-ins, punch-outs, and overnight shifts.
- Concurrency-safe attendance operations and regularization.
- Append-only audit history for manual corrections.
- Aggregation-based employee and department analytics.
- Index-backed MongoDB query performance.
- Automated testing and API documentation.

## License

No license has been specified in this repository documentation. Add a `LICENSE` file if you intend to distribute or reuse the project publicly.
