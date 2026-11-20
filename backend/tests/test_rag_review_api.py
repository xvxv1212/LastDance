
from pathlib import Path

from app.infrastructure.citation_approval import ApprovedKnowledgeBase
from tests.conftest import agree_all


def test_rag_review_requires_owner_and_confirmed_facts(ctx):
    # 시험용 원문 승인 기록은 비어 있다.
    # 합성 자료를 승인된 공식 근거로 만들지 않는다.
    ctx.container.knowledge = ApprovedKnowledgeBase(
        ctx.container.knowledge, {}, Path(__file__).parent
    )

    agree_all(ctx, ctx.user_a)

    created = ctx.client.post(
        "/api/v1/check-sessions",
        headers=ctx.auth(ctx.user_a),
        json={
            "entry_type": "REFUND_CONCERN",
            "situation_text": "필라테스 회원권 환불에 관한 합성 시험입니다.",
        },
    )
    assert created.status_code == 201
    sid = created.json()["id"]
    url = f"/api/v1/check-sessions/{sid}/rag-review"

    # 확인 전에는 검색을 시작하지 않는다.
    assert ctx.client.post(
        url, headers=ctx.auth(ctx.user_a)
    ).status_code == 409

    registered = ctx.client.post(
        f"/api/v1/check-sessions/{sid}/facts",
        headers=ctx.svc(ctx.user_a),
        json={
            "facts": [
                {
                    "fact_key": "PAYMENT_AMOUNT",
                    "value_text": "600000원",
                    "source_kind": "STATEMENT",
                },
                {
                    "fact_key": "CONTRACT_DATE",
                    "source_kind": "UNKNOWN",
                },
            ]
        },
    )
    assert registered.status_code == 201

    facts = ctx.client.get(
        f"/api/v1/check-sessions/{sid}/facts",
        headers=ctx.auth(ctx.user_a),
    )
    assert facts.status_code == 200

    for fact in facts.json()["facts"]:
        action = (
            "ACKNOWLEDGE_UNKNOWN"
            if fact["source_kind"] == "UNKNOWN"
            else "CONFIRM"
        )
        response = ctx.client.post(
            f"/api/v1/facts/{fact['id']}/confirmation",
            headers=ctx.auth(ctx.user_a),
            json={"action": action},
        )
        assert response.status_code == 200

    # 다른 사용자의 점검은 존재 여부를 숨기는 404.
    assert ctx.client.post(
        url, headers=ctx.auth(ctx.user_b)
    ).status_code == 404

    # 인증 없는 요청은 거부.
    assert ctx.client.post(url).status_code == 401

    # 소유자 요청만 처리되며, 검색기 미설정 상태로 종료.
    review = ctx.client.post(
        url, headers=ctx.auth(ctx.user_a)
    )
    assert review.status_code == 200
    assert review.json() == {
        "search_status": "NOT_CONFIGURED",
        "candidate_count": 0,
        "generation_allowed": False,
    }

    # 모델 호출은 발생하지 않는다.
    assert ctx.model.calls == []
