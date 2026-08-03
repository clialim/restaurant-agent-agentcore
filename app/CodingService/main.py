"""AgentCore Runtime용 팀 코딩 서비스.

세션별 관리형 스토리지를 workspace로 사용하고, 선택적으로 마운트된 S3 Files에
세션별 JSONL 감사 로그를 남깁니다. LLM 도구는 workspace 안 파일 작업과 제한된
검증 명령만 허용하며 Git push·PR 생성은 신뢰된 외부 오케스트레이터가 담당합니다.
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
import re
import shlex
import subprocess  # nosec B404 - shell=False, allowlist, timeout으로 제한합니다.
import threading
from datetime import UTC, datetime
from pathlib import Path

from bedrock_agentcore import BedrockAgentCoreApp
from bedrock_agentcore.runtime import RequestContext
from strands import Agent, tool
from strands.models import BedrockModel
from strands.session import FileSessionManager

REGION = os.environ.get("AWS_REGION", "us-west-2")
DEFAULT_MODEL_ID = "us.amazon.nova-lite-v1:0"
MODEL_ID = os.environ.get("BEDROCK_MODEL_ID", DEFAULT_MODEL_ID)
WORKSPACE_ROOT = Path(os.environ.get("WORKSPACE_ROOT", "/mnt/workspace"))
PERSISTENT_ROOT = os.environ.get("PERSISTENT_ROOT")

WORK_LOG_INCLUDE_PREVIEWS = os.environ.get("WORK_LOG_INCLUDE_PREVIEWS", "false").lower() == "true"
WORK_LOG_REQUIRED = os.environ.get("WORK_LOG_REQUIRED", "false").lower() == "true"

MAX_PROMPT_CHARS = 4000
MAX_FILE_BYTES = 256 * 1024
MAX_COMMAND_OUTPUT_CHARS = 20_000
COMMAND_TIMEOUT_SECONDS = 120
RESERVED_WORKSPACE_PARTS = frozenset({".git", ".runtime-home", ".sessions", ".work-logs"})
SESSION_ID_PATTERN = re.compile(r"^[A-Za-z0-9_-]{33,100}$")
REQUEST_ID_PATTERN = re.compile(r"^[A-Za-z0-9_-]{1,64}$")
THINKING_PATTERN = re.compile(r"<thinking>.*?</thinking>\s*", re.DOTALL)
SENSITIVE_PAIR_PATTERN = re.compile(
    r"(?i)\b(token|password|secret|api[_-]?key|authorization)\s*[:=]\s*[^\s,;]+"
)
BEARER_PATTERN = re.compile(r"(?i)\bbearer\s+[A-Za-z0-9._~+/=-]+")
AWS_ACCESS_KEY_PATTERN = re.compile(r"\b(?:AKIA|ASIA)[A-Z0-9]{16}\b")
URL_CREDENTIAL_PATTERN = re.compile(r"(?i)(https?://)[^/\s:@]+:[^/\s@]+@")
PRIVATE_KEY_PATTERN = re.compile(
    r"-----BEGIN [A-Z ]*PRIVATE KEY-----.*?-----END [A-Z ]*PRIVATE KEY-----",
    re.DOTALL,
)

_LOG_LOCK = threading.Lock()
logger = logging.getLogger(__name__)

SYSTEM_PROMPT = """당신은 RestaurantAgent 팀의 코딩 서비스입니다.

## 신뢰 경계
- 모든 파일은 workspace 기준 상대 경로로만 읽고 씁니다.
- 실제 Git push, 브랜치 생성, PR 생성은 외부의 신뢰된 오케스트레이터가 수행합니다.
- 자격증명, 환경 변수, 절대 경로를 읽거나 출력하지 않습니다.
- 사용자 입력을 명령 문자열로 직접 연결하지 않습니다.

