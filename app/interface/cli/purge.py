"""정기 파기 명령(v0.11 SUP-03). 운영에서는 스케줄러(cron 등)가 하루 1회 이상 실행한다.

    python -m scripts.run_purge

- 파기 전용 DB 접속(HJ_BATCH_DATABASE_URL, BYPASSRLS 롤)으로 실행한다.
- 출력은 건수와 상태 코드뿐이다(개인정보·경로·thread_id 를 출력하지 않는다).
- 종료 코드: 0 = DB 밖 자료까지 모두 파기 / 3 = 아직 못 지운 대상이 남음(다음 실행에서 재시도) / 1 = 실행 오류
  체크포인트 저장소가 연결되지 않았으면 3 이다 — 연결되지 않은 것을 '파기 완료'로 보고하지 않는다.
"""

from __future__ import annotations

import dataclasses
import json
import logging

from app.container import Container

logger = logging.getLogger(__name__)

EXIT_OK, EXIT_ERROR, EXIT_PENDING = 0, 1, 3


def run_purge(container: Container) -> tuple[int, dict[str, object]]:
    report = container.purge_expired_data().execute()
    result: dict[str, object] = dataclasses.asdict(report)
    result["complete"] = report.complete
    return (EXIT_OK if report.complete else EXIT_PENDING), result


def main(container: Container | None = None) -> int:
    try:
        if container is None:
            from app.main import build_container

            container = build_container()
        code, result = run_purge(container)
    except Exception:
        logger.exception("정기 파기 실행 오류")  # 원인은 서버 로그에만
        print(json.dumps({"complete": False, "error": "PURGE_RUN_FAILED"}))
        return EXIT_ERROR
    print(json.dumps(result, ensure_ascii=False, sort_keys=True))
    return code
