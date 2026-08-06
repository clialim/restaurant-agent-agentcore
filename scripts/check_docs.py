#!/usr/bin/env python
"""문서 품질 게이트 — Markdown 링크, Mermaid 블록, SVG를 결정적으로 검증합니다.

세 가지를 검사하고 하나라도 실패하면 종료 코드 1을 반환합니다.

1. 로컬 링크: `[텍스트](경로)` / `![대체텍스트](경로)`에서 http(s)·mailto·앵커가
   아닌 상대 경로가 실제로 존재하는지 확인합니다.
2. Markdown 코드 fence(````) 개수가 짝수인지, 그리고 각 ```mermaid 블록이
   알려진 다이어그램 종류로 시작하고 소괄호·중괄호·대괄호가 균형을 이루는지
   가벼운 구조 검사를 합니다. 전체 Mermaid 문법 검사기는 두지 않으므로
   렌더링 자체를 보장하지는 않습니다.
3. 추적 중인 `.svg` 파일이 유효한 XML로 파싱되는지 확인합니다.

`.venv`·`node_modules`·`cdk.out` 등 생성물 디렉터리는 자동으로 제외합니다.
git 메타데이터 없이도 동작하므로 CodeBuild(소스 ZIP)에서도 그대로 쓸 수
있습니다.

    uv run python scripts/check_docs.py
"""

from __future__ import annotations

import re
import sys
import xml.etree.ElementTree as ET  # nosec B405 - 저장소 내부 신뢰 SVG만 파싱합니다.
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

# CodeBuild는 소스를 ZIP 아티팩트로 받을 수 있어 `.git` 메타데이터가 없습니다.
# `git ls-files` 대신 파일시스템을 직접 순회해 어디서든 동작하게 합니다.
EXCLUDED_DIR_NAMES = frozenset(
    {
        ".git",
        ".venv",
        "node_modules",
        "cdk.out",
        "dist",
        ".cache",
        ".ruff_cache",
        ".pytest_cache",
    }
)

LINK_PATTERN = re.compile(r"!?\[[^\]]*\]\(([^)\s]+)(?:\s+\"[^\"]*\")?\)")
EXTERNAL_LINK_PATTERN = re.compile(r"^(?:https?:|mailto:|#)", re.IGNORECASE)
FENCE_PATTERN = re.compile(r"^```")
MERMAID_FENCE_PATTERN = re.compile(r"^```mermaid\s*$")
FENCE_END_PATTERN = re.compile(r"^```\s*$")

KNOWN_DIAGRAM_KEYWORDS = (
    "flowchart",
    "graph",
    "sequenceDiagram",
    "classDiagram",
    "stateDiagram",
    "stateDiagram-v2",
    "erDiagram",
    "gantt",
    "pie",
    "journey",
    "gitGraph",
    "mindmap",
    "timeline",
    "quadrantChart",
    "requirementDiagram",
    "C4Context",
    "sankey-beta",
    "block-beta",
)


def _is_excluded(path: Path) -> bool:
    """생성물·의존성 디렉터리 하위 경로인지 확인합니다."""
    return any(part in EXCLUDED_DIR_NAMES for part in path.relative_to(ROOT).parts[:-1])


def _find_files(suffix: str) -> list[Path]:
    """생성물·의존성 디렉터리를 제외하고 저장소 파일을 직접 순회합니다.

    CodeBuild가 소스를 ZIP 아티팩트로 받으면 `.git` 메타데이터가 없어
    `git ls-files`가 실패하므로, 파일시스템 순회로 git 존재 여부와
    무관하게 동작하게 합니다.
    """
    return sorted(
        path for path in ROOT.rglob(f"*{suffix}") if path.is_file() and not _is_excluded(path)
    )


def _bracket_balance(text: str) -> bool:
    """소괄호·중괄호·대괄호가 서로 교차하지 않고 균형을 이루는지 확인합니다."""
    pairs = {")": "(", "]": "[", "}": "{"}
    stack: list[str] = []
    for char in text:
        if char in "([{":
            stack.append(char)
        elif char in ")]}":
            if not stack or stack.pop() != pairs[char]:
                return False
    return not stack


def _check_mermaid_block(path: Path, lineno: int, body_lines: list[str]) -> list[str]:
    """단일 Mermaid 블록의 가벼운 구조적 유효성을 검사하고 오류 목록을 반환합니다."""
    errors: list[str] = []
    non_empty = [line for line in body_lines if line.strip()]
    if not non_empty:
        errors.append(f"{path}:{lineno}: mermaid 블록이 비어 있습니다.")
        return errors

    first_token = non_empty[0].strip().split()[0]
    if not any(first_token.startswith(keyword) for keyword in KNOWN_DIAGRAM_KEYWORDS):
        errors.append(
            f"{path}:{lineno}: 알려진 다이어그램 종류로 시작하지 않습니다 "
            f"(첫 토큰: '{first_token}')."
        )

    body = "\n".join(body_lines)
    if not _bracket_balance(body):
        errors.append(f"{path}:{lineno}: 괄호가 균형을 이루지 않습니다 (( ) [ ] {{ }}).")

    return errors


def check_markdown_file(path: Path) -> list[str]:
    """단일 Markdown 파일의 fence·Mermaid·로컬 링크를 검사합니다."""
    errors: list[str] = []
    text = path.read_text(encoding="utf-8")
    lines = text.splitlines()

    fence_count = sum(1 for line in lines if FENCE_PATTERN.match(line))
    if fence_count % 2 != 0:
        errors.append(f"{path}: 코드 fence(```) 개수가 홀수입니다 ({fence_count}개).")

    index = 0
    while index < len(lines):
        if MERMAID_FENCE_PATTERN.match(lines[index]):
            start = index
            body_lines: list[str] = []
            index += 1
            closed = False
            while index < len(lines):
                if FENCE_END_PATTERN.match(lines[index]):
                    closed = True
                    break
                body_lines.append(lines[index])
                index += 1
            if not closed:
                errors.append(f"{path}:{start + 1}: mermaid 블록이 닫히지 않았습니다.")
                break
            errors.extend(_check_mermaid_block(path, start + 1, body_lines))
        index += 1

    for match in LINK_PATTERN.finditer(text):
        target = match.group(1)
        if EXTERNAL_LINK_PATTERN.match(target):
            continue
        local_target = target.split("#", 1)[0]
        if not local_target:
            continue
        resolved = (path.parent / local_target).resolve()
        if not resolved.exists():
            errors.append(f"{path}: 로컬 링크가 존재하지 않습니다: {target}")

    return errors


def check_svg_file(path: Path) -> list[str]:
    """SVG 파일이 유효한 XML로 파싱되는지 확인합니다."""
    try:
        ET.parse(path)  # nosec B314 - 저장소 내부 신뢰 파일만 파싱합니다.
    except ET.ParseError as exc:
        return [f"{path}: 유효한 XML이 아닙니다 ({exc})."]
    return []


def main() -> int:
    errors: list[str] = []

    markdown_files = _find_files(".md")
    for path in markdown_files:
        errors.extend(check_markdown_file(path))

    svg_files = _find_files(".svg")
    for path in svg_files:
        errors.extend(check_svg_file(path))

    if errors:
        print(f"문서 품질 게이트 실패 — {len(errors)}건:")
        for error in errors:
            print(f"  - {error}")
        return 1

    print(
        f"문서 품질 게이트 통과 — Markdown {len(markdown_files)}개, SVG {len(svg_files)}개 검사 완료."
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
