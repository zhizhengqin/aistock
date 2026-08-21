from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import text


pytestmark = pytest.mark.integration

UTC = timezone.utc
NOW = datetime(2026, 8, 21, 12, 0, tzinfo=UTC)


def _explain(connection, statement: str, **params):
    raw = connection.execute(
        text(f"EXPLAIN (ANALYZE, BUFFERS, FORMAT JSON) {statement}"), params
    ).scalar_one()
    if isinstance(raw, str):
        raw = json.loads(raw)
    return raw[0]["Plan"]


def _nodes(plan):
    yield plan
    for child in plan.get("Plans", []):
        yield from _nodes(child)


def test_llm_query_plans_cover_100k_rows_without_unbounded_supported_scans(
    postgres_engine,
):
    """Exercise the production-shaped queries against a disposable 100k-row dataset.

    Every supported production path is intentionally selective and must use the
    narrow index that owns its predicate.  The fixture keeps pending, stale-locked,
    and terminal-cleanup rows sparse enough for PostgreSQL to reject a full scan.
    """

    with postgres_engine.begin() as connection:
        # Keep all rows disposable and use set-based SQL for predictable fixture time.
        connection.execute(
            text(
                """
                INSERT INTO llm_model_configs (
                    id, provider, display_name, model_name, base_url,
                    encrypted_api_key, encryption_key_id, envelope_version, nonce,
                    credential_version, lifecycle_status, runtime_fingerprint,
                    version, created_at, updated_at
                ) VALUES (
                    'cfg-plan', 'deepseek', 'Plan fixture', 'deepseek-chat',
                    'https://example.test/v1', 'ciphertext-only', 'test-key', 'v1',
                    'nonce', 'credential-v1', 'active', 'plan-fingerprint', 1,
                    :now, :now
                )
                """
            ),
            {"now": NOW},
        )
        connection.execute(
            text(
                """
                INSERT INTO llm_usage (
                    user_id, module, model, prompt_tokens, completion_tokens, cost_fen,
                    task_id, model_config_id, provider_snapshot, model_snapshot,
                    input_price_snapshot, output_price_snapshot, input_tokens,
                    output_tokens, cost_micro_yuan, status, error_code, created_at, updated_at
                )
                SELECT NULL, 'analysis', 'deepseek-chat', 10, 20, 3,
                       NULL, 'cfg-plan', 'deepseek', 'deepseek-chat', 1, 1,
                       10, 20, 30, 'success', NULL,
                       :now - (g * interval '1 day' / 100),
                       :now - (g * interval '1 day' / 100)
                FROM generate_series(1, 100000) AS series(g)
                """
            ),
            {"now": NOW},
        )
        connection.execute(
            text(
                """
                INSERT INTO task_records (
                    task_type, user_id, model_config_id, ref_id, status, progress,
                    error, result_json, started_at, finished_at, input_snapshot,
                    input_snapshot_hash, prompt_version, execution_token,
                    lease_expires_at, heartbeat_at, created_at, updated_at
                )
                SELECT 'plan-test', NULL, NULL, NULL,
                       CASE
                           WHEN g % 100 = 2 THEN 'success'
                           WHEN g % 100 = 1 THEN 'running'
                           WHEN g % 100 = 0 THEN 'pending'
                           ELSE 'success'
                       END,
                       0, NULL, NULL, NULL, NULL, '{}'::json,
                       NULL, NULL, NULL, NULL, NULL,
                       :now - (g * interval '1 day' / 100),
                       :now - (g * interval '1 day' / 100)
                FROM generate_series(1, 100000) AS series(g)
                """
            ),
            {"now": NOW},
        )
        connection.execute(
            text(
                """
                INSERT INTO task_outbox (
                    task_id, status, attempts, available_at, locked_at, locked_by,
                    last_error, created_at, updated_at
                )
                SELECT id,
                       CASE
                           WHEN id % 100 = 0 THEN 'pending'
                           WHEN id % 100 = 1 THEN 'locked'
                           ELSE 'delivered'
                       END,
                       0,
                       :now - interval '1 hour',
                       CASE WHEN id % 100 = 1 THEN :now - interval '2 hours' ELSE NULL END,
                       NULL, NULL, :now, :now
                FROM task_records
                """
            ),
            {"now": NOW},
        )
        connection.execute(
            text(
                """
                INSERT INTO llm_call_attempts (
                    id, task_id, model_config_id, operation_id, operation_type, step_key,
                    attempt_no, provider_snapshot, model_snapshot, runtime_fingerprint,
                    input_snapshot_hash, prompt_version, reservation_id, status,
                    response_model_snapshot, input_tokens, output_tokens,
                    input_price_snapshot, output_price_snapshot, cost_micro_yuan,
                    usage_source, error_code, error_message, result_json, result_hash,
                    result_schema_version, response_metadata_json, created_at, updated_at
                )
                SELECT md5('attempt-' || id::text), id, NULL, md5(id::text), 'task', 'plan.test.v1', 1,
                       'deepseek', 'deepseek-chat', 'fingerprint', NULL, 'v1', NULL,
                       CASE WHEN id % 100 = 2 THEN 'success' ELSE 'started' END,
                       NULL, 10, 20, 1, 1, 30, 'plan', NULL, NULL,
                       CASE WHEN id % 100 = 2 THEN '{"payload":"private"}'::json ELSE NULL END,
                       md5(id::text), 'v1', NULL,
                       CASE
                           WHEN id % 100 = 2
                               THEN :now - interval '91 days' - (id % 1000) * interval '1 minute'
                           ELSE :now - interval '1 day'
                       END,
                       CASE
                           WHEN id % 100 = 2
                               THEN :now - interval '91 days' - (id % 1000) * interval '1 minute'
                           ELSE :now - interval '1 day'
                       END
                FROM task_records
                """
            ),
            {"now": NOW},
        )
        connection.execute(
            text(
                """
                INSERT INTO llm_token_reservations (
                    id, task_id, step_key, budget_date, reserved_tokens,
                    settled_tokens, status, lease_expires_at, created_at, updated_at
                )
                SELECT md5('reservation-' || id::text), id, 'plan.test.v1',
                       :budget_date, 100, 0, 'reserved', :now + interval '1 hour',
                       :now, :now
                FROM task_records
                WHERE id % 10 = 3 OR id % 1000 = 2
                """
            ),
            {"now": NOW, "budget_date": NOW.date()},
        )
        for table in (
            "llm_usage",
            "task_outbox",
            "llm_call_attempts",
            "task_records",
            "llm_token_reservations",
        ):
            connection.execute(text(f"ANALYZE {table}"))

        usage_plan = _explain(
            connection,
            """
            SELECT (created_at AT TIME ZONE 'Asia/Shanghai')::date AS usage_date,
                   module, provider_snapshot, model_snapshot, model_config_id,
                   SUM(input_tokens), SUM(output_tokens)
            FROM llm_usage
            WHERE created_at >= :since AND created_at < :until
            GROUP BY 1, module, provider_snapshot, model_snapshot, model_config_id
            """,
            since=NOW - timedelta(days=1),
            until=NOW,
        )
        pending_plan = _explain(
            connection,
            """
            SELECT id, task_id
            FROM task_outbox
            WHERE status = 'pending' AND available_at <= :now
            ORDER BY available_at, id
            LIMIT 50
            """,
            now=NOW,
        )
        stale_plan = _explain(
            connection,
            """
            SELECT id
            FROM task_outbox
            WHERE status = 'locked' AND locked_at < :cutoff
            """,
            cutoff=NOW - timedelta(minutes=30),
        )
        cleanup_plan = _explain(
            connection,
            """
            SELECT a.id
            FROM llm_call_attempts AS a
            JOIN task_records AS t ON t.id = a.task_id
            WHERE a.created_at < :cutoff
              AND a.status IN ('success', 'failed', 'failed_unknown')
              AND t.status IN ('success', 'failed', 'failed_unknown')
              AND NOT EXISTS (
                    SELECT 1 FROM task_outbox AS o
                    WHERE o.task_id = t.id AND o.status IN ('pending', 'locked')
              )
              AND NOT EXISTS (
                    SELECT 1 FROM llm_token_reservations AS r
                    WHERE r.task_id = t.id AND r.status = 'reserved'
              )
              AND NOT (t.execution_token IS NOT NULL AND t.lease_expires_at > :now)
              AND (a.result_json IS NOT NULL OR a.response_metadata_json IS NOT NULL)
            ORDER BY a.created_at, a.id
            LIMIT 500
            """,
            cutoff=NOW - timedelta(days=90),
            now=NOW,
        )

        plans = {
            "usage": usage_plan,
            "pending_outbox": pending_plan,
            "stale_recovery": stale_plan,
            "cleanup": cleanup_plan,
        }
        print(
            "LLM_QUERY_PLAN_SUMMARY "
            + json.dumps(
                {
                    name: {
                        "node_type": plan.get("Node Type"),
                        "index_names": [
                            node.get("Index Name")
                            for node in _nodes(plan)
                            if node.get("Index Name")
                        ],
                        "actual_rows": plan.get("Actual Rows"),
                        "plan_rows": plan.get("Plan Rows"),
                        "actual_time_ms": plan.get("Actual Total Time"),
                    }
                    for name, plan in plans.items()
                },
                sort_keys=True,
            )
        )
        expected_indexes = {
            "usage": {"ix_llm_usage_created_config"},
            "pending_outbox": {"ix_task_outbox_pending_available"},
            "stale_recovery": {"ix_task_outbox_locked_at"},
            "cleanup": {
                "ix_llm_call_attempts_created_config_status",
                "ix_llm_token_reservations_task_reserved",
            },
        }
        for name, plan in plans.items():
            index_names = {
                node.get("Index Name") for node in _nodes(plan) if node.get("Index Name")
            }
            assert expected_indexes[name] <= index_names, {
                "query": name,
                "expected": sorted(expected_indexes[name]),
                "actual": sorted(index_names),
            }
            seq_scans = [node for node in _nodes(plan) if node.get("Node Type") == "Seq Scan"]
            assert not seq_scans, {
                "query": name,
                "seq_scans": seq_scans,
            }
        assert usage_plan.get("Actual Rows", 0) <= 1_000
        assert usage_plan.get("Plan Rows", 0) <= 1_000
        assert pending_plan.get("Actual Rows", 0) <= 50
        assert pending_plan.get("Plan Rows", 0) <= 100
        assert stale_plan.get("Actual Rows", 0) <= 5_000
        assert stale_plan.get("Plan Rows", 0) <= 5_000
        assert cleanup_plan.get("Actual Rows", 0) <= 500
        assert cleanup_plan.get("Plan Rows", 0) <= 500


def test_retention_indexes_upgrade_downgrade_and_rebuild(migration_cycle):
    migration_cycle.upgrade()

    def index_names() -> set[str]:
        with migration_cycle.engine.connect() as connection:
            return set(
                connection.execute(
                    text(
                        "SELECT indexname FROM pg_indexes "
                        "WHERE schemaname = current_schema() "
                        "AND indexname IN ("
                        "'ix_task_outbox_locked_at',"
                        "'ix_llm_token_reservations_task_reserved')"
                    )
                ).scalars()
            )

    assert index_names() == {
        "ix_task_outbox_locked_at",
        "ix_llm_token_reservations_task_reserved",
    }
    migration_cycle.downgrade()
    assert index_names() == set()
    migration_cycle.upgrade()
    assert index_names() == {
        "ix_task_outbox_locked_at",
        "ix_llm_token_reservations_task_reserved",
    }
