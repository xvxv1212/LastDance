"""새 로컬 PostgreSQL에서 실제 체크포인터·파기 재시도를 검증하고 증거를 저장한다.

    python -m scripts.verify_checkpoint_lifecycle --output var/checkpoint_verification

외부 URL 인자는 없으며 DB 환경변수가 설정되어 있으면 시험 전에 차단한다.
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
import xml.etree.ElementTree as ET
from datetime import datetime
from importlib.metadata import PackageNotFoundError, version
from pathlib import Path
from zoneinfo import ZoneInfo

from scripts.isolated_postgres import IsolatedPostgresUnavailableError, reject_external_database_environment


def main() -> int:
    parser = argparse.ArgumentParser(description="격리 PostgreSQL 체크포인트 파기 검증")
    parser.add_argument("--output", type=Path, default=Path("var/checkpoint_verification"))
    args = parser.parse_args()
    output = args.output.resolve()
    output.mkdir(parents=True, exist_ok=True)
    result: dict[str, object] = {
        "checked_at_kst": datetime.now(ZoneInfo("Asia/Seoul")).isoformat(),
        "status": "BLOCKED",
        "actual_model": "NOT CONNECTED",
        "actual_langgraph_execution": "NOT RUN",
        "production_db_touched": False,
        "external_db_url_used": False,
        "original_test_database_guard_changed": False,
        "scope": "새 로컬 PostgreSQL의 공식 PostgresSaver 및 파기 작업·권한·재시도 계약",
        "limits": "공용 서버·실제 사용자자료·백업·운영 전체 파기 및 실행 중 Graph 중지 검증은 별도",
    }
    try:
        reject_external_database_environment()
        result["packages"] = {
            name: version(name)
            for name in ("langgraph-checkpoint-postgres", "langgraph-checkpoint", "pgserver")
        }
    except (IsolatedPostgresUnavailableError, PackageNotFoundError) as exc:
        result["blocked_reason"] = type(exc).__name__
        (output / "summary.json").write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n")
        print(json.dumps(result, ensure_ascii=False))
        return 3
    xml_path = output / "pytest.xml"
    completed = subprocess.run(  # noqa: S603 - 현재 인터프리터와 고정 시험 파일만 실행
        [sys.executable, "-m", "pytest", "tests/test_postgres_checkpoint.py", "-q", f"--junitxml={xml_path}"],
        cwd=Path(__file__).resolve().parents[1],
        capture_output=True,
        text=True,
        check=False,
    )
    (output / "pytest.log").write_text(completed.stdout + completed.stderr, encoding="utf-8")
    if xml_path.exists():
        root = ET.parse(xml_path).getroot()  # noqa: S314 - 방금 생성한 로컬 pytest 결과
        result["postgresql"] = {
            prop.attrib["name"]: prop.attrib.get("value") for prop in root.iter("property")
        }
        cases = list(root.iter("testcase"))
        failed = sum(case.find("failure") is not None or case.find("error") is not None for case in cases)
        skipped = sum(case.find("skipped") is not None for case in cases)
        result.update({"passed": len(cases) - failed - skipped, "failed": failed, "skipped": skipped})
        result["status"] = "FAIL" if completed.returncode or failed else "BLOCKED" if skipped else "PASS"
    else:
        result["status"] = "FAIL"
    (output / "summary.json").write_text(
        json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    print(json.dumps(result, ensure_ascii=False))
    return 0 if result["status"] == "PASS" else 3 if result["status"] == "BLOCKED" else 1


if __name__ == "__main__":
    raise SystemExit(main())
