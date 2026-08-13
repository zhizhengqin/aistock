"""Behavior tests for the exact-Alembic-head database CLI."""

from __future__ import annotations

from app.cli import database


def test_wait_for_head_requires_complete_revision_set(monkeypatch):
    monkeypatch.setattr(database, "_current_heads", lambda: {"head-a"})
    monkeypatch.setattr(database, "_all_heads", lambda: {"head-a", "head-b"})

    result = database.run_wait_for_head()

    assert result.exit_code != 0
    assert "迁移" in result.message


def test_wait_for_head_accepts_exact_head_set(monkeypatch):
    monkeypatch.setattr(database, "_current_heads", lambda: {"head-a", "head-b"})
    monkeypatch.setattr(database, "_all_heads", lambda: {"head-a", "head-b"})

    result = database.run_wait_for_head()

    assert result.exit_code == 0
