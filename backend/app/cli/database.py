"""Database migration and exact-head readiness commands.

Only the one-shot ``migrator`` container calls :func:`run_migrate`.  Long
lived API and worker containers use the command-line checks here instead of
trying to race one another through Alembic.
"""

from __future__ import annotations

import argparse
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable

from alembic import command
from alembic.config import Config
from alembic.migration import MigrationContext
from alembic.script import ScriptDirectory
from sqlalchemy import create_engine

from app.core.config import settings


@dataclass(frozen=True, slots=True)
class DatabaseCliResult:
    exit_code: int
    message: str


def _target_url() -> str:
    # TEST_DATABASE_URL is intentionally an explicit test-only override.  It
    # lets disposable PostgreSQL integration tests invoke this CLI without
    # changing the application's configured database.
    return os.getenv("TEST_DATABASE_URL") or settings.DATABASE_URL


def _alembic_config() -> Config:
    backend_dir = Path(__file__).resolve().parents[2]
    config = Config(str(backend_dir / "alembic.ini"))
    config.set_main_option("sqlalchemy.url", _target_url())
    return config


def _all_heads() -> set[str]:
    return set(ScriptDirectory.from_config(_alembic_config()).get_heads())


def _current_heads() -> set[str]:
    engine = create_engine(_target_url(), pool_pre_ping=True)
    try:
        with engine.connect() as connection:
            context = MigrationContext.configure(connection)
            return set(context.get_current_heads())
    finally:
        engine.dispose()


def _compare_heads() -> DatabaseCliResult:
    try:
        expected = _all_heads()
        current = _current_heads()
    except Exception:
        return DatabaseCliResult(1, "数据库迁移状态无法读取，请检查数据库连接")
    if current != expected:
        expected_text = ",".join(sorted(expected)) or "无"
        current_text = ",".join(sorted(current)) or "无"
        return DatabaseCliResult(
            1,
            f"数据库迁移版本不一致：当前 {current_text}，期望 {expected_text}",
        )
    return DatabaseCliResult(0, "数据库迁移版本已与全部 Alembic head 一致")


def run_wait_for_head() -> DatabaseCliResult:
    """Check every current revision against the complete Alembic head set."""

    return _compare_heads()


def run_migrate() -> DatabaseCliResult:
    """Apply migrations once and fail unless the resulting head set is exact."""

    try:
        command.upgrade(_alembic_config(), "head")
    except Exception:
        return DatabaseCliResult(1, "数据库迁移失败，请检查数据库连接和迁移日志")
    return _compare_heads()


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="睿见投研数据库运维命令")
    subparsers = parser.add_subparsers(dest="command", required=True)
    subparsers.add_parser("migrate", help="执行一次 Alembic upgrade head")
    subparsers.add_parser("wait-for-head", help="等待并校验全部 Alembic head")
    return parser


def main(argv: Iterable[str] | None = None) -> int:
    args = build_parser().parse_args(list(argv) if argv is not None else None)
    result = run_migrate() if args.command == "migrate" else run_wait_for_head()
    print(result.message)
    return result.exit_code


if __name__ == "__main__":  # pragma: no cover - exercised by the container
    raise SystemExit(main())


__all__ = [
    "DatabaseCliResult",
    "build_parser",
    "main",
    "run_migrate",
    "run_wait_for_head",
]
