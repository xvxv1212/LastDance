"""P1 모델 어댑터의 네트워크 없는 합성 시험. 실제 API 호출·비용 발생 없음."""

from __future__ import annotations

import json
import urllib.error
from typing import Any

import pytest

from app.infrastructure.openai_responses_model import (
    ModelResponseRejectedError,
    ModelTransportFailedError,
    OpenAIResponsesCandidateClient,
    _secure_post,
    _validate_candidates,
    configured_model_client,
)

KEY = "synthetic-api-key-only-not-real"
MODEL = "synthetic-model"


def _candidate(*, source: str = "STATEMENT", value: str | None = "4회") -> dict[str, Any]:
    return {
        "facts": [{"fact_key": "USED_SESSIONS", "source_kind": source, "value_text": value}],
        "questions": ["계약일을 알고 계신가요?"],
    }


def _response(payload: dict[str, Any] | None = None, *, status: str = "completed") -> bytes:
    if payload is None:
        payload = _candidate()
    return json.dumps(
        {
            "status": status,
            "output": [
                {
                    "type": "message",
                    "role": "assistant",
                    "content": [{"type": "output_text", "text": json.dumps(payload, ensure_ascii=False)}],
                }
            ],
            "usage": {"input_tokens": 17, "output_tokens": 9},
        },
        ensure_ascii=False,
    ).encode("utf-8")


def _client(
    reply: bytes | None = None, *, calls: list[dict[str, Any]] | None = None
) -> OpenAIResponsesCandidateClient:
    def fake_send(body: bytes, key: str, timeout: float) -> bytes:
        if calls is not None:
            calls.append({"body": json.loads(body), "key": key, "timeout": timeout})
        return reply if reply is not None else _response()

    return OpenAIResponsesCandidateClient(model=MODEL, api_key=KEY, post=fake_send)


def test_default_provider_disabled_even_if_key_exists() -> None:
    assert configured_model_client({}) is None
    assert configured_model_client({"HJ_OPENAI_API_KEY": KEY, "HJ_OPENAI_MODEL": MODEL}) is None


@pytest.mark.parametrize(
    "env",
    [
        {"HJ_MODEL_PROVIDER": "UNSUPPORTED", "HJ_OPENAI_API_KEY": KEY, "HJ_OPENAI_MODEL": MODEL},
        {"HJ_MODEL_PROVIDER": "OPENAI_RESPONSES", "HJ_OPENAI_MODEL": MODEL},
        {"HJ_MODEL_PROVIDER": "OPENAI_RESPONSES", "HJ_OPENAI_API_KEY": KEY},
    ],
)
def test_partial_or_unknown_provider_fails_closed(env: dict[str, str]) -> None:
    with pytest.raises(ValueError):
        configured_model_client(env)


def test_valid_provider_explicit_opt_in() -> None:
    client = configured_model_client(
        {
            "HJ_MODEL_PROVIDER": "OPENAI_RESPONSES",
            "HJ_OPENAI_MODEL": MODEL,
            "HJ_OPENAI_API_KEY": KEY,
        }
    )
    assert isinstance(client, OpenAIResponsesCandidateClient)


def test_success_sends_structured_response_with_store_false_and_no_autoapproval() -> None:
    calls: list[dict[str, Any]] = []
    client = _client(calls=calls)
    result = client.complete("candidate_extract", "합성 필라테스 이용 4회")
    assert len(calls) == 1 and calls[0]["key"] == KEY
    body = calls[0]["body"]
    assert body["model"] == MODEL and body["store"] is False
    assert body["text"]["format"]["strict"] is True
    assert body["text"]["format"]["schema"]["additionalProperties"] is False
    assert body["input"][0]["content"][0]["text"] == "합성 필라테스 이용 4회"
    assert body["max_output_tokens"] <= 1200
    assert "승인" in body["instructions"]
    assert json.loads(result.text) == _candidate()
    assert (result.model_name, result.input_tokens, result.output_tokens) == (MODEL, 17, 9)


def test_unknown_never_has_value_or_document_source() -> None:
    assert json.loads(
        _validate_candidates(json.dumps(_candidate(source="UNKNOWN", value=None)))
    ) == _candidate(source="UNKNOWN", value=None)
    with pytest.raises(ModelResponseRejectedError):
        _validate_candidates(json.dumps(_candidate(source="UNKNOWN", value="0")))
    with pytest.raises(ModelResponseRejectedError):
        _validate_candidates(json.dumps(_candidate(source="DOCUMENT", value="4회")))


