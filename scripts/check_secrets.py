#!/usr/bin/env python
"""시크릿 스캔 게이트 — baseline에 없는 새 시크릿이 있으면 실패합니다.

`detect-secrets scan`으로 저장소를 스캔하고, 결과를 커밋된
`.secrets.baseline`과 비교합니다. baseline에 기록되지 않은 새 탐지가 있으면
종료 코드 1을 반환합니다. git 없이도 동작하므로 CodeBuild(소스 ZIP)에서
그대로 쓸 수 있습니다.

    uv run python scripts/check_secrets.py
"""

import json
import subprocess  # nosec B404
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
BASELINE = ROOT / ".secrets.baseline"

# 가상환경·의존성·생성물은 스캔에서 제외합니다(성능·오탐 방지).
# baseline도 반드시 동일한 패턴으로 생성해야 결과가 일치합니다.
EXCLUDE_FILES = r"(^|/)(\.venv|node_modules|cdk\.out|\.cache|\.git|\.ruff_cache)/"


def _results(payload: dict) -> set[tuple[str, str]]:
    """(파일, 해시) 집합으로 탐지 결과를 정규화합니다."""
    found: set[tuple[str, str]] = set()
    for filename, secrets in payload.get("results", {}).items():
        for secret in secrets:
            found.add((filename, secret.get("hashed_secret", "")))
    return found


def main() -> int:
    if not BASELINE.exists():
        sys.exit(f"baseline이 없습니다: {BASELINE}")

    baseline = json.loads(BASELINE.read_text(encoding="utf-8"))

    # 현재 트리를 새로 스캔해 결과 JSON을 받습니다.
    # (--baseline은 파일을 갱신만 하고 stdout으로 출력하지 않으므로 쓰지 않습니다.)
    # 고정 명령, shell 미사용, 사용자 입력 없음.
    scan = subprocess.run(  # nosec B603 B607
        ["detect-secrets", "scan", "--exclude-files", EXCLUDE_FILES],
        cwd=ROOT,
        capture_output=True,
        text=True,
        check=True,
    )
    current = json.loads(scan.stdout)

    new = _results(current) - _results(baseline)
    if new:
        print("baseline에 없는 새 시크릿 탐지:")
        for filename, _ in sorted(new):
            print(f"  - {filename}")
        print("\n실제 시크릿이면 제거하고, 오탐이면 baseline을 갱신하세요:")
        print("  uv run detect-secrets scan > .secrets.baseline")
        return 1

    print("시크릿 스캔 통과 — baseline 외 새 탐지 없음.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
