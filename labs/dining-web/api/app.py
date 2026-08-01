"""dining-web Lambda 핸들러 — HTTP API(payload 2.0) 2라우트.

- GET  /health : 상태 확인(shallow health check).
- POST /ask    : {"prompt": "...", "sessionId": "..."}를 받아 배포된 AgentCore Runtime을
                 invoke_agent_runtime으로 호출하고 {"answer": "...", "sessionId": "..."}로 반환.

멀티턴 세션:
- 클라이언트가 sessionId를 보내면 같은 대화로 이어갑니다.
- sessionId가 없으면 서버에서 새로 생성합니다.
- AgentCore runtimeSessionId 요구사항: 33자 이상, [a-zA-Z0-9_-] 포함.

보안 관점:
- 인증 없는 PUBLIC 진입점이므로 payload에서 신뢰 경계가 시작합니다.
  prompt의 존재·타입·길이를 모델 호출 전에 검증(fail-closed)합니다.
- sessionId는 형식·길이만 검증하고 서버 상태를 저장하지 않습니다.
- 사용자 입력 원문은 로그로 남기지 않습니다.
"""

import base64
import json
import os
import re
import uuid

import boto3

REGION = os.environ.get("AWS_REGION", "us-west-2")
AGENT_RUNTIME_ARN = os.environ["AGENT_RUNTIME_ARN"]
QUALIFIER = "DEFAULT"

# 과대 페이로드로 인한 비용/지연/남용을 방지합니다.
MAX_PROMPT_CHARS = 4000
MIN_PROMPT_CHARS = 1

# sessionId 검증: AgentCore runtimeSessionId는 33~100자, 알파벳·숫자·하이픈·언더스코어.
SESSION_ID_PATTERN = re.compile(r"^[a-zA-Z0-9_-]{33,100}$")
SESSION_ID_PREFIX = "dining-web-"

# 데이터 평면 클라이언트는 콜드 스타트 1회만 생성해 재사용합니다.
_agent_client = boto3.client("bedrock-agentcore", region_name=REGION)


def _json_response(status: int, body: dict) -> dict:
    return {
        "statusCode": status,
        "headers": {"Content-Type": "application/json"},
        "body": json.dumps(body, ensure_ascii=False),
    }


# ---------------------------------------------------------------------------
# 응답 파싱 — 스트리밍(SSE) / 단일 JSON / 평문 모두 견고하게 처리
# ---------------------------------------------------------------------------

_TEXT_KEYS = ("result", "answer", "data", "output", "text", "content", "message")


def _coerce_chunk(chunk: str) -> str:
    """조각 하나에서 사람이 읽을 텍스트를 추출합니다.

    JSON이면 알려진 텍스트 키를 우선 꺼내고, 아니면 원문을 그대로 씁니다.
    """
    try:
        obj = json.loads(chunk)
    except (ValueError, TypeError):
        return chunk
    if isinstance(obj, str):
        return obj
    if isinstance(obj, dict):
        for key in _TEXT_KEYS:
            value = obj.get(key)
            if isinstance(value, str):
                return value
        return json.dumps(obj, ensure_ascii=False)
    return str(obj)


def _extract_answer(raw: str) -> str:
    """invoke 응답 본문에서 최종 텍스트를 조립합니다.

    엔트리포인트가 스트리밍이면 SSE 프레이밍(`data:` 줄)이 섞일 수 있어
    줄 단위로 안전하게 파싱하고, 아니면 전체를 단일 페이로드로 처리합니다.
    """
    raw = raw.strip()
    if not raw:
        return ""

    lines = raw.splitlines()
    if any(line.lstrip().startswith("data:") for line in lines):
        parts = []
        for line in lines:
            stripped = line.lstrip()
            if not stripped.startswith("data:"):
                continue
            # SSE 규격: 콜론 뒤 선행 공백 1개만 제거하고 내부 공백은 보존합니다
            # (스트리밍 조각 경계의 공백이 유의미하기 때문).
            payload = stripped[len("data:"):]
            if payload.startswith(" "):
                payload = payload[1:]
            if not payload or payload == "[DONE]":
                continue
            parts.append(_coerce_chunk(payload))
        if parts:
            return "".join(parts).strip()

    return _coerce_chunk(raw).strip()


# ---------------------------------------------------------------------------
# 라우트 핸들러
# ---------------------------------------------------------------------------


def _generate_session_id() -> str:
    """AgentCore runtimeSessionId 요구사항(33자 이상)을 만족하는 새 세션 ID를 생성합니다."""
    return f"{SESSION_ID_PREFIX}{uuid.uuid4().hex}"


def _validate_session_id(session_id: str | None) -> str:
    """클라이언트 제공 sessionId를 검증하거나 새로 생성합니다."""
    if not session_id:
        return _generate_session_id()
    if not isinstance(session_id, str):
        raise ValueError("sessionId는 문자열이어야 합니다.")
    if not SESSION_ID_PATTERN.match(session_id):
        raise ValueError("sessionId 형식이 올바르지 않습니다. 33~100자의 영문·숫자·하이픈·언더스코어만 허용됩니다.")
    return session_id


def _parse_request(event: dict) -> tuple[str, str]:
    """요청 본문에서 prompt와 sessionId를 꺼내 검증합니다(fail-closed).

    Returns:
        (prompt, session_id) 튜플
    """
    body = event.get("body") or ""
    if event.get("isBase64Encoded"):
        body = base64.b64decode(body).decode("utf-8")
    try:
        parsed = json.loads(body) if body else {}
    except (ValueError, TypeError) as exc:
        raise ValueError("요청 본문이 올바른 JSON이 아닙니다.") from exc
    if not isinstance(parsed, dict):
        raise ValueError("요청 형식이 올바르지 않습니다.")

    prompt = parsed.get("prompt")
    if not isinstance(prompt, str):
        raise ValueError("prompt는 문자열이어야 합니다.")
    prompt = prompt.strip()
    if len(prompt) < MIN_PROMPT_CHARS:
        raise ValueError("prompt가 비어 있습니다.")
    if len(prompt) > MAX_PROMPT_CHARS:
        raise ValueError(f"prompt가 너무 깁니다. 최대 {MAX_PROMPT_CHARS}자까지 허용됩니다.")

    session_id = _validate_session_id(parsed.get("sessionId"))
    return prompt, session_id


def handle_ask(event: dict) -> dict:
    try:
        prompt, session_id = _parse_request(event)
    except ValueError as exc:
        return _json_response(400, {"error": str(exc)})

    try:
        response = _agent_client.invoke_agent_runtime(
            agentRuntimeArn=AGENT_RUNTIME_ARN,
            qualifier=QUALIFIER,
            runtimeSessionId=session_id,
            payload=json.dumps({"prompt": prompt}).encode("utf-8"),
        )
        raw = response["response"].read().decode("utf-8")
    except Exception:  # noqa: BLE001 — 내부 오류 상세는 사용자에게 노출하지 않습니다.
        return _json_response(502, {"error": "에이전트 호출에 실패했습니다."})

    answer = _extract_answer(raw)
    return _json_response(200, {"answer": answer, "sessionId": session_id})


def lambda_handler(event, _context):
    route_key = event.get("routeKey", "")

    if route_key == "GET /health":
        return _json_response(200, {"status": "ok"})
    if route_key == "POST /ask":
        return handle_ask(event)

    return _json_response(404, {"error": f"지원하지 않는 경로입니다: {route_key}"})