def test_missing_pdf_is_not_interpreted_as_missing_copy() -> None:
    observed = _candidate(source="UNKNOWN", value=None)
    observed["facts"][0]["fact_key"] = "CONTRACT_DATE"
    result = _client(_response(observed)).complete("candidate_extract", "PDF 미제출")
    parsed = json.loads(result.text)
    assert parsed["facts"][0]["source_kind"] == "UNKNOWN"
    assert parsed["facts"][0]["value_text"] is None


@pytest.mark.parametrize(
    "candidate",
    [
        {"facts": [], "questions": [], "approved": True},
        {
            "facts": [{"fact_key": "NOT_IN_ENUM", "source_kind": "STATEMENT", "value_text": "4회"}],
            "questions": [],
        },
        {
            "facts": [
                {"fact_key": "USED_SESSIONS", "source_kind": "STATEMENT", "value_text": "4회", "page": 2}
            ],
            "questions": [],
        },
        {
            "facts": [{"fact_key": "USED_SESSIONS", "source_kind": "STATEMENT", "value_text": "4회"}] * 2,
            "questions": [],
        },
        {
            "facts": [
                {
                    "fact_key": "USED_SESSIONS",
                    "source_kind": "STATEMENT",
                    "value_text": "반드시 환불 대상입니다",
                }
            ],
            "questions": [],
        },
        {"facts": [], "questions": ["반드시 환불받을 수 있습니다"]},
        {"facts": [], "questions": [""]},
    ],
)
def test_invalid_or_legal_conclusion_outputs_rejected(candidate: dict[str, Any]) -> None:
    with pytest.raises(ModelResponseRejectedError):
        _client(_response(candidate)).complete("candidate_extract", "합성 사례")


def test_empty_or_truncated_or_refusal_raises_without_success() -> None:
    with pytest.raises(ModelResponseRejectedError):
        _client(_response(status="incomplete")).complete("candidate_extract", "합성 사례")
    refusal = {
        "status": "completed",
        "output": [
            {"type": "message", "role": "assistant", "content": [{"type": "refusal", "refusal": "거부"}]}
        ],
    }
    with pytest.raises(ModelResponseRejectedError):
        _client(json.dumps(refusal).encode()).complete("candidate_extract", "합성 사례")
    with pytest.raises(ModelResponseRejectedError):
        _client(b'{"status":"completed","output":[]}').complete("candidate_extract", "합성 사례")


def test_invalid_node_or_input_does_not_make_http_request() -> None:
    calls: list[dict[str, Any]] = []
    client = _client(calls=calls)
    with pytest.raises(ModelResponseRejectedError):
        client.complete("make_legal_conclusion", "합성 사례")
    with pytest.raises(ModelResponseRejectedError):
        client.complete("candidate_extract", " " * 10)
    with pytest.raises(ModelResponseRejectedError):
        client.complete("candidate_extract", "가" * 10001)
    assert not calls


def test_transport_error_never_exposes_key_or_response() -> None:
    def fail(body: bytes, key: str, timeout: float) -> bytes:
        raise RuntimeError(f"bad-request {key} private-text")

    client = OpenAIResponsesCandidateClient(model=MODEL, api_key=KEY, post=fail)
    with pytest.raises(ModelTransportFailedError) as caught:
        client.complete("candidate_extract", "합성 사례")
    assert KEY not in str(caught.value)
    assert "private-text" not in str(caught.value)


def test_no_follow_redirects(monkeypatch: pytest.MonkeyPatch) -> None:
    class FakeOpener:
        def open(self, request: Any, timeout: float) -> Any:
            raise urllib.error.HTTPError(request.full_url, 307, f"redirect {KEY}", {}, None)

    monkeypatch.setattr("urllib.request.build_opener", lambda *_: FakeOpener())
    with pytest.raises(ModelTransportFailedError) as caught:
        _secure_post(b"{}", KEY, 2.0)
    assert KEY not in str(caught.value)


def test_rejects_non_json_or_oversized_reply() -> None:
    with pytest.raises(ModelResponseRejectedError):
        _client(b"not-json").complete("candidate_extract", "합성 사례")
    with pytest.raises(ModelResponseRejectedError):
        _client(b"{" + b" " * 65537).complete("candidate_extract", "합성 사례")
