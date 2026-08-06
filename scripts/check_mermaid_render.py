#!/usr/bin/env python
"""Mermaid 렌더링 게이트 — 실제 mermaid-cli로 다이어그램을 그려봅니다.

`scripts/check_docs.py`는 Mermaid 블록의 구조(다이어그램 키워드, 괄호 균형)만
가볍게 검사하고 실제 렌더링은 보장하지 않습니다. 이 스크립트는 Markdown에서
```mermaid 블록을 추출해 각각 `@mermaid-js/mermaid-cli`(mmdc)로 SVG를 실제로
렌더링해, 문법 오류로 GitHub에서 다이어그램이 깨지는 것을 배포 전에 잡습니다.

요구 사항: Node.js/npx. mmdc는 `npx --yes`로 그때그때 받아 실행하므로 별도
설치 단계가 필요 없지만, 최초 실행은 다운로드 때문에 느립니다. npx를 찾지
못하면(Node가 없는 환경) 검사를 건너뛰고 exit 0을 반환합니다 — 렌더링 검증은
결정적 문서 게이트(check_docs.py)를 보강하는 것이지 대체하지 않습니다.

    uv run python scripts/check_mermaid_render.py
"""

from __future__ import annotations

import shutil
import subprocess  # nosec B404 - 고정 명령, shell 미사용, 사용자 입력은 임시 파일 경로로만 전달.
import sys
import tempfile
from pathlib import Path

# Windows 콘솔의 기본 레거시 코드페이지(cp949 등)는 em dash 같은 문자를
# 인코딩하지 못해 UnicodeEncodeError로 게이트가 죽습니다. UTF-8로 강제해
# 플랫폼과 무관하게 출력이 성공하도록 합니다.
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

sys.path.insert(0, str(Path(__file__).resolve().parent))

from check_docs import _find_files, extract_mermaid_blocks  # noqa: E402

MERMAID_CLI_VERSION = "11.16.0"
RENDER_TIMEOUT_SECONDS = 60

# CodeBuild 표준 이미지는 root로 실행되어 Chromium sandbox를 만들 수 없으므로
# 비활성화합니다. 이 저장소가 신뢰하는 로컬 Markdown만 렌더링합니다.
PUPPETEER_CONFIG = '{"args": ["--no-sandbox", "--disable-setuid-sandbox"]}'


def _resolve_npx() -> str | None:
    """npx의 실제 실행 파일 경로를 찾습니다.

    Windows에서는 npx가 `npx.cmd`로 설치되어 `shell=False`인 subprocess가
    맨 이름 `npx`로는 찾지 못합니다. `shutil.which`로 PATHEXT까지 포함해
    해석해야 플랫폼과 무관하게 동작합니다.
    """
    resolved = shutil.which("npx")
    if resolved is None:
        return None
    try:
        subprocess.run(  # nosec B603 - 해석된 절대 경로만 실행, 고정 인자.
            [resolved, "--version"],
            capture_output=True,
            text=True,
            timeout=30,
            check=True,
        )
        return resolved
    except (OSError, subprocess.CalledProcessError, subprocess.TimeoutExpired):
        return None


def _render_block(mmdc_command: list[str], workdir: Path, index: int, body: str) -> str | None:
    """단일 Mermaid 블록을 렌더링하고 실패 시 오류 요약을 반환합니다."""
    input_path = workdir / f"block-{index}.mmd"
    output_path = workdir / f"block-{index}.svg"
    input_path.write_text(body, encoding="utf-8")

    try:
        result = subprocess.run(  # nosec B603 - 고정 인자만 사용, shell=False.
            [
                *mmdc_command,
                "-i",
                str(input_path),
                "-o",
                str(output_path),
                "-p",
                str(workdir / "puppeteer-config.json"),
            ],
            capture_output=True,
            text=True,
            timeout=RENDER_TIMEOUT_SECONDS,
            check=False,
        )
    except subprocess.TimeoutExpired:
        return f"{RENDER_TIMEOUT_SECONDS}초 안에 렌더링을 마치지 못했습니다."

    if result.returncode != 0 or not output_path.exists():
        detail = (result.stderr or result.stdout or "출력 없음").strip()
        return detail[:500]
    return None


def main() -> int:
    npx_path = _resolve_npx()
    if npx_path is None:
        print("건너뜀: npx를 찾을 수 없어 Mermaid 렌더링 검증을 수행하지 않습니다.")
        return 0

    markdown_files = _find_files(".md")
    blocks = []
    for path in markdown_files:
        lines = path.read_text(encoding="utf-8").splitlines()
        blocks.extend(extract_mermaid_blocks(path, lines))

    if not blocks:
        print("Mermaid 렌더링 게이트 통과 — 검사할 mermaid 블록이 없습니다.")
        return 0

    errors: list[str] = []
    mmdc_command = [npx_path, "--yes", f"@mermaid-js/mermaid-cli@{MERMAID_CLI_VERSION}"]
    with tempfile.TemporaryDirectory(prefix="mermaid-render-") as raw_workdir:
        workdir = Path(raw_workdir)
        (workdir / "puppeteer-config.json").write_text(PUPPETEER_CONFIG, encoding="utf-8")

        for index, block in enumerate(blocks):
            reason = _render_block(mmdc_command, workdir, index, block.body)
            if reason:
                errors.append(f"{block.path}:{block.lineno}: 렌더링 실패 — {reason}")

    if errors:
        print(f"Mermaid 렌더링 게이트 실패 — {len(errors)}건:")
        for error in errors:
            print(f"  - {error}")
        return 1

    print(f"Mermaid 렌더링 게이트 통과 — {len(blocks)}개 블록 렌더링 확인.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
