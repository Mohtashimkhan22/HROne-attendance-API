"""HROne Employee Attendance & Analytics API, contract v2.0."""
import json
import os
import re
import calendar
from datetime import datetime, date, timedelta, timezone
from decimal import Decimal, ROUND_HALF_UP
from typing import Optional, Literal
from bson import json_util
from dotenv import load_dotenv
from fastapi import FastAPI, HTTPException, Query
from pydantic import BaseModel, ConfigDict, Field, StrictInt, field_validator, model_validator
from pymongo import MongoClient, ASCENDING, DESCENDING
from pymongo.errors import DuplicateKeyError, PyMongoError
load_dotenv()
MONGO_URI = os.getenv("MONGO_URI")
if not MONGO_URI:
    raise RuntimeError("MONGO_URI must be configured in the environment")
MONGO_DB = os.getenv("MONGO_DB", "attendance_db")
# print(f"Connecting to MongoDB at {MONGO_URI}, database: {MONGO_DB}")
client = MongoClient(MONGO_URI)
db = client[MONGO_DB]
IST = timezone(timedelta(hours=5, minutes=30))
UTC = timezone.utc
PRESENT_STATUSES = ("PRESENT", "WFH", "ON_DUTY")
ALL_STATUSES = PRESENT_STATUSES + ("ABSENT", "LEAVE")
app = FastAPI(title="Employee Attendance & Analytics API", version="2.0.0")


@app.on_event("startup")
def create_indexes():
    """Indexes are created by MongoDB and enforce natural-key uniqueness."""
    db.employees.create_index([("emp_code", ASCENDING)], unique=True, name="uniq_employee_code")
    db.employees.create_index([("department", ASCENDING), ("emp_code", ASCENDING)], name="employee_department_code")
    db.employees.create_index([("department", ASCENDING), ("joined_on", ASCENDING)], name="employee_department_joined")
    db.employees.create_index([("joined_on", ASCENDING), ("department", ASCENDING)], name="employee_joined_department")
    db.attendance_logs.create_index([("emp_code", ASCENDING), ("date", ASCENDING)], unique=True, name="uniq_attendance_employee_date")
    db.attendance_logs.create_index([("emp_code", ASCENDING), ("punch_in", DESCENDING)], name="attendance_employee_punch_in")
    db.attendance_logs.create_index([("date", DESCENDING), ("emp_code", ASCENDING)], name="attendance_date_employee")
    db.attendance_logs.create_index([("status", ASCENDING), ("date", DESCENDING), ("emp_code", ASCENDING)], name="attendance_status_date_employee")
    db.attendance_logs.create_index([("date", ASCENDING), ("late_minutes", DESCENDING), ("emp_code", ASCENDING)], name="attendance_late_date_employee")


class EmployeeIn(BaseModel):
    model_config = ConfigDict(extra="ignore")
    emp_code: str = Field(pattern=r"^EMP\d{4,6}$")
    name: str = Field(min_length=1, max_length=100)
    email: str = Field(
        min_length=3,
        max_length=120,
        pattern=r"^[^@\s]+@[^@\s]+\.[^@\s]+$",
    )
    department: str = Field(min_length=1, max_length=50)
    shift_start: str = "09:30"
    shift_end: str = "18:30"
    joined_on: str
    @field_validator("shift_start", "shift_end")
    @classmethod
    def validate_shift_time(cls, value: str) -> str:
        if not re.fullmatch(r"(?:[01]\d|2[0-3]):[0-5]\d", value):
            raise ValueError("time must be HH:MM in 24-hour format")
        return value
    @field_validator("joined_on")
    @classmethod
    def validate_joined_on(cls, value: str) -> str:
        try:
            parsed = date.fromisoformat(value)
        except (TypeError, ValueError):
            raise ValueError("joined_on must be YYYY-MM-DD")
        if parsed.isoformat() != value:
            raise ValueError("joined_on must be YYYY-MM-DD")
        return value
    @model_validator(mode="after")
    def validate_shift_range(self):
        if self.shift_start == self.shift_end:
            raise ValueError("shift_start and shift_end must differ")
        return self


class PunchInIn(BaseModel):
    model_config = ConfigDict(extra="ignore")
    emp_code: str = Field(min_length=1)
    punched_at: StrictInt | None = None
    status: Literal["PRESENT", "WFH", "ON_DUTY"] = "PRESENT"


