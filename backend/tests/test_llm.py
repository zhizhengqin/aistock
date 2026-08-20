"""Compatibility assertions for the post-Task-8 LLM facade."""

import pytest

from app.core import llm as llm_module
from app.services.llm.call_executor import LlmCallExecutor
from app.services.llm.errors import LlmError
from app.services.llm.execution_service import LlmExecutionService


def test_core_facade_exports_durable_execution_services():
    assert llm_module.LlmCallExecutor is LlmCallExecutor
    assert llm_module.LlmExecutionService is LlmExecutionService
    assert not hasattr(llm_module, "MOCK_RESPONSES")


@pytest.mark.asyncio
async def test_legacy_chat_entrypoint_is_rejected_without_business_fallback():
    with pytest.raises(LlmError) as exc_info:
        await llm_module.chat(
            [{"role": "user", "content": "return JSON"}],
            user_id=0,
            module="test",
        )
    assert exc_info.value.code == "llm_legacy_chat_removed"
    assert "mock" not in str(exc_info.value).lower()
