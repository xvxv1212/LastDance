"""최소 LangGraph 실행: 기존 외부 전송 검사와 감사 기록을 우회하지 않는다.

P0 범위:
- 인증된 사용자에 바인딩된 InvokeExternalModel 유스케이스만 호출
- LangGraph 노드 1개, 체크포인트 없음, 외부 추적 비활성 환경에서만 실행
- 출력은 '검증 전 초안'이며 사실 승인·보고서 등록을 수행하지 않음

이 모듈 자체는 외부 모델 SDK나 시민 인증 토큰을 직접 다루지 않는다.
"""

from __future__ import annotations

import os
import uuid
from dataclasses import dataclass
from typing import TypedDict

from langgraph.graph import END, START, StateGraph

from app.application.use_cases.agent import InvokeExternalModel, ModelCallCommand


class _FlowInput(TypedDict):
    thread_id: str
    node_name: str
    text: str


class _FlowOutput(TypedDict):
    allowed: bool
    audit_id: str
    blocked_code: str | None
    draft_text: str | None


class _FlowState(_FlowInput, _FlowOutput):
    pass


@dataclass(frozen=True)
class DraftOutcome:
    """기계적으로 실행된 결과. 사용자 승인 또는 법률 검증을 뜻하지 않는다."""

    allowed: bool
    audit_id: str
    blocked_code: str | None
    draft_text: str | None


def _assert_external_tracing_disabled() -> None:
    """원문 상태의 외부 추적을 막는다. 체크포인트 미사용과는 별도 통제다."""
    enabled = {"1", "true", "yes", "on"}
    for name in ("LANGSMITH_TRACING", "LANGCHAIN_TRACING_V2"):
        if os.environ.get(name, "").strip().lower() in enabled:
            raise RuntimeError("원문이 포함된 AI 실행은 외부 추적이 꺼진 환경에서만 허용합니다.")


class GuardedModelFlow:
    """인증 사용자 문맥에 묶인 일회성 LangGraph 실행기.

    체크포인트를 붙이지 않는다: 입력 텍스트·외부 모델 출력이 저장소에
    자동 기록되면 기존 파기/동의 계약을 우회할 수 있기 때문이다.
    """

    def __init__(self, invoke_model: InvokeExternalModel, *, user_id: uuid.UUID) -> None:
        _assert_external_tracing_disabled()

        def guarded_call(state: _FlowState) -> _FlowOutput:
            outcome = invoke_model.execute(
                ModelCallCommand(
                    user_id=user_id,
                    thread_id=state["thread_id"],
                    node_name=state["node_name"],
                    text=state["text"],
                )
            )
            return {
                "allowed": outcome.allowed,
                "audit_id": str(outcome.audit_id),
                "blocked_code": outcome.blocked_code.value if outcome.blocked_code else None,
                # 차단되거나 모델이 실패한 출력을 사용자에게 노출하지 않는다.
                "draft_text": outcome.output_text if outcome.allowed else None,
            }

        graph = StateGraph(_FlowState, input_schema=_FlowInput, output_schema=_FlowOutput)
        graph.add_node("model_call_via_existing_guard", guarded_call)
        graph.add_edge(START, "model_call_via_existing_guard")
        graph.add_edge("model_call_via_existing_guard", END)
        self._graph = graph.compile()  # 의도적으로 checkpointer=None

    @property
    def persists_state(self) -> bool:
        return self._graph.checkpointer is not None

    def run(self, *, thread_id: str, node_name: str, text: str) -> DraftOutcome:
        _assert_external_tracing_disabled()  # 구성 이후 환경 변경에도 차단
        result = self._graph.invoke(
            {"thread_id": thread_id, "node_name": node_name, "text": text},
            config={"callbacks": []},
        )
        return DraftOutcome(**result)


def build_guarded_model_graph(
    invoke_model: InvokeExternalModel, *, user_id: uuid.UUID
) -> GuardedModelFlow:
    """기존 업무 유스케이스를 호출하는 실제 LangGraph 실행기를 만든다."""
    return GuardedModelFlow(invoke_model, user_id=user_id)
