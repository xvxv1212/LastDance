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

## P1 — 외부 모델 연결 후보 추출(명시적으로 켠 경우만)

- 새 `app/infrastructure/openai_responses_model.py`는 기존 `ExternalModelClient` 포트를 구현합니다.
- `build_container()`에서 환경변수 세 개를 **모두** 명시적으로 설정해야만 활성화됩니다.
  `HJ_MODEL_PROVIDER=OPENAI_RESPONSES`, `HJ_OPENAI_MODEL=<실제 접근 권한이 있는 모델 ID>`,
  `HJ_OPENAI_API_KEY=<비공개 키>` 입니다. 설정이 없으면 기존 `DisabledModelClient`가 유지됩니다.
- API 키를 Git·공유 문서·터미널 로그·테스트 데이터에 저장하지 않습니다. 실제 호출은 합성 사례와
  필요한 사용자 동의·전송 근거 확인 후에만 수행합니다. OpenAI API를 사용한다는 선택은 이번
  준비용 선택지이며, Jev 등 다른 제공자·모델을 승인하거나 연결한 것은 아닙니다.
- 전송은 반드시 기존 `InvokeExternalModel`의 사용자 소유권·마스킹·동의/국외이전 검사를 거칩니다.
  내부 서비스 토큰의 쓰기 권한을 자동 확대하지 않고, 추가 공개 HTTP 경로를 만들지 않습니다.
- 후보 추출(`candidate_extract`)만 허용하며 `UNKNOWN` 값은 `null`, 문서 출처 `DOCUMENT`는
  이 단계에서 허용하지 않습니다. 후보는 사용자 확인 전 정보로, DB 저장·사실 확정·인용에
  자동 반영하지 않습니다. 법률 결론·가짜 날짜·확정 환불액도 차단합니다.
- Responses API에 구조화 JSON 스키마 요청, `store=false`, 외부 추적 비활성, 리디렉션 차단,
  제한시간·응답 크기 상한·거부/불완전 응답 차단을 적용합니다.
- 현재 **실제 외부 모델 호출·비용 측정·운영 개인정보 처리 검증은 NOT RUN**입니다.
  `tests/test_openai_responses_model.py`는 모두 가짜 API 응답을 사용합니다.

테스트: `python -m pytest -q -ra`와 `python -m ruff check .`를 정상 설치된 Python 3.11 환경에서
실행합니다. P1 시험 외의 DB 전용 미실행 사유는 종전과 동일하게 구분합니다.
