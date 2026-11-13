# AI Agent 레이어 — P0 최소 실행 그래프

`workflow.py`는 실제 LangGraph StateGraph 노드 1개를 실행합니다.
새로운 모델 API·HTTP 경로·사실 자동 승인 기능은 추가하지 않습니다.
외부 모델 호출은 기존 `InvokeExternalModel`을 거치므로 사용자 소유권, 동의,
개인정보 마스킹, 전송 검사, 감사 기록을 우회하지 않습니다.

## 현재 연결 상태

- 그래프 구조: **구현됨** (체크포인트 미사용, 원문 외부 추적 금지)
- 실제 모델 제공자/인증키: **미연결** (`DisabledModelClient` 기본 동작 유지)
- 실서버·프론트 화면: **미연결**
- 근거 검색·검증·보고서 자동 승격: **미구현**, 기존 검증 계약 유지
- 그래프 출력: **검증 전 초안**. 사실 정본·확정 법률 판단 아님

## 내부 실행 사용법

외부 공개 API에 바로 연결하지 말고, 로그인/토큰 검증을 통과한 내부 유스케이스에서만 호출합니다.

```python
# c: 요청의 Container, user_id: 이미 인증된 사용자의 UUID
flow = c.for_user(user_id).guarded_model_graph()
result = flow.run(thread_id=owned_thread_id, node_name="candidate_extract", text=synthetic_text)
# result.draft_text는 승인 전 원문 후보일 뿐이며 DB에 자동 기록되지 않습니다.
```

실행 전에 이 스레드가 동일 사용자의 점검/사건에 귀속되는지 확인하며,
사용자의 외부 전송 동의가 없으면 기존 검사에서 차단됩니다.
`LANGSMITH_TRACING` 또는 `LANGCHAIN_TRACING_V2`를 켠 환경에서는 실행하지 않습니다.
체크포인트 미연결을 `NOT_USED`로 거짓 보고하지 않습니다.

## 다음 단계(별도 PR)

1. 모델 제공자·데이터 처리 위치·요금·인증정보 저장을 승인 후 최소 어댑터 연결
2. 실제 모델 응답을 구조화 후보로 검증하고 사용자 확인 전 확정 사실 승격 차단
3. 승인된 근거 검색과 보고서 대조 노드를 분리, 무근거 인용 보류
4. 체크포인트를 쓰기 전 원문·개인정보 저장/파기 설계를 별도 검증