class PunchOutIn(BaseModel):
    model_config = ConfigDict(extra="ignore")
    emp_code: str = Field(min_length=1)
    punched_at: StrictInt | None = None


class RegularizeIn(BaseModel):
    model_config = ConfigDict(extra="ignore")
    status: Literal["PRESENT", "WFH", "ON_DUTY", "ABSENT", "LEAVE"] | None = None
    punch_in: StrictInt | None = None
    punch_out: StrictInt | None = None
    reason: str = Field(min_length=5, max_length=200)
    regularized_by: str = Field(min_length=1, max_length=50)
    @model_validator(mode="before")
    @classmethod
    def reject_explicit_nulls(cls, data):
        if isinstance(data, dict):
            for field_name in ("status", "punch_in", "punch_out"):
                if field_name in data and data[field_name] is None:
                    raise ValueError(f"{field_name} cannot be null")
        return data


def error422(message: str):
    raise HTTPException(status_code=422, detail=message)


def parse_day(value: str) -> date:
    try:
        parsed = date.fromisoformat(value)
        if parsed.isoformat() != value:
            raise ValueError
        return parsed
    except (TypeError, ValueError):
        error422("Date must be YYYY-MM-DD")


def parse_month(value: str) -> date:
    try:
        if len(value) != 7 or value[4] != "-":
            raise ValueError
        year, month = map(int, value.split("-"))
        return date(year, month, 1)
    except (TypeError, ValueError):
        error422("month must be YYYY-MM")