## 작업 방식
1. 기존 파일을 먼저 읽고 요청 범위를 확인합니다.
2. 입력 검증은 fail-closed로 구현하고 사용자 메시지는 존댓말 한국어로 작성합니다.
3. 코드를 작성한 뒤 run_checks로 Python 또는 pytest 검증을 실행합니다.
4. 실패하면 오류 원문을 근거로 수정하고 다시 검증합니다.
5. 완료 응답에는 변경 파일, 검증 명령, 검증 결과를 간결히 보고합니다.
"""


def _workspace() -> Path:
    """호출 시점에 마운트된 세션 workspace를 준비합니다."""
    WORKSPACE_ROOT.mkdir(parents=True, exist_ok=True)
    return WORKSPACE_ROOT.resolve()


def _safe_path(path: str) -> Path | None:
    """workspace 내부 상대 경로만 해석합니다."""
    if not isinstance(path, str) or not path.strip():
        return None
    relative = Path(path)
    if (
        relative.is_absolute()
        or ".." in relative.parts
        or RESERVED_WORKSPACE_PARTS.intersection(relative.parts)
    ):
        return None
    root = _workspace()
    target = (root / relative).resolve()
    return target if target.is_relative_to(root) else None


def _safe_command_path(token: str) -> bool:
    """명령 인자에 절대 경로나 상위 경로 이동이 없는지 확인합니다."""
    if not token or "\x00" in token or ".." in token:
        return False
    if token.startswith("-"):
        return not re.search(r"=(?:/|\\|[A-Za-z]:)", token)
    candidate = Path(token)
    return not candidate.is_absolute()


def _validate_command(tokens: list[str]) -> str | None:
    """검증 전용 명령 형태만 허용하고 거부 사유를 반환합니다."""
    if not tokens:
        return "명령이 비어 있습니다."
    executable = tokens[0]
    args = tokens[1:]
    if executable not in {"python", "python3", "pytest", "ruff"}:
        return f"'{executable}' 명령은 허용되지 않습니다."
    if not all(_safe_command_path(token) for token in args):
        return "절대 경로와 상위 경로 이동은 허용되지 않습니다."

    if executable == "pytest":
        return None
    if executable == "ruff" and args and args[0] in {"check", "format"}:
        return None
    if executable in {"python", "python3"} and len(args) >= 2:
        if args[:2] == ["-m", "pytest"] or args[:2] == ["-m", "compileall"]:
            return None
    return "Python은 '-m pytest' 또는 '-m compileall', Ruff는 check/format만 허용됩니다."


@tool
def list_files(path: str = ".") -> str:
    """workspace 내부 파일을 최대 200개까지 나열합니다.

    Args:
        path: workspace 기준 디렉터리 상대 경로.
    """
    target = _safe_path(path)
    if target is None or not target.is_dir():
        return "거부: workspace 내부의 존재하는 디렉터리만 조회할 수 있습니다."
    root = _workspace()
    files = []
    for item in target.rglob("*"):
        if not item.is_file():
            continue
        relative = item.relative_to(root)
        if not RESERVED_WORKSPACE_PARTS.intersection(relative.parts):
            files.append(relative.as_posix())
    files.sort()
    shown = files[:200]
    suffix = "\n... 파일이 200개를 넘어 나머지는 생략했습니다." if len(files) > 200 else ""
    return "\n".join(shown) + suffix if shown else "파일이 없습니다."


@tool
def read_file(path: str) -> str:
    """workspace 내부 UTF-8 텍스트 파일을 읽습니다.

    Args:
        path: workspace 기준 파일 상대 경로.
    """
    target = _safe_path(path)
    if target is None or not target.is_file():
        return "거부: workspace 내부의 존재하는 파일만 읽을 수 있습니다."
    if target.stat().st_size > MAX_FILE_BYTES:
        return f"거부: 파일 크기는 {MAX_FILE_BYTES}바이트 이하여야 합니다."
    try:
        return target.read_text(encoding="utf-8")
    except UnicodeDecodeError:
        return "거부: UTF-8 텍스트 파일만 읽을 수 있습니다."


@tool
def write_file(path: str, content: str) -> str:
    """workspace 내부 파일에 UTF-8 텍스트를 씁니다.

    Args:
        path: workspace 기준 파일 상대 경로.
        content: 저장할 UTF-8 텍스트.
    """
    if not isinstance(content, str):
        return "거부: content는 문자열이어야 합니다."
    encoded = content.encode("utf-8")
    if len(encoded) > MAX_FILE_BYTES:
        return f"거부: 파일 크기는 {MAX_FILE_BYTES}바이트 이하여야 합니다."
    target = _safe_path(path)
    if target is None:
        return "거부: workspace 내부 상대 경로만 쓸 수 있습니다."
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(content, encoding="utf-8")
    return f"저장 완료: {target.relative_to(_workspace()).as_posix()} ({len(encoded)} bytes)"


@tool
def run_checks(command: str) -> str:
    """workspace에서 허용된 Python·pytest·Ruff 검증 명령을 실행합니다.

    Args:
        command: 셸 연산자 없는 단일 검증 명령.
    """
    if not isinstance(command, str) or len(command) > 1000:
        return "거부: 명령은 1,000자 이하 문자열이어야 합니다."
    try:
        tokens = shlex.split(command, posix=True)
    except ValueError:
        return "거부: 명령 형식을 해석할 수 없습니다."
    reason = _validate_command(tokens)
    if reason:
        return f"거부: {reason}"

    runtime_home = _workspace() / ".runtime-home"
    runtime_home.mkdir(exist_ok=True)
    safe_env = {
        "AWS_EC2_METADATA_DISABLED": "true",
        "HOME": str(runtime_home),
        "LANG": "C.UTF-8",
        "PATH": os.environ.get("PATH", ""),
        "PYTHONUNBUFFERED": "1",
    }
    try:
        result = subprocess.run(  # nosec B603 - 검증된 토큰을 shell=False로 실행합니다.
            tokens,
            cwd=_workspace(),
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            env=safe_env,
            timeout=COMMAND_TIMEOUT_SECONDS,
            check=False,
        )
    except subprocess.TimeoutExpired:
        return f"중단: {COMMAND_TIMEOUT_SECONDS}초 실행 상한을 넘었습니다."
    output = f"exit={result.returncode}\nstdout:\n{result.stdout}\nstderr:\n{result.stderr}"
    return output[:MAX_COMMAND_OUTPUT_CHARS]


def _validate_payload(payload: object, runtime_session_id: str | None) -> tuple[str, str, str]:
    """Runtime payload를 검증하고 헤더 session id를 단일 권위로 사용합니다."""
    if not isinstance(payload, dict):
        raise ValueError("요청 형식이 올바르지 않습니다.")
    prompt = payload.get("prompt")
    payload_session_id = payload.get("sessionId")
    request_id = payload.get("requestId")
    if not isinstance(prompt, str) or not prompt.strip():
        raise ValueError("prompt는 비어 있지 않은 문자열이어야 합니다.")
    prompt = prompt.strip()
    if len(prompt) > MAX_PROMPT_CHARS:
        raise ValueError(f"prompt는 최대 {MAX_PROMPT_CHARS}자까지 허용됩니다.")
    if not isinstance(runtime_session_id, str) or not SESSION_ID_PATTERN.fullmatch(
        runtime_session_id
    ):
        raise ValueError("Runtime sessionId 형식이 올바르지 않습니다.")
    if payload_session_id != runtime_session_id:
        raise ValueError("payload sessionId가 Runtime sessionId와 일치하지 않습니다.")
    if not isinstance(request_id, str) or not REQUEST_ID_PATTERN.fullmatch(request_id):
        raise ValueError("requestId 형식이 올바르지 않습니다.")
    return prompt, runtime_session_id, request_id


def _redact(text: str) -> str:
    """로그 미리보기에서 일반적인 자격증명 형태를 제거합니다."""
    redacted = PRIVATE_KEY_PATTERN.sub("[REDACTED_PRIVATE_KEY]", text)
    redacted = URL_CREDENTIAL_PATTERN.sub(r"\1[REDACTED]@", redacted)
    redacted = BEARER_PATTERN.sub("Bearer [REDACTED]", redacted)
    redacted = AWS_ACCESS_KEY_PATTERN.sub("[REDACTED_AWS_ACCESS_KEY]", redacted)
    return SENSITIVE_PAIR_PATTERN.sub(r"\1=[REDACTED]", redacted)


def _log_root() -> Path:
    """S3 Files 마운트가 있으면 사용하고, 없으면 세션 스토리지로 폴백합니다."""
    if PERSISTENT_ROOT:
        configured = Path(PERSISTENT_ROOT)
        if configured.exists() and configured.is_dir():
            return configured
    root = _workspace() / ".work-logs"
    root.mkdir(parents=True, exist_ok=True)
    return root


def _append_work_log(
    *,
    session_id: str,
    request_id: str,
    prompt: str,
    status: str,
    result: str,
) -> None:
    """동시 쓰기 충돌을 피하도록 세션별 JSONL 작업 로그를 추가합니다."""
    record: dict[str, str | int] = {
        "timestamp": datetime.now(UTC).isoformat(),
        "sessionId": session_id,
        "requestId": request_id,
        "promptSha256": hashlib.sha256(prompt.encode("utf-8")).hexdigest(),
        "promptChars": len(prompt),
        "status": status,
        "resultSha256": hashlib.sha256(result.encode("utf-8")).hexdigest(),
        "resultChars": len(result),
    }
    if WORK_LOG_INCLUDE_PREVIEWS:
        record["promptPreview"] = _redact(prompt)[:300]
        record["resultPreview"] = _redact(result)[:500]
    log_path = _log_root() / f"{session_id}.jsonl"
    with _LOG_LOCK, log_path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(record, ensure_ascii=False) + "\n")


def _build_agent(session_id: str) -> Agent:
    """세션 스토리지에 대화 이력을 보존하는 코딩 Agent를 생성합니다."""
    session_manager = FileSessionManager(
        session_id=session_id,
        storage_dir=str(_workspace() / ".sessions"),
    )
    return Agent(
        model=BedrockModel(
            model_id=MODEL_ID,
            region_name=REGION,
            temperature=0.0,
        ),
        system_prompt=SYSTEM_PROMPT,
        tools=[list_files, read_file, write_file, run_checks],
        session_manager=session_manager,
        callback_handler=None,
    )


def _final_answer(result: object) -> str:
    """모델 내부 추론 태그를 제거한 최종 응답을 반환합니다."""
    return THINKING_PATTERN.sub("", str(result)).strip()


app = BedrockAgentCoreApp()


@app.entrypoint
def invoke(payload: object, context: RequestContext) -> dict[str, str]:
    """코딩 요청을 처리하고 세션별 작업 로그를 남깁니다."""
    try:
        prompt, session_id, request_id = _validate_payload(payload, context.session_id)
    except ValueError as exc:
        return {"status": "REJECTED", "result": str(exc)}

    try:
        result = _final_answer(_build_agent(session_id)(prompt))
        status = "COMPLETED"
    except Exception:  # noqa: BLE001 - 내부 예외 상세와 자격증명을 응답에 노출하지 않습니다.
        result = "코딩 요청 처리 중 내부 오류가 발생했습니다."
        status = "FAILED"

    try:
        _append_work_log(
            session_id=session_id,
            request_id=request_id,
            prompt=prompt,
            status=status,
            result=result,
        )
    except OSError:
        logger.exception(
            "CodingService 작업 로그 기록 실패",
            extra={"session_id": session_id, "request_id": request_id},
        )
        if WORK_LOG_REQUIRED:
            status = "FAILED"
            result = "코딩 작업은 끝났지만 필수 작업 로그를 기록하지 못했습니다."
    return {"status": status, "result": result, "requestId": request_id}


if __name__ == "__main__":
    app.run()
