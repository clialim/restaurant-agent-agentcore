"""Runtime이 만든 patch를 로컬 신뢰 경계에서 적용해 GitHub PR로 게시합니다.

Runtime에는 GitHub 토큰을 전달하지 않습니다. --publish를 지정한 경우에만 로컬 git/gh
자격증명으로 branch push와 PR 생성을 수행합니다.
"""

from __future__ import annotations

import argparse
import os
import re
import shlex
import subprocess  # nosec B404 - 고정 실행 파일과 리스트 인자를 사용합니다.
import tempfile
from pathlib import Path, PurePosixPath
from urllib.parse import urlparse

from runtime_client import CodingRuntimeClient, print_stream

GITHUB_REPO_PATTERN = re.compile(r"^[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+$")
GIT_REF_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._/-]{0,99}$")
BRANCH_PATTERN = re.compile(r"^(?:fix|feature)/[A-Za-z0-9._-]{1,80}$")
COMMIT_PATTERN = re.compile(r"^(?:[0-9a-f]{40}|[0-9a-f]{64})$")
MAX_CHANGED_FILES = 100
MAX_PATCH_BYTES = 1024 * 1024
SENSITIVE_PATH_PREFIXES = (".circleci/", ".github/actions/", ".github/workflows/")
SENSITIVE_FILE_NAMES = {
    ".gitlab-ci.yml",
    ".gitmodules",
    "buildspec.yaml",
    "buildspec.yml",
    "codeowners",
}


def _repo_slug(repo_url: str) -> str:
    parsed = urlparse(repo_url)
    if (
        parsed.scheme != "https"
        or parsed.netloc != "github.com"
        or parsed.params
        or parsed.query
        or parsed.fragment
    ):
        raise ValueError("쿼리·프래그먼트가 없는 HTTPS GitHub 저장소 URL만 허용됩니다.")
    slug = parsed.path.removesuffix(".git").strip("/")
    if not GITHUB_REPO_PATTERN.fullmatch(slug):
        raise ValueError("GitHub 저장소 경로 형식이 올바르지 않습니다.")
    return slug


def _validate_changed_paths(raw_paths: str) -> list[str]:
    """자동 게시에서 CI·공급망 권한을 바꿀 수 있는 경로를 기본 거부합니다."""
    paths = [path for path in raw_paths.split("\0") if path]
    if not paths:
        raise ValueError("게시할 변경 파일이 없습니다.")
    if len(paths) > MAX_CHANGED_FILES:
        raise ValueError(f"변경 파일은 최대 {MAX_CHANGED_FILES}개까지 허용됩니다.")

    for path in paths:
        candidate = PurePosixPath(path)
        lower_path = path.lower()
        lower_name = candidate.name.lower()
        if candidate.is_absolute() or ".." in candidate.parts or "\\" in path:
            raise ValueError(f"안전하지 않은 변경 경로입니다: {path}")
        if lower_path.startswith(SENSITIVE_PATH_PREFIXES):
            raise ValueError(f"자동 게시가 금지된 CI 경로입니다: {path}")
        if lower_name in SENSITIVE_FILE_NAMES or lower_name.startswith("dockerfile"):
            raise ValueError(f"자동 게시가 금지된 공급망 파일입니다: {path}")
    return paths


def _run_local(args: list[str], cwd: Path | None = None) -> str:
    result = subprocess.run(  # nosec B603 - shell 없이 검증된 인자만 실행합니다.
        args,
        cwd=cwd,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        check=False,
    )
    if result.returncode != 0:
        raise RuntimeError(result.stderr.strip() or f"명령 실패: {args[0]}")
    return result.stdout.strip()


