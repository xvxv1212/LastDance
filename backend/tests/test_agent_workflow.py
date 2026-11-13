"""P0 LangGraph: 사용자 바인딩·기존 전송 검사 위임·비영속·추적 차단."""

from __future__ import annotations

import uuid
from unittest.mock import Mock

import pytest

from app.agent.workflow import build_guarded_model_graph
from app.application.use_cases.agent import InvokeExternalModel, ModelCallCommand, ModelCallOutcome
from app.container import Container
from app.domain.enums import GuardCode
from app.domain.errors import NotFoundError


def _mock_model(outcome: ModelCallOutcome) -> Mock:
    model = Mock(spec=InvokeExternalModel)
    model.execute.return_value = outcome
    return model


def test_real_graph_calls_existing_guard_with_bound_user(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("LANGSMITH_TRACING", raising=False)
    monkeypatch.delenv("LANGCHAIN_TRACING_V2", raising=False)
    owner = uuid.uuid4()
    audit_id = uuid.uuid4()
    invoker = _mock_model(ModelCallOutcome(True, audit_id, None, "검증 전 초안"))
    flow = build_guarded_model_graph(invoker, user_id=owner)

    result = flow.run(thread_id="synthetic-thread", node_name="candidate_extract", text="합성 사례")

    assert result.allowed is True
    assert result.audit_id == str(audit_id)
    assert result.draft_text == "검증 전 초안"
    assert result.blocked_code is None
    assert flow.persists_state is False
    invoker.execute.assert_called_once_with(
        ModelCallCommand(owner, "synthetic-thread", "candidate_extract", "합성 사례")
    )


def test_blocked_transfer_never_returns_a_draft(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("LANGSMITH_TRACING", raising=False)
    monkeypatch.delenv("LANGCHAIN_TRACING_V2", raising=False)
    invoker = _mock_model(
        ModelCallOutcome(False, uuid.uuid4(), GuardCode.NO_TRANSFER_BASIS, "출력되면 안 되는 텍스트")
    )
    flow = build_guarded_model_graph(invoker, user_id=uuid.uuid4())

    result = flow.run(thread_id="synthetic-thread", node_name="candidate_extract", text="합성 사례")

    assert result.allowed is False
    assert result.blocked_code == GuardCode.NO_TRANSFER_BASIS.value
    assert result.draft_text is None


def test_unconfigured_model_is_not_reported_as_success(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("LANGSMITH_TRACING", raising=False)
    monkeypatch.delenv("LANGCHAIN_TRACING_V2", raising=False)
    invoker = _mock_model(ModelCallOutcome(False, uuid.uuid4(), None, None))
    flow = build_guarded_model_graph(invoker, user_id=uuid.uuid4())
    result = flow.run(thread_id="synthetic-thread", node_name="candidate_extract", text="합성 사례")
    assert result.allowed is False and result.draft_text is None


def test_unknown_thread_ownership_error_is_not_swallowed(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("LANGSMITH_TRACING", raising=False)
    monkeypatch.delenv("LANGCHAIN_TRACING_V2", raising=False)
    invoker = Mock(spec=InvokeExternalModel)
    invoker.execute.side_effect = NotFoundError("실행 스레드를 찾을 수 없습니다.")
    flow = build_guarded_model_graph(invoker, user_id=uuid.uuid4())
    with pytest.raises(NotFoundError):
        flow.run(thread_id="other-user-thread", node_name="candidate_extract", text="합성 사례")


@pytest.mark.parametrize("setting", ["LANGSMITH_TRACING", "LANGCHAIN_TRACING_V2"])
def test_external_tracing_blocks_before_model_call(monkeypatch: pytest.MonkeyPatch, setting: str) -> None:
    monkeypatch.delenv("LANGSMITH_TRACING", raising=False)
    monkeypatch.delenv("LANGCHAIN_TRACING_V2", raising=False)
    invoker = _mock_model(ModelCallOutcome(True, uuid.uuid4(), None, "비공개"))
    flow = build_guarded_model_graph(invoker, user_id=uuid.uuid4())
    monkeypatch.setenv(setting, "true")
    with pytest.raises(RuntimeError, match="외부 추적"):
        flow.run(thread_id="synthetic-thread", node_name="candidate_extract", text="합성 사례")
    invoker.execute.assert_not_called()


def test_container_rejects_unbound_graph() -> None:
    container = object.__new__(Container)
    container._acting_user_id = None
    with pytest.raises(RuntimeError, match="사용자"):
        container.guarded_model_graph()
