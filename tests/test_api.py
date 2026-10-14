
import sys
from pathlib import Path

# Add the project root to Python's import path.
ROOT_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT_DIR))

from fastapi.testclient import TestClient
from app.main import app

client = TestClient(app)


def test_health():
    response = client.get("/health")
    assert response.status_code == 200
    assert response.json()["status"] == "ok"


def test_list_employees():
    response = client.get("/employees")
    assert response.status_code == 200

    data = response.json()
    assert "items" in data
    assert "total" in data


def test_unknown_employee_punch_in():
    response = client.post(
        "/attendance/punch-in",
        json={
            "emp_code": "UNKNOWN_TEST_EMPLOYEE_987654",
            "status": "PRESENT",
        },
    )
    assert response.status_code == 404


def test_invalid_month():
    response = client.get(
        "/analytics/employees/EMP9001/monthly",
        params={"month": "invalid-month"},
    )
    assert response.status_code in (404, 422)



from datetime import datetime, timedelta, timezone
from uuid import uuid4


IST = timezone(timedelta(hours=5, minutes=30))


def timestamp_ms(day: str, hour: int, minute: int) -> int:
    local_dt = datetime.strptime(
        f"{day} {hour:02d}:{minute:02d}",
        "%Y-%m-%d %H:%M",
    ).replace(tzinfo=IST)
    return int(local_dt.timestamp() * 1000)


def create_test_employee():
    emp_code = f"TEST{uuid4().hex[:12].upper()}"

    payload = {
        "emp_code": emp_code,
        "name": "Automated Test Employee",
        "email": f"{emp_code.lower()}@example.com",
        "department": "TestDepartment",
        "shift_start": "09:30",
        "shift_end": "18:30",
        "joined_on": "2026-01-01",
    }

    response = client.post("/employees", json=payload)
    assert response.status_code == 201, response.text
    return emp_code


def test_duplicate_employee_returns_409():
    emp_code = f"TEST{uuid4().hex[:12].upper()}"

    payload = {
        "emp_code": emp_code,
        "name": "Duplicate Test",
        "email": f"{emp_code.lower()}@example.com",
        "department": "TestDepartment",
        "shift_start": "09:30",
        "shift_end": "18:30",
        "joined_on": "2026-01-01",
    }

    first = client.post("/employees", json=payload)
    assert first.status_code == 201, first.text

    second = client.post("/employees", json=payload)
    assert second.status_code == 409, second.text


def test_duplicate_punch_in_returns_409():
    emp_code = create_test_employee()
    punch_time = timestamp_ms("2026-10-12", 9, 30)

    payload = {
        "emp_code": emp_code,
        "punched_at": punch_time,
        "status": "PRESENT",
    }

    first = client.post("/attendance/punch-in", json=payload)
    assert first.status_code == 201, first.text

    second = client.post("/attendance/punch-in", json=payload)
    assert second.status_code == 409, second.text


def test_punch_out_calculates_work_hours():
    emp_code = create_test_employee()

    punch_in_time = timestamp_ms("2026-10-12", 9, 30)
    punch_out_time = timestamp_ms("2026-10-12", 18, 45)

    punch_in_response = client.post(
        "/attendance/punch-in",
        json={
            "emp_code": emp_code,
            "punched_at": punch_in_time,
            "status": "PRESENT",
        },
    )
    assert punch_in_response.status_code == 201, punch_in_response.text

    punch_out_response = client.post(
        "/attendance/punch-out",
        json={
            "emp_code": emp_code,
            "punched_at": punch_out_time,
        },
    )
    assert punch_out_response.status_code == 200, punch_out_response.text

    result = punch_out_response.json()
    assert result["punch_out"] == punch_out_time
    assert result["work_hours"] == 9.25
    assert result["late_minutes"] == 0
    assert result["overtime_minutes"] == 0
    assert result["half_day"] is False



def test_regularization_updates_status_and_history():
    emp_code = create_test_employee()
    day = "2026-10-12"

    punch_in_time = timestamp_ms(day, 9, 30)
    punch_out_time = timestamp_ms(day, 18, 30)

    punch_in_response = client.post(
        "/attendance/punch-in",
        json={
            "emp_code": emp_code,
            "punched_at": punch_in_time,
            "status": "PRESENT",
        },
    )
    assert punch_in_response.status_code == 201, punch_in_response.text

    punch_out_response = client.post(
        "/attendance/punch-out",
        json={
            "emp_code": emp_code,
            "punched_at": punch_out_time,
        },
    )
    assert punch_out_response.status_code == 200, punch_out_response.text

    response = client.patch(
        f"/attendance/{emp_code}/{day}",
        json={"status": "WFH"},
    )

    assert response.status_code == 200, response.text

    data = response.json()
    assert data["status"] == "WFH"
    assert len(data["history"]) == 1
    assert data["history"][0]["changes"]["status"] == "WFH"


def test_regularization_rejects_punch_out_before_punch_in():
    emp_code = create_test_employee()
    day = "2026-10-12"

    response = client.post(
        "/attendance/punch-in",
        json={
            "emp_code": emp_code,
            "punched_at": timestamp_ms(day, 9, 30),
            "status": "PRESENT",
        },
    )
    assert response.status_code == 201, response.text

    response = client.patch(
        f"/attendance/{emp_code}/{day}",
        json={
            "punch_out": timestamp_ms(day, 9, 0),
        },
    )

    assert response.status_code == 422, response.text


