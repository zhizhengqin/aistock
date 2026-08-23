"""Progressive, redacted task-status API contract tests."""

import pytest

from app.models.llm_execution import LlmCallAttempt
from app.models.task_record import TaskRecord
from app.models.user import User


def _attempt(task_id: int, step_key: str, *, status: str, schema: str | None = None, result=None):
    return LlmCallAttempt(
        task_id=task_id,
        operation_type="task",
        step_key=step_key,
        provider_snapshot="deepseek",
        model_snapshot="deepseek-chat",
        runtime_fingerprint="fixture",
        status=status,
        result_schema_version=schema,
        result_json=result,
        error_message="provider internal traceback must not be exposed",
    )


def test_task_status_exposes_six_steps_without_unvalidated_or_provider_data(auth_client, test_db):
    _, Session = test_db
    db = Session()
    task = TaskRecord(
        task_type="stock_analysis",
        user_id=1,
        status="running",
        progress=58,
        error="provider internal traceback with secret raw body",
        input_snapshot={"stock_code": "600519"},
    )
    db.add(task)
    db.flush()
    db.add_all(
        [
            _attempt(
                task.id,
                "stock.technical.v1",
                status="success",
                schema="v1",
                result={"trend": "偏强"},
            ),
            _attempt(
                task.id,
                "stock.fundamental.v1",
                status="started",
                result={"secret": "raw provider output"},
            ),
            _attempt(task.id, "stock.capital.v1", status="failed"),
            _attempt(task.id, "stock.risk.v1", status="failed_unknown"),
            _attempt(task.id, "stock.sentiment.v1", status="success", result={"raw": True}),
        ]
    )
    db.commit()
    task_id = task.id
    db.close()

    response = auth_client.get(f"/api/tasks/{task_id}")

    assert response.status_code == 200
    data = response.json()["data"]
    assert data["phase"] == "analyzing"
    assert [step["key"] for step in data["steps"]] == [
        "technical", "fundamental", "capital", "news", "sentiment", "risk", "chief",
    ]
    by_key = {step["key"]: step for step in data["steps"]}
    assert by_key["technical"]["status"] == "completed"
    assert by_key["technical"]["result"] == {"trend": "偏强"}
    assert by_key["fundamental"]["status"] == "analyzing"
    assert by_key["fundamental"]["result"] is None
    assert by_key["capital"]["status"] == "failed"
    assert by_key["capital"]["error"] == "本步骤分析失败，请稍后重试"
    assert by_key["risk"]["status"] == "unknown"
    assert by_key["risk"]["result"] is None
    assert "raw provider output" not in response.text
    assert "deepseek-chat" not in response.text
    assert "provider internal traceback" not in response.text


def test_task_status_keeps_user_isolation(auth_client, test_db):
    _, Session = test_db
    db = Session()
    other = User(
        username="other-progress",
        email="other-progress@example.com",
        password_hash="not-used",
        role="user",
        tier="free",
        is_active=True,
    )
    db.add(other)
    db.flush()
    task = TaskRecord(task_type="stock_analysis", user_id=other.id, status="pending")
    db.add(task)
    db.commit()
    task_id = task.id
    db.close()

    response = auth_client.get(f"/api/tasks/{task_id}")

    assert response.status_code == 404


@pytest.mark.parametrize(
    ("code", "message"),
    [
        ("rate_limited", "数据源请求过于频繁，请稍后重试"),
        ("timeout", "数据源请求超时，请稍后重试"),
        ("not_configured", "数据源尚未配置"),
    ],
)
def test_task_status_maps_common_datahub_error_codes_safely(auth_client, test_db, code, message):
    _, Session = test_db
    db = Session()
    task = TaskRecord(
        task_type="stock_analysis",
        user_id=1,
        status="failed",
        error=f"{code}: provider secret details",
        input_snapshot={"stock_code": "600519"},
    )
    db.add(task)
    db.commit()
    task_id = task.id
    db.close()

    response = auth_client.get(f"/api/tasks/{task_id}")

    assert response.status_code == 200
    data = response.json()["data"]
    assert data["error"] == f"{code}: {message}"
    assert "provider secret" not in response.text