def millis_dt(ms: int) -> datetime:
    if type(ms) is not int or ms < 100_000_000_000 or ms > 4_102_444_800_000:
        error422("timestamp must be an in-range epoch-millisecond integer")
    try:
        return datetime.fromtimestamp(ms // 1000, UTC)
    except (ValueError, OverflowError, OSError):
        error422("Invalid timestamp")


def input_dt(ms: Optional[int]) -> datetime:
    return (datetime.now(UTC) if ms is None else millis_dt(ms)).replace(microsecond=0)


def utc_aware(value: Optional[datetime]) -> Optional[datetime]:
    if value is None:
        return None
    return value.replace(tzinfo=UTC) if value.tzinfo is None else value.astimezone(UTC)


def to_ms(value: Optional[datetime]) -> Optional[int]:
    value = utc_aware(value)
    return None if value is None else (value - datetime(1970, 1, 1, tzinfo=UTC)) // timedelta(milliseconds=1)


def round_half_up(value, places=2):
    quantum = Decimal("1").scaleb(-places)
    return float(Decimal(str(value)).quantize(quantum, rounding=ROUND_HALF_UP))


def shift_datetime(day_value: str, hhmm: str) -> datetime:
    d = date.fromisoformat(day_value)
    h, m = map(int, hhmm.split(":"))
    return datetime(d.year, d.month, d.day, h, m, tzinfo=IST)


def attendance_day(punch_in: datetime, employee: dict) -> str:
    local = punch_in.astimezone(IST)
    shift_end_h, shift_end_m = map(int, employee["shift_end"].split(":"))
    end_minutes = shift_end_h * 60 + shift_end_m
    local_minutes = local.hour * 60 + local.minute
    start_h, start_m = map(int, employee["shift_start"].split(":"))
    start_minutes = start_h * 60 + start_m
    # For overnight shifts, after midnight and before shift_end belongs to prior date.
    if end_minutes <= start_minutes and local_minutes < end_minutes:
        local = local - timedelta(days=1)
    return local.date().isoformat()


def late_minutes(
    punch_in: datetime,
    shift_start: str,
    day_value: str,
) -> int:
    punch_in = utc_aware(punch_in)
    start = utc_aware(shift_datetime(day_value, shift_start))
    seconds = (punch_in - start).total_seconds()
    return int(seconds // 60) if seconds > 10 * 60 else 0


def shift_end_datetime(day_value: str, employee: dict) -> datetime:
    start_h, start_m = map(int, employee["shift_start"].split(":"))
    end_h, end_m = map(int, employee["shift_end"].split(":"))
    end_day = date.fromisoformat(day_value)
    if end_h * 60 + end_m <= start_h * 60 + start_m:
        end_day += timedelta(days=1)
    return datetime(end_day.year, end_day.month, end_day.day, end_h, end_m, tzinfo=IST)


def calculate_fields(
    punch_in: Optional[datetime],
    punch_out: Optional[datetime],
    employee: dict,
    day_value: str,
):
    # MongoDB may return naive datetimes; normalize both to UTC.
    punch_in = utc_aware(punch_in)
    punch_out = utc_aware(punch_out)
    late = (
        late_minutes(punch_in, employee["shift_start"], day_value)
        if punch_in is not None
        else 0
    )
    overtime = 0
    hours = None
    half_day = False
    if punch_in is not None and punch_out is not None:
        # Prevent invalid negative durations.
        if punch_out < punch_in:
            raise HTTPException(
                status_code=422,
                detail="punch-out cannot be before punch-in",
            )
        seconds = (punch_out - punch_in).total_seconds()
        hours = round_half_up(seconds / 3600, 2)
        half_day = hours < 4.50
        end_utc = utc_aware(
            shift_end_datetime(day_value, employee)
        )
        raw_overtime = max(
            0,
            int((punch_out - end_utc).total_seconds() // 60),
        )
        overtime = raw_overtime if raw_overtime >= 30 else 0
    return late, overtime, hours, half_day


def _ms(value):
    return to_ms(value) if isinstance(value, datetime) else value


def api_record(document: Optional[dict]):
    """Contract shape of an attendance record: epoch-ms instants, defaults for legacy docs, no internal fields."""
    if not document:
        return document
    result = {
        "emp_code": document["emp_code"],
        "date": document["date"],
        "status": document["status"],
        "punch_in": to_ms(document.get("punch_in")),
        "punch_out": to_ms(document.get("punch_out")),
        "work_hours": document.get("work_hours"),
        "late_minutes": document.get("late_minutes", 0),
        "overtime_minutes": document.get("overtime_minutes", 0),
        "half_day": document.get("half_day", False),
        "history": [],
    }
    for item in document.get("history") or []:
        changes = {
            key: {"from": _ms(change.get("from")), "to": _ms(change.get("to"))}
            for key, change in (item.get("changes") or {}).items()
        }
        result["history"].append({"at": _ms(item.get("at")), "by": item.get("by"), "reason": item.get("reason"), "changes": changes})
    return result


def clean_explain(value):
    """Explain output holds BSON types (Timestamp, ObjectId...); make it plain JSON."""
    return json.loads(json_util.dumps(value, json_options=json_util.RELAXED_JSON_OPTIONS))


def require_employee(emp_code: str) -> dict:
    employee = db.employees.find_one({"emp_code": emp_code}, {"_id": 0})
    if employee is None:
        raise HTTPException(status_code=404, detail=f"Unknown employee: {emp_code}")
    return employee








@app.get("/health")
def health():
    try:
        client.admin.command("ping")
        return {"status": "ok", "database": "connected"}
    except PyMongoError:
        raise HTTPException(status_code=503, detail="MongoDB is unavailable")


@app.post("/employees", status_code=201)
def create_employee(body: EmployeeIn):
    doc = body.model_dump()
    doc["created_at"] = datetime.now(UTC).replace(microsecond=0)
    try:
        db.employees.insert_one(doc)
    except DuplicateKeyError:
        raise HTTPException(status_code=409, detail="emp_code already exists")
    except PyMongoError as exc:
        raise HTTPException(status_code=503, detail="Unable to create employee") from exc
    result = {k: v for k, v in doc.items() if k != "_id"}
    result["created_at"] = to_ms(result["created_at"])
    return result


@app.get("/employees")
def list_employees(
    department: Optional[str] = None,
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
):
    query = {"department": department} if department is not None else {}
    try:
        total = db.employees.count_documents(query)
        items = list(db.employees.find(query, {"_id": 0}).sort("emp_code", ASCENDING).skip((page - 1) * page_size).limit(page_size))
        for item in items:
            if "created_at" in item:
                item["created_at"] = to_ms(item["created_at"])
        return {"items": items, "total": total, "page": page, "page_size": page_size}
    except PyMongoError as exc:
        raise HTTPException(status_code=503, detail="Unable to query employees") from exc


@app.post("/attendance/punch-in", status_code=201)
def punch_in(body: PunchInIn):
    employee = require_employee(body.emp_code)
    punched_at = input_dt(body.punched_at)
    day_value = attendance_day(punched_at, employee)
    late, _, _, _ = calculate_fields(punched_at, None, employee, day_value)
    document = {
        "emp_code": body.emp_code, "date": day_value, "status": body.status,
        "punch_in": punched_at, "punch_out": None, "work_hours": None,
        "late_minutes": late, "overtime_minutes": 0, "half_day": False,
        "history": [], "created_at": datetime.now(UTC).replace(microsecond=0),
    }
    try:
        db.attendance_logs.insert_one(document)
    except DuplicateKeyError:
        raise HTTPException(status_code=409, detail="Already punched in for this attendance date")
    except PyMongoError as exc:
        raise HTTPException(status_code=503, detail="Unable to record punch-in") from exc
    return api_record(document)


@app.post("/attendance/punch-out")
def punch_out(body: PunchOutIn):
    employee = require_employee(body.emp_code)
    punched_at = input_dt(body.punched_at)
    # Most recent record whose punch_in is at or before punched_at (works across midnight).
    record = db.attendance_logs.find_one(
        {"emp_code": body.emp_code, "punch_in": {"$lte": punched_at}},
        sort=[("punch_in", DESCENDING)],
    )
    if record is None:
        raise HTTPException(status_code=404, detail="No punch-in found")
    if record.get("punch_out") is not None:
        raise HTTPException(status_code=409, detail="Attendance record was already punched out")
    elapsed = (punched_at - utc_aware(record["punch_in"])).total_seconds()
    if elapsed <= 0 or elapsed > 24 * 3600:
        error422("punch-out must be after punch-in and within 24 hours of it")
    late, overtime, hours, half_day = calculate_fields(record["punch_in"], punched_at, employee, record["date"])
    update = {"punch_out": punched_at, "work_hours": hours, "late_minutes": late, "overtime_minutes": overtime,
              "half_day": half_day, "updated_at": datetime.now(UTC).replace(microsecond=0)}
    # Atomic guard: only the first of several simultaneous punch-outs matches punch_out == null.
    result = db.attendance_logs.update_one({"_id": record["_id"], "punch_out": None}, {"$set": update})
    if result.modified_count != 1:
        raise HTTPException(status_code=409, detail="Attendance record was already punched out")
    record.update(update)
    return api_record(record)


@app.get("/attendance")
def list_attendance(
    emp_code: Optional[str] = None,
    date_from: Optional[str] = None,
    date_to: Optional[str] = None,
    status: Optional[str] = None,
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
):
    query = {}
    if emp_code is not None:
        query["emp_code"] = emp_code
    if date_from or date_to:
        date_query = {}
        if date_from:
            date_query["$gte"] = parse_day(date_from).isoformat()
        if date_to:
            date_query["$lte"] = parse_day(date_to).isoformat()
        if date_from and date_to and date_from > date_to:
            error422("date_from must be on or before date_to")
        query["date"] = date_query
    if status:
        if status not in ALL_STATUSES:
            error422("Invalid status")
        query["status"] = status
    try:
        total = db.attendance_logs.count_documents(query)
        docs = list(db.attendance_logs.find(query, {"_id": 0}).sort([("date", DESCENDING), ("emp_code", ASCENDING)]).skip((page - 1) * page_size).limit(page_size))
        return {"items": [api_record(doc) for doc in docs], "total": total, "page": page, "page_size": page_size}
    except PyMongoError as exc:
        raise HTTPException(status_code=503, detail="Unable to query attendance") from exc


@app.patch("/attendance/{emp_code}/{day_value}")
def regularize_attendance(emp_code: str, day_value: str, body: RegularizeIn):
    parse_day(day_value)
    employee = require_employee(emp_code)
    record = db.attendance_logs.find_one({"emp_code": emp_code, "date": day_value})
    if record is None:
        raise HTTPException(status_code=404, detail="Attendance record not found")
    supplied = body.model_dump(exclude_unset=True)
    reason = supplied.pop("reason")
    regularized_by = supplied.pop("regularized_by")
    if not any(k in supplied for k in ("status", "punch_in", "punch_out")):
        error422("Provide at least one of status, punch_in, punch_out")

    old_status = record.get("status", "PRESENT")
    old_in = utc_aware(record.get("punch_in"))
    old_out = utc_aware(record.get("punch_out"))
    new_status = supplied.get("status", old_status)
    new_in = millis_dt(supplied["punch_in"]) if "punch_in" in supplied else old_in
    new_out = millis_dt(supplied["punch_out"]) if "punch_out" in supplied else old_out

    if new_status in ("ABSENT", "LEAVE"):
        if "punch_in" in supplied or "punch_out" in supplied:
            error422("ABSENT/LEAVE cannot be combined with punch times")
        new_in = new_out = None
    elif new_in is None:
        error422("A present status requires punch_in")
    if new_in is not None and attendance_day(new_in, employee) != day_value:
        error422("punch_in must fall on the record's attendance date")
    if new_in is not None and new_out is not None:
        duration = (new_out - new_in).total_seconds()
        if duration <= 0 or duration > 24 * 3600:
            error422("punch_out must be after punch_in and within 24 hours")

    late, overtime, hours, half_day = calculate_fields(new_in, new_out, employee, day_value)
    new_state = {"status": new_status, "punch_in": new_in, "punch_out": new_out, "work_hours": hours,
                 "late_minutes": late, "overtime_minutes": overtime, "half_day": half_day}
    old_state = {"status": old_status, "punch_in": old_in, "punch_out": old_out, "work_hours": record.get("work_hours"),
                 "late_minutes": record.get("late_minutes", 0), "overtime_minutes": record.get("overtime_minutes", 0),
                 "half_day": record.get("half_day", False)}
    changed = {k: {"from": old_state[k], "to": new_state[k]} for k in new_state if old_state[k] != new_state[k]}
    if not any(k in changed for k in ("status", "punch_in", "punch_out")):
        error422("Regularization would not change the record")

    now = datetime.now(UTC).replace(microsecond=0)
    entry = {"at": now, "by": regularized_by, "reason": reason, "changes": changed}  # instants stay BSON dates
    history = record.get("history")
    # Optimistic lock on history length (works for legacy records without a history field).
    if history is None:
        lock = {"history": None}
        ops = {"$set": {**new_state, "history": [entry], "updated_at": now}}
    else:
        lock = {"history": {"$size": len(history)}}
        ops = {"$set": {**new_state, "updated_at": now}, "$push": {"history": entry}}
    result = db.attendance_logs.update_one({"_id": record["_id"], **lock}, ops)
    if result.modified_count != 1:
        raise HTTPException(status_code=409, detail="Attendance record changed concurrently; retry")
    record.update(new_state)
    record["history"] = list(history or []) + [entry]
    return api_record(record)


def month_bounds(month: str):
    first = parse_month(month)
    last = date(first.year, first.month, calendar.monthrange(first.year, first.month)[1])
    return first.isoformat(), last.isoformat()


def employee_monthly_pipeline(emp_code: str, month: str):
    start, end = month_bounds(month)
    return [
        {"$match": {"emp_code": emp_code, "date": {"$gte": start, "$lte": end}}},
        {"$set": {"_weekday": {"$dayOfWeek": {"$dateFromString": {"dateString": "$date"}}}}},
        {"$group": {
            "_id": None,
            "present_days": {"$sum": {"$cond": [
                {"$and": [{"$in": ["$status", list(PRESENT_STATUSES)]}, {"$in": ["$_weekday", [2, 3, 4, 5, 6]]}]},
                {"$cond": [{"$ifNull": ["$half_day", False]}, 0.5, 1]}, 0]}},
            "leave_days": {"$sum": {"$cond": [{"$eq": ["$status", "LEAVE"]}, 1, 0]}},
            "late_count": {"$sum": {"$cond": [{"$gt": [{"$ifNull": ["$late_minutes", 0]}, 0]}, 1, 0]}},
            "total_late_minutes": {"$sum": {"$ifNull": ["$late_minutes", 0]}},
            "total_overtime_minutes": {"$sum": {"$ifNull": ["$overtime_minutes", 0]}},
            "attendance_records": {"$sum": 1},
        }},
    ]


def department_summary_pipeline(month: str, department: Optional[str] = None):
    _, end = month_bounds(month)
    lookup_pipeline = [
        {"$match": {"$expr": {"$and": [
            {"$eq": ["$emp_code", "$$code"]},
            {"$gte": ["$date", "$$start"]},
            {"$lte": ["$date", "$$end"]},
        ]}}},
    ]
    return [
        {"$match": {"joined_on": {"$lte": end}, **({"department": department} if department else {})}},
        {"$lookup": {"from": "attendance_logs", "let": {"code": "$emp_code", "start": month_bounds(month)[0], "end": end}, "pipeline": lookup_pipeline, "as": "logs"}},
        {"$unwind": {"path": "$logs", "preserveNullAndEmptyArrays": True}},
        {"$group": {
            "_id": "$department",
            "headcount_codes": {"$addToSet": "$emp_code"},
            "present_days": {"$sum": {"$cond": [
                {"$and": [{"$in": ["$logs.status", list(PRESENT_STATUSES)]},
                          {"$in": [{"$dayOfWeek": {"$dateFromString": {"dateString": {"$ifNull": ["$logs.date", "2000-01-01"]}}}}, [2, 3, 4, 5, 6]]}]},
                {"$cond": [{"$ifNull": ["$logs.half_day", False]}, 0.5, 1]}, 0]}},
            "hours_sum": {"$sum": {"$cond": [{"$and": [{"$in": ["$logs.status", list(PRESENT_STATUSES)]}, {"$ne": ["$logs.work_hours", None]}]}, "$logs.work_hours", 0]}},
            "hours_count": {"$sum": {"$cond": [{"$and": [{"$in": ["$logs.status", list(PRESENT_STATUSES)]}, {"$ne": ["$logs.work_hours", None]}]}, 1, 0]}},
            "late_count": {"$sum": {"$cond": [{"$gt": [{"$ifNull": ["$logs.late_minutes", 0]}, 0]}, 1, 0]}},
            "total_late_minutes": {"$sum": {"$ifNull": ["$logs.late_minutes", 0]}},
            "leave_count": {"$sum": {"$cond": [{"$eq": ["$logs.status", "LEAVE"]}, 1, 0]}},
            "on_duty_count": {"$sum": {"$cond": [{"$eq": ["$logs.status", "ON_DUTY"]}, 1, 0]}},
        }},
        {"$set": {"headcount": {"$size": "$headcount_codes"}, "avg_work_hours": {"$cond": [{"$gt": ["$hours_count", 0]}, {"$divide": ["$hours_sum", "$hours_count"]}, None]}}},
        {"$match": {"headcount": {"$gt": 0}}},
        {"$sort": {"_id": 1}},
        {"$project": {"_id": 0, "department": "$_id", "headcount": 1, "present_days": 1, "avg_work_hours": 1, "late_count": 1, "total_late_minutes": 1, "leave_count": 1, "on_duty_count": 1}},
    ]


def leaderboard_pipeline(month: str, limit: int, department: Optional[str] = None):
    start, end = month_bounds(month)
    pipeline = [
        {"$match": {"date": {"$gte": start, "$lte": end}, "late_minutes": {"$gt": 0}}},
        {"$group": {"_id": "$emp_code", "total_late_minutes": {"$sum": "$late_minutes"}, "late_count": {"$sum": 1}}},
        {"$lookup": {"from": "employees", "localField": "_id", "foreignField": "emp_code", "as": "employee"}},
        {"$unwind": "$employee"},  # logs of unknown employees are dropped
    ]
    if department:
        pipeline.append({"$match": {"employee.department": department}})
    pipeline += [
        {"$set": {"name": "$employee.name", "department": "$employee.department"}},
        {"$setWindowFields": {"sortBy": {"total_late_minutes": -1}, "output": {"rank": {"$rank": {}}}}},
        {"$match": {"rank": {"$lte": limit}}},
        {"$sort": {"total_late_minutes": -1, "_id": 1}},
        {"$project": {"_id": 0, "emp_code": "$_id", "name": 1, "department": 1, "total_late_minutes": 1, "late_count": 1, "rank": 1}},
    ]
    return pipeline


def department_trend_pipeline(department: str, from_day: str, to_day: str):
    """Daily trend, computed in one pass.

    1. Per employee of the department, fetch that employee's logs in the range through the (emp_code, date)
       index, then group by date -> present_count / late_count (only days that have records).
    2. $unionWith a skeleton with one row per calendar day and that day's headcount (R9), so empty days and
       weekends still appear.  3. Merge both by date, then compute rate and moving average.
    """
    start_expr = {"$dateFromString": {"dateString": from_day}}
    end_expr = {"$dateFromString": {"dateString": to_day}}
    offsets = {"$range": [0, {"$add": [{"$dateDiff": {"startDate": start_expr, "endDate": end_expr, "unit": "day"}}, 1]}]}
    day_string = {"$dateToString": {"date": {"$dateAdd": {"startDate": start_expr, "unit": "day", "amount": "$$offset"}}, "format": "%Y-%m-%d"}}
    skeleton = [
        {"$match": {"department": department}},
        {"$group": {"_id": None, "joins": {"$push": "$joined_on"}}},
        {"$project": {"_id": 0, "rows": {"$map": {"input": offsets, "as": "offset", "in": {
            "_id": day_string,
            "headcount": {"$size": {"$filter": {"input": "$joins", "as": "j", "cond": {"$lte": ["$$j", day_string]}}}},
            "present_count": {"$literal": 0},
            "late_count": {"$literal": 0},
        }}}}},
        {"$unwind": "$rows"},
        {"$replaceRoot": {"newRoot": "$rows"}},
    ]
    return [
        {"$match": {"department": department}},
        {"$lookup": {"from": "attendance_logs", "localField": "emp_code", "foreignField": "emp_code", "pipeline": [
            {"$match": {"date": {"$gte": from_day, "$lte": to_day}}},
            {"$project": {"_id": 0, "date": 1, "status": 1, "half_day": 1, "late_minutes": 1}},
        ], "as": "logs"}},
        {"$unwind": "$logs"},
        {"$group": {"_id": "$logs.date",
            "headcount": {"$sum": 0},
            "present_count": {"$sum": {"$cond": [{"$in": ["$logs.status", list(PRESENT_STATUSES)]}, {"$cond": [{"$ifNull": ["$logs.half_day", False]}, 0.5, 1]}, 0]}},
            "late_count": {"$sum": {"$cond": [{"$gt": [{"$ifNull": ["$logs.late_minutes", 0]}, 0]}, 1, 0]}},
        }},
        {"$unionWith": {"coll": "employees", "pipeline": skeleton}},
        {"$group": {"_id": "$_id", "headcount": {"$max": "$headcount"}, "present_count": {"$sum": "$present_count"}, "late_count": {"$sum": "$late_count"}}},
        {"$set": {
            "_date": {"$dateFromString": {"dateString": "$_id"}},
            "is_working_day": {"$not": [{"$in": [{"$dayOfWeek": {"$dateFromString": {"dateString": "$_id"}}}, [1, 7]]}]},
        }},
        # Exact integer half-up to 4 dp: MongoDB $round is half-even, which breaks R8 (e.g. 1/32).
        {"$set": {"attendance_rate": {"$cond": [{"$and": ["$is_working_day", {"$gt": ["$headcount", 0]}]}, {"$divide": [{"$floor": {"$divide": [{"$add": [{"$multiply": ["$present_count", 40000]}, {"$multiply": ["$headcount", 2]}]}, {"$multiply": ["$headcount", 4]}]}}, 10000]}, None]}}},
        {"$setWindowFields": {"sortBy": {"_date": 1}, "output": {"moving_avg_7d": {"$avg": "$attendance_rate", "window": {"documents": [-6, 0]}}}}},
        {"$sort": {"_date": 1}},
        {"$project": {"_id": 0, "date": "$_id", "is_working_day": 1, "headcount": 1, "present_count": 1, "late_count": 1, "attendance_rate": 1, "moving_avg_7d": 1}},
    ]


def explain_aggregate(collection, pipeline):
    return db.command({"explain": {"aggregate": collection.name, "pipeline": pipeline, "cursor": {}}, "verbosity": "executionStats"})


@app.get("/analytics/employees/{emp_code}/monthly")
def employee_monthly(emp_code: str, month: str):
    employee = require_employee(emp_code)
    month_start = parse_month(month)
    start_day = max(month_start, date.fromisoformat(employee["joined_on"]))
    _, month_end = month_bounds(month)
    working_days = sum(1 for offset in range(max(0, (date.fromisoformat(month_end) - start_day).days + 1)) if (start_day + timedelta(days=offset)).weekday() < 5)
    rows = list(db.attendance_logs.aggregate(employee_monthly_pipeline(emp_code, month)))
    stats = rows[0] if rows else {}
    present = stats.get("present_days", 0)
    pct = None if working_days == 0 else float((Decimal(str(present)) * 100 / Decimal(working_days)).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP))
    return {
        "emp_code": emp_code,
        "month": month,
        "working_days": working_days,
        "present_days": round_half_up(present, 2),
        "leave_days": stats.get("leave_days", 0),
        "late_count": stats.get("late_count", 0),
        "total_late_minutes": stats.get("total_late_minutes", 0),
        "total_overtime_minutes": stats.get("total_overtime_minutes", 0),
        "attendance_pct": pct,
    }


@app.get("/analytics/departments/summary")
def department_summary(month: str, department: Optional[str] = None):
    parse_month(month)
    items = list(db.employees.aggregate(department_summary_pipeline(month, department)))
    for item in items:
        item["present_days"] = round_half_up(item["present_days"], 2)
        if item.get("avg_work_hours") is not None:
            item["avg_work_hours"] = round_half_up(item["avg_work_hours"], 2)
    return {"month": month, "items": items}


@app.get("/analytics/leaderboard/late")
def late_leaderboard(month: str, limit: int = Query(10, ge=1, le=50), department: Optional[str] = None):
    parse_month(month)
    return {"month": month, "items": list(db.attendance_logs.aggregate(leaderboard_pipeline(month, limit, department)))}


@app.get("/analytics/departments/{department}/trend")
def department_trend(department: str, from_day: str = Query(alias="from"), to_day: str = Query(alias="to")):
    first, last = parse_day(from_day), parse_day(to_day)
    if last < first:
        error422("to must be on or after from")
    if (last - first).days + 1 > 92:
        error422("date range cannot exceed 92 days")
    if db.employees.count_documents({"department": department}) == 0:
        raise HTTPException(status_code=404, detail="Unknown department")
    pipeline = department_trend_pipeline(department, from_day, to_day)
    rows = list(db.employees.aggregate(pipeline))
    for row in rows:
        for key in ("attendance_rate", "moving_avg_7d"):
            if row.get(key) is not None:
                row[key] = round_half_up(row[key], 4)
    return {"department": department, "items": rows}


@app.get("/admin/explain/{endpoint}")
def explain_endpoint(
    endpoint: Literal["attendance_list", "employee_monthly", "department_summary", "late_leaderboard", "department_trend"],
    emp_code: Optional[str] = None, month: Optional[str] = None, department: Optional[str] = None,
    limit: int = Query(10, ge=1, le=50), date_from: Optional[str] = None, date_to: Optional[str] = None,
    status: Optional[str] = None, page: int = Query(1, ge=1), page_size: int = Query(20, ge=1, le=100),
    from_day: Optional[str] = Query(default=None, alias="from"), to_day: Optional[str] = Query(default=None, alias="to"),
):
    if endpoint == "attendance_list":
        query = {}
        if emp_code: query["emp_code"] = emp_code
        if date_from: query.setdefault("date", {})["$gte"] = parse_day(date_from).isoformat()
        if date_to: query.setdefault("date", {})["$lte"] = parse_day(date_to).isoformat()
        if date_from and date_to and date_from > date_to: error422("date_from must be on or before date_to")
        if status:
            if status not in ALL_STATUSES: error422("Invalid status")
            query["status"] = status
        explain = db.command({"explain": {"find": "attendance_logs", "filter": query, "sort": {"date": -1, "emp_code": 1}, "skip": (page-1)*page_size, "limit": page_size}, "verbosity": "executionStats"})
        return {"endpoint": endpoint, "collection": "attendance_logs", "explain": clean_explain(explain)}
    if endpoint == "employee_monthly":
        if not emp_code or not month: error422("emp_code and month are required")
        parse_month(month); require_employee(emp_code)
        explain = explain_aggregate(db.attendance_logs, employee_monthly_pipeline(emp_code, month))
        return {"endpoint": endpoint, "collection": "attendance_logs", "explain": clean_explain(explain)}
    if endpoint == "department_summary":
        if not month: error422("month is required")
        parse_month(month)
        explain = explain_aggregate(db.employees, department_summary_pipeline(month, department))
        return {"endpoint": endpoint, "collection": "employees", "explain": clean_explain(explain)}
    if endpoint == "late_leaderboard":
        if not month: error422("month is required")
        parse_month(month)
        explain = explain_aggregate(db.attendance_logs, leaderboard_pipeline(month, limit, department))
        return {"endpoint": endpoint, "collection": "attendance_logs", "explain": clean_explain(explain)}
    if endpoint == "department_trend":
        if not department or not from_day or not to_day: error422("department, from and to are required")
        parse_day(from_day); parse_day(to_day)
        if to_day < from_day: error422("to must be on or after from")
        if (date.fromisoformat(to_day)-date.fromisoformat(from_day)).days + 1 > 92: error422("date range cannot exceed 92 days")
        pipeline = department_trend_pipeline(department, from_day, to_day)
        explain = explain_aggregate(db.employees, pipeline)
        return {"endpoint": endpoint, "collection": "employees", "explain": clean_explain(explain)}