def test_regularization_rejects_empty_body():
    emp_code = create_test_employee()
    day = "2026-10-12"

    response = client.post(
        "/attendance/punch-in",
        json={
            "emp_code": emp_code,
            "punched_at": timestamp_ms(day, 9, 30),
            "status": "PRESENT",
        },
    )
    assert response.status_code == 201, response.text

    response = client.patch(
        f"/attendance/{emp_code}/{day}",
        json={},
    )

    assert response.status_code == 422, response.text




def test_late_threshold_exactly_10_minutes():
    emp_code = create_test_employee()
    day = "2026-10-13"

    response = client.post(
        "/attendance/punch-in",
        json={
            "emp_code": emp_code,
            "punched_at": timestamp_ms(day, 9, 40),
            "status": "PRESENT",
        },
    )

    assert response.status_code == 201, response.text
    assert response.json()["late_minutes"] == 0


def test_late_threshold_11_minutes():
    emp_code = create_test_employee()
    day = "2026-10-13"

    response = client.post(
        "/attendance/punch-in",
        json={
            "emp_code": emp_code,
            "punched_at": timestamp_ms(day, 9, 41),
            "status": "PRESENT",
        },
    )

    assert response.status_code == 201, response.text
    assert response.json()["late_minutes"] == 11


def test_overtime_29_minutes_is_zero():
    emp_code = create_test_employee()
    day = "2026-10-13"

    punch_in = client.post(
        "/attendance/punch-in",
        json={
            "emp_code": emp_code,
            "punched_at": timestamp_ms(day, 9, 30),
            "status": "PRESENT",
        },
    )
    assert punch_in.status_code == 201, punch_in.text

    punch_out_time = timestamp_ms(day, 18, 59)
    punch_out = client.post(
        "/attendance/punch-out",
        json={
            "emp_code": emp_code,
            "punched_at": punch_out_time,
        },
    )

    assert punch_out.status_code == 200, punch_out.text
    assert punch_out.json()["overtime_minutes"] == 0


def test_overtime_30_minutes_is_recorded():
    emp_code = create_test_employee()
    day = "2026-10-13"

    punch_in = client.post(
        "/attendance/punch-in",
        json={
            "emp_code": emp_code,
            "punched_at": timestamp_ms(day, 9, 30),
            "status": "PRESENT",
        },
    )
    assert punch_in.status_code == 201, punch_in.text

    punch_out_time = timestamp_ms(day, 19, 0)
    punch_out = client.post(
        "/attendance/punch-out",
        json={
            "emp_code": emp_code,
            "punched_at": punch_out_time,
        },
    )

    assert punch_out.status_code == 200, punch_out.text
    assert punch_out.json()["overtime_minutes"] == 30




def test_half_day_below_4_50_hours():
    emp_code = create_test_employee()
    day = "2026-10-14"

    punch_in = client.post(
        "/attendance/punch-in",
        json={
            "emp_code": emp_code,
            "punched_at": timestamp_ms(day, 9, 30),
            "status": "PRESENT",
        },
    )
    assert punch_in.status_code == 201, punch_in.text

    punch_out = client.post(
        "/attendance/punch-out",
        json={
            "emp_code": emp_code,
            "punched_at": timestamp_ms(day, 13, 59),
        },
    )
    assert punch_out.status_code == 200, punch_out.text

    data = punch_out.json()
    assert data["work_hours"] == 4.48
    assert data["half_day"] is True


def test_exactly_4_50_hours_is_not_half_day():
    emp_code = create_test_employee()
    day = "2026-10-14"

    punch_in = client.post(
        "/attendance/punch-in",
        json={
            "emp_code": emp_code,
            "punched_at": timestamp_ms(day, 9, 30),
            "status": "PRESENT",
        },
    )
    assert punch_in.status_code == 201, punch_in.text

    punch_out = client.post(
        "/attendance/punch-out",
        json={
            "emp_code": emp_code,
            "punched_at": timestamp_ms(day, 14, 0),
        },
    )
    assert punch_out.status_code == 200, punch_out.text

    data = punch_out.json()
    assert data["work_hours"] == 4.50
    assert data["half_day"] is False



def test_monthly_analytics_returns_expected_fields():
    emp_code = create_test_employee()
    day = "2026-10-15"

    punch_in = client.post(
        "/attendance/punch-in",
        json={
            "emp_code": emp_code,
            "punched_at": timestamp_ms(day, 9, 30),
            "status": "PRESENT",
        },
    )
    assert punch_in.status_code == 201, punch_in.text

    response = client.get(
        f"/analytics/employees/{emp_code}/monthly",
        params={"month": "2026-10"},
    )

    assert response.status_code == 200, response.text
    data = response.json()

    assert data["emp_code"] == emp_code
    assert data["month"] == "2026-10"
    assert data["attendance_records"] == 1
    assert data["present_days"] == 1

    required_fields = {
        "working_days",
        "absent_days",
        "late_days",
        "late_minutes",
        "overtime_minutes",
        "work_hours",
        "half_days",
    }
    assert required_fields.issubset(data.keys())
