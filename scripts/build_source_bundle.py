#!/usr/bin/env python
"""CI/CD 소스 번들(ZIP)을 재현 가능하게 생성합니다.

`git ls-files`로 추적 중인 파일만 담기 때문에 `.venv`, `node_modules`,
`cdk.out`, `.cache` 같은 생성물·의존성이 포함되지 않습니다. 작업 트리의
현재 내용을 그대로 담으므로, 커밋하지 않은 로컬 수정도 반영됩니다.

사용법:
    uv run python scripts/build_source_bundle.py [출력파일]

기본 출력 파일명은 restaurant-agent-src.zip 입니다.
그다음 S3에 업로드하면 CodePipeline이 실행됩니다:
    aws s3 cp restaurant-agent-src.zip \
        s3://restaurant-agent-src-262428258542/restaurant-agent-src.zip
"""

import subprocess  # nosec B404
import sys
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def tracked_files() -> list[str]:
    """git이 추적 중인 파일 목록을 반환합니다."""
    # 고정 인자 리스트, shell 미사용, 사용자 입력 없음 → 명령 주입 위험 없음
    result = subprocess.run(  # nosec B603 B607
        ["git", "ls-files"],
        cwd=ROOT,
        capture_output=True,
        text=True,
        check=True,
    )
    return [line for line in result.stdout.splitlines() if line.strip()]


def build(output: Path) -> None:
    files = tracked_files()
    if not files:
        sys.exit("추적 중인 파일이 없습니다. git 저장소 루트에서 실행하세요.")

    with zipfile.ZipFile(output, "w", zipfile.ZIP_DEFLATED) as zf:
        for rel in files:
            src = ROOT / rel
            if src.is_file():
                zf.write(src, arcname=rel)

    size = output.stat().st_size
    print(f"생성 완료: {output} ({len(files)}개 파일, {size:,} bytes)")


if __name__ == "__main__":
    out = Path(sys.argv[1]) if len(sys.argv) > 1 else ROOT / "restaurant-agent-src.zip"
    build(out)
