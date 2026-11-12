"""시험 계정 발급: `HJ_TEST_ACCOUNT_PASSWORD=... python -m scripts.issue_test_account --email t1@example.com`

필요 환경변수: HJ_JWT_SECRET, HJ_DATABASE_URL, HJ_AUTH_DATABASE_URL(인증 전용 접속), HJ_STORAGE_DIR.
"""

from __future__ import annotations

import sys

from app.interface.cli.accounts import main

if __name__ == "__main__":
    sys.exit(main())
