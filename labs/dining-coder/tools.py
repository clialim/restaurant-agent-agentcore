"""dining-coder 도구 — workspace 경계 안에서만 동작하는 파일·셸 도구.

이 실습의 코딩 에이전트가 RestaurantAgent 프로젝트 스타일의 코드를 작성·실행·검증할 때
사용하는 세 가지 도구를 정의합니다. 세 도구 모두 다음 안전 원칙을 따릅니다.

- 경계는 해석된 절대 경로로 판정합니다(`resolve()` + `is_relative_to()`).
  ".." 문자열 필터로는 절대 경로·심링크 우회를 놓치기 때문입니다.
- 거부는 예외가 아니라 반환값입니다. 도구가 예외로 죽으면 에이전트는 이유를 모른 채
  같은 시도를 반복합니다. 거부 사유를 문자열로 돌려주면 스스로 경로를 고쳐 재시도합니다.
- 셸 실행은 명령 화이트리스트 + 파괴적 패턴 차단 + 타임아웃으로 이중·삼중 방어합니다.
- 읽기 전용 모드에서는 쓰기·실행 도구가 거부 문자열을 반환합니다.

환경 변수:
- DINING_CODER_READONLY=1        → write_file·run_shell을 거부(읽기 전용 모드).
- DINING_CODER_ALLOWED_COMMANDS  → 쉼표로 구분한 허용 명령 접두사 목록(기본값 대체).
"""

from __future__ import annotations

import os
import shlex
import subprocess  # nosec B404 — 샌드박스 실행 도구. 화이트리스트·타임아웃으로 방어.
from pathlib import Path

from strands import tool

WORKSPACE = (Path(__file__).parent / "workspace").resolve()
WORKSPACE.mkdir(exist_ok=True)

# 실행 상한 — 대화형 명령·서버 기동이 세션을 멈추지 않도록 강제 종료합니다.
SHELL_TIMEOUT_SECONDS = 60

# 명령의 첫 토큰이 이 목록에 있어야 실행합니다.
# RestaurantAgent 개발에 필요한 명령만 기본 허용합니다.
DEFAULT_ALLOWED_COMMANDS = (
    "python",
    "python3",
    "pytest",
    "uv",
    "ruff",
    "cat",
    "ls",
    "dir",
    "type",
    "echo",
)

# 화이트리스트를 통과하더라도 아래 패턴이 있으면 무조건 거부합니다(심층 방어).
DANGEROUS_PATTERNS = (
    "rm -rf",
    "sudo",
    ":(){",
    "mkfs",
    "dd if=",
    "del /",
    "rd /s",
    "format ",
    "> /dev/",
    "shutdown",
)


def _readonly() -> bool:
    """읽기 전용 모드 여부."""
    return os.environ.get("DINING_CODER_READONLY") == "1"


def _allowed_commands() -> tuple[str, ...]:
    """허용 명령 접두사 목록 — 환경 변수가 있으면 그 값으로 대체합니다."""
    override = os.environ.get("DINING_CODER_ALLOWED_COMMANDS")
    if override:
        return tuple(part.strip() for part in override.split(",") if part.strip())
    return DEFAULT_ALLOWED_COMMANDS


def _safe_path(path: str) -> Path | None:
    """workspace 안이면 해석된 경로, 밖이면 None.

    에이전트가 프롬프트를 따라 'workspace/' 접두사를 붙여도 workspace/workspace로
    이중 중첩되지 않도록 선행 접두사를 정규화합니다. 절대 경로·상위 경로 우회는
    resolve() + is_relative_to()로 그대로 차단됩니다.
    """
    normalized = path.replace("\\", "/").lstrip("/")
    if normalized.startswith("workspace/"):
        normalized = normalized[len("workspace/") :]
    target = (WORKSPACE / normalized).resolve()
    return target if target.is_relative_to(WORKSPACE) else None


@tool
def run_shell(command: str) -> str:
    """workspace를 작업 디렉터리로 셸 명령을 실행하고 exit code·stdout·stderr를 돌려줍니다.

    Args:
        command: 실행할 셸 명령. 첫 토큰이 허용 목록에 있어야 하며,
            파괴적 패턴이 포함되면 거부됩니다.
    """
    if _readonly():
        return "거부: 읽기 전용 모드(DINING_CODER_READONLY=1)에서는 명령을 실행하지 않습니다."

    lowered = command.lower()
    for bad in DANGEROUS_PATTERNS:
        if bad in lowered:
            return f"거부: 파괴적인 명령으로 판단했습니다 — {command}"

    try:
        tokens = shlex.split(command, posix=(os.name != "nt"))
        first_token = tokens[0]
    except (ValueError, IndexError):
        return f"거부: 명령을 해석할 수 없습니다 — {command}"

    allowed = _allowed_commands()
    if Path(first_token).name not in allowed:
        return (
            f"거부: '{first_token}'는 허용 목록에 없습니다. "
            f"허용 명령: {', '.join(allowed)}"
        )

    try:
        result = subprocess.run(  # nosec B602 — 샌드박스·화이트리스트·타임아웃으로 통제.
            command,
            shell=True,
            cwd=WORKSPACE,
            capture_output=True,
            text=True,
            timeout=SHELL_TIMEOUT_SECONDS,
        )
    except subprocess.TimeoutExpired:
        return f"중단: {SHELL_TIMEOUT_SECONDS}초 실행 상한을 넘겨 종료했습니다 — {command}"
    return f"exit={result.returncode}\nstdout:\n{result.stdout}\nstderr:\n{result.stderr}"


@tool
def read_file(path: str) -> str:
    """workspace 안의 파일을 읽습니다.

    Args:
        path: workspace 기준 상대 경로.
    """
    target = _safe_path(path)
    if target is None:
        return f"거부: {path}는 workspace 밖입니다. workspace 안 상대 경로만 허용됩니다."
    if not target.exists():
        return f"실패: {path} 파일이 존재하지 않습니다."
    return target.read_text(encoding="utf-8")


@tool
def write_file(path: str, content: str) -> str:
    """workspace 안의 파일에 내용을 씁니다.

    Args:
        path: workspace 기준 상대 경로.
        content: 파일에 쓸 내용.
    """
    if _readonly():
        return "거부: 읽기 전용 모드(DINING_CODER_READONLY=1)에서는 파일을 쓰지 않습니다."
    target = _safe_path(path)
    if target is None:
        return f"거부: {path}는 workspace 밖입니다. workspace 안 상대 경로만 허용됩니다."
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(content, encoding="utf-8")
    return f"작성 완료: {target.relative_to(WORKSPACE)} ({len(content)} bytes)"


if __name__ == "__main__":
    # 경계·거부 동작 자체 점검 (Bedrock 자격 증명 없이 실행 가능).
    print(write_file("../escape.txt", "x"))  # 거부: workspace 밖
    print(write_file("test.txt", "hello world"))  # 성공
    print(read_file("test.txt"))  # 성공
    print(read_file("../../secret.txt"))  # 거부: workspace 밖
    print(run_shell("rm -rf /"))  # 거부: 파괴적 명령
    print(run_shell("curl http://example.com"))  # 거부: 화이트리스트 밖
    print(run_shell("echo OK"))  # 성공
