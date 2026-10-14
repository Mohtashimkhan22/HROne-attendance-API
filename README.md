# HROne Attendance & Analytics API

A backend attendance management and analytics API built with FastAPI, Python 3.11+, and MongoDB. It supports employee management, attendance punch-in and punch-out, manual attendance regularization with audit history, and department-level analytics.

All application code is contained in app/main.py.

## Features
- Employee management: Create employees and retrieve paginated, department-filtered employee lists.
- Attendance tracking: Record punch-ins and punch-outs, including overnight shifts.
- Business rules: Calculate late minutes, work hours, overtime, and half-day status.
- Attendance regularization: Correct attendance records while preserving an append-only audit history.
- Analytics: Generate monthly employee summaries, department summaries, late-comer rankings, and daily department trends with seven-day moving averages.
- Concurrency safety: Prevent duplicate punch-ins and punch-outs and preserve audit history during concurrent corrections.
- Database performance: Create indexes at startup and provide execution statistics through the explain endpoint.

## Tech Stack
Python 3.11+
FastAPI
MongoDB 6.0+
PyMongo or Motor, as configured in the implementation
Uvicorn
Pytest

# Prerequisites

Ensure Python 3.11+ and MongoDB 6.0+ are installed and running.

## Setup and Run
### 1. Clone the repository
git clone https://github.com/Mohtashimkhan22/HROne-attendance-API.git
cd HROne-attendance-API


Replace the placeholders with your Git repository URL and directory name.

### 2. Install dependencies
pip install -r requirements.txt


Alternatively, if the project is configured for uv:

uv sync

### 3. Configure environment variables

Set the MongoDB connection URI and database name.

Linux/macOS:

export MONGO_URI="mongodb://localhost:27017"
export MONGO_DB="attendance_db"


Windows PowerShell:

$env:MONGO_URI = "mongodb://localhost:27017"
$env:MONGO_DB = "attendance_db"


MONGO_URI is required. MONGO_DB defaults to attendance_db if not specified.

A local .env file is also supported. Actual environment variables take precedence over values loaded from .env.

### 4. Start the application
uvicorn app.main:app --port 8000


The application initializes the required database indexes at startup. It is designed to work with the existing seeded dataset without requiring the dataset to be recreated.

## API Documentation

Once the server is running:

Health check: GET http://localhost:8000/health
Interactive Swagger documentation: http://localhost:8000/docs
ReDoc documentation: http://localhost:8000/redoc

The health endpoint checks MongoDB connectivity and returns an appropriate HTTP status.

## API Overview
Method	Endpoint	Description
POST	/employees	Create an employee
GET	/employees	List employees with filtering and pagination
POST	/attendance/punch-in	Record employee punch-in
POST	/attendance/punch-out	Record employee punch-out
GET	/attendance	Query attendance records
PATCH	/attendance/{emp_code}/{date}	Regularize an attendance record
GET	/analytics/employees/{emp_code}/monthly	Retrieve an employee's monthly summary
GET	/analytics/departments/summary	Retrieve department-level statistics
GET	/analytics/leaderboard/late	Rank employees by late attendance
GET	/analytics/departments/{department}/trend	Retrieve daily department trends
GET	/admin/explain/{endpoint}	Inspect query execution statistics
GET	/health	Check application and database availability

For exact request schemas, query parameters, response fields, and status codes, refer to the interactive Swagger documentation at /docs.

## Business Rules and Data Handling
All API instants use integer epoch milliseconds; MongoDB stores timestamps as BSON UTC datetimes.
Calendar dates, month values, and shift times follow IST conventions.
Punch-in and punch-out calculations account for overnight shifts.
Late arrival, overtime, work hours, and half-day status follow the specified business rules.
Presence calculations distinguish between present statuses and ABSENT or LEAVE.
Attendance history records manual corrections, including the actor, timestamp, reason, and changed field values.
List endpoints support pagination, with page numbering starting at 1 and a maximum page size of 100.
Employee attendance is identified by emp_code and date; MongoDB _id values are not exposed through the API.

## MongoDB Indexing and Analytics

Required indexes are created idempotently during application startup.

Analytics are implemented using MongoDB aggregation pipelines rather than fetching attendance records for Python-side aggregation. The explain endpoint exposes executionStats for the supported query types:

attendance_list
employee_monthly
department_summary
late_leaderboard
department_trend

These plans can be used to verify index usage and identify expensive database operations.

## Testing

Run the test suite with:

pytest -q


### Local test result: 30 tests passed.

The tests write to the database configured through MONGO_DB. Use a dedicated, disposable test database to avoid modifying development or production data.

## Performance Validation

The implementation was tested locally against a seeded dataset containing approximately 500 employees and 100,000 attendance records.

Observed results:

Explain plans used IXSCAN without COLLSCAN for the tested query patterns.
The department trend query over a 92-day period took approximately 50 ms in the local test environment.

These figures are environment-specific observations, not guaranteed production performance benchmarks.

## Known Limitations
The application was tested against a single MongoDB instance. Hosted and sharded MongoDB deployments have not been verified.
Startup currently uses FastAPI's @app.on_event lifecycle API, which produces a deprecation warning but works in the tested environment.

## Project Structure
.
├── app/
│   └── main.py
├── requirements.txt
├── README.md
├── REVIEW.md
└── DECISIONS.md

## Implementation Notes

This project was developed for the HROne Software Engineer assignment (Contract v2.0). The implementation focuses on correctness of attendance business rules, concurrency safety, aggregation-based analytics, and index-backed query performance.