def _publish_patch(
    *,
    repo_url: str,
    repo_slug: str,
    base: str,
    base_commit: str,
    branch: str,
    issue: int,
    patch_path: Path,
) -> str:
    if not BRANCH_PATTERN.fullmatch(branch):
        raise ValueError("브랜치는 fix/* 또는 feature/* 형식이어야 합니다.")
    if not COMMIT_PATTERN.fullmatch(base_commit):
        raise ValueError("Runtime base commit 형식이 올바르지 않습니다.")

    with tempfile.TemporaryDirectory(prefix="coding-service-pr-") as temp:
        repo_dir = Path(temp) / "repo"
        _run_local(["git", "clone", "--depth", "1", "--branch", base, repo_url, str(repo_dir)])
        local_commit = _run_local(["git", "rev-parse", "HEAD"], cwd=repo_dir)
        if local_commit != base_commit:
            raise RuntimeError("Runtime 검증 후 base branch가 변경되어 게시를 중단했습니다.")
        _run_local(["git", "checkout", "-b", branch], cwd=repo_dir)
        _run_local(["git", "apply", "--index", str(patch_path.resolve())], cwd=repo_dir)
        _run_local(["git", "diff", "--cached", "--check"], cwd=repo_dir)
        _run_local(["git", "commit", "-m", f"fix: 이슈 #{issue} 자동 수정"], cwd=repo_dir)
        _run_local(["git", "push", "-u", "origin", branch], cwd=repo_dir)
        return _run_local(
            [
                "gh",
                "pr",
                "create",
                "--repo",
                repo_slug,
                "--base",
                base,
                "--head",
                branch,
                "--title",
                f"fix: 이슈 #{issue} 자동 수정",
                "--body",
                "AgentCore CodingService가 생성한 patch를 로컬 신뢰 경계에서 검증해 게시했습니다.",
            ],
            cwd=repo_dir,
        )


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--runtime-arn", default=os.environ.get("CODING_RUNTIME_ARN"))
    parser.add_argument("--repo-url", required=True)
    parser.add_argument("--issue", required=True, type=int)
    parser.add_argument("--requirement", required=True)
    parser.add_argument("--base", default="main")
    parser.add_argument("--branch")
    parser.add_argument("--publish", action="store_true")
    args = parser.parse_args()
    if not args.runtime_arn:
        parser.error("--runtime-arn 또는 CODING_RUNTIME_ARN이 필요합니다.")
    if args.issue < 1:
        parser.error("--issue는 1 이상의 정수여야 합니다.")
    if not GIT_REF_PATTERN.fullmatch(args.base) or ".." in args.base:
        parser.error("--base는 안전한 Git ref 형식이어야 합니다.")

    repo_slug = _repo_slug(args.repo_url)
    branch = args.branch or f"fix/issue-{args.issue}-agentcore"
    if not BRANCH_PATTERN.fullmatch(branch):
        parser.error("--branch는 fix/* 또는 feature/* 형식이어야 합니다.")

    client = CodingRuntimeClient(args.runtime_arn)
    session_id = client.new_session_id(f"issue-{args.issue}")
    repo_dir = f"repo-{args.issue}"
    quoted_url = shlex.quote(args.repo_url)
    clone = client.run_command(
        command=(
            f"git clone --depth 1 --branch {shlex.quote(args.base)} "
            f"{quoted_url} /mnt/workspace/{repo_dir}"
        ),
        session_id=session_id,
        timeout=300,
        on_output=print_stream,
    )
    if clone.exit_code != 0:
        raise RuntimeError("Runtime git clone에 실패했습니다.")

    commit = client.run_command(
        command=f"git -C /mnt/workspace/{repo_dir} rev-parse HEAD",
        session_id=session_id,
        timeout=60,
    )
    base_commit = commit.stdout.strip()
    if commit.exit_code != 0 or not COMMIT_PATTERN.fullmatch(base_commit):
        raise RuntimeError("Runtime base commit을 확인하지 못했습니다.")

    response = client.invoke(
        prompt=(
            f"GitHub 이슈 #{args.issue} 요구사항을 해결해 주세요.\n"
            f"요구사항: {args.requirement}\n"
            f"대상 저장소는 {repo_dir}이며 파일을 수정하고 pytest로 검증해 주세요."
        ),
        session_id=session_id,
    )
    if response.get("status") != "COMPLETED":
        raise RuntimeError(f"Agent 작업 실패: {response.get('result', '')}")

    tests = client.run_command(
        command=(
            "mkdir -p /mnt/workspace/.runtime-home && "
            f"cd /mnt/workspace/{repo_dir} && "
            "env -i AWS_EC2_METADATA_DISABLED=true "
            "HOME=/mnt/workspace/.runtime-home LANG=C.UTF-8 "
            "PATH=/app/.venv/bin:/usr/local/bin:/usr/bin:/bin "
            "PYTHONDONTWRITEBYTECODE=1 PYTHONNOUSERSITE=1 python -m pytest -q"
        ),
        session_id=session_id,
        timeout=600,
        on_output=print_stream,
    )
    if tests.exit_code != 0:
        raise RuntimeError("Runtime 테스트가 실패해 PR 생성을 중단했습니다.")

    intent_to_add = client.run_command(
        command=f"git -C /mnt/workspace/{repo_dir} add -N .",
        session_id=session_id,
        timeout=60,
    )
    if intent_to_add.exit_code != 0:
        raise RuntimeError("새 파일을 patch 대상으로 등록하지 못했습니다.")

    changed = client.run_command(
        command=f"git -C /mnt/workspace/{repo_dir} diff --name-only -z --no-ext-diff --",
        session_id=session_id,
        timeout=60,
    )
    if changed.exit_code != 0:
        raise RuntimeError("변경 파일 목록을 확인하지 못했습니다.")
    changed_paths = _validate_changed_paths(changed.stdout)

    patch = client.run_command(
        command=f"git -C /mnt/workspace/{repo_dir} diff --binary --no-ext-diff --",
        session_id=session_id,
        timeout=120,
    )
    patch_bytes = patch.stdout.encode("utf-8")
    if patch.exit_code != 0 or not patch_bytes:
        raise RuntimeError("게시할 Git diff가 없습니다.")
    if len(patch_bytes) > MAX_PATCH_BYTES:
        raise RuntimeError(f"patch는 최대 {MAX_PATCH_BYTES}바이트까지 허용됩니다.")

    artifact_dir = Path(__file__).parent / "artifacts"
    artifact_dir.mkdir(exist_ok=True)
    patch_path = artifact_dir / f"issue-{args.issue}.patch"
    patch_path.write_bytes(patch_bytes)
    print(f"patch 저장: {patch_path} ({len(changed_paths)}개 파일)")

    if args.publish:
        pr_url = _publish_patch(
            repo_url=args.repo_url,
            repo_slug=repo_slug,
            base=args.base,
            base_commit=base_commit,
            branch=branch,
            issue=args.issue,
            patch_path=patch_path,
        )
        print(f"PR 생성: {pr_url}")
    else:
        print("--publish를 지정하지 않아 원격 push와 PR 생성은 수행하지 않았습니다.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
