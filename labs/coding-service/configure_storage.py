"""S3 Files 스택 출력을 agentcore.json의 CodingService Runtime에 반영합니다."""

from __future__ import annotations

import argparse
import json
import os
import re
import tempfile
from pathlib import Path

import boto3

ROOT = Path(__file__).resolve().parents[2]
DEFAULT_CONFIG = ROOT / "agentcore" / "agentcore.json"
READY_STACK_STATUSES = {"CREATE_COMPLETE", "IMPORT_COMPLETE", "UPDATE_COMPLETE"}


def _stack_outputs(stack_name: str, region: str) -> dict[str, str]:
    client = boto3.client("cloudformation", region_name=region)
    stacks = client.describe_stacks(StackName=stack_name)["Stacks"]
    if len(stacks) != 1:
        raise RuntimeError("스토리지 스택을 하나만 찾을 수 있어야 합니다.")
    stack = stacks[0]
    if stack.get("StackStatus") not in READY_STACK_STATUSES:
        raise RuntimeError(f"스토리지 스택이 준비되지 않았습니다: {stack.get('StackStatus')}")
    return {
        item["OutputKey"]: item["OutputValue"]
        for item in stack.get("Outputs", [])
    }


def _set_env(runtime: dict, name: str, value: str) -> None:
    env_vars = runtime.setdefault("envVars", [])
    for item in env_vars:
        if item.get("name") == name:
            item["value"] = value
            return
    env_vars.append({"name": name, "value": value})


def _subnet_ids(raw_subnets: str) -> list[str]:
    subnets = [item.strip() for item in raw_subnets.split(",") if item.strip()]
    if len(subnets) < 2 or len(subnets) != len(set(subnets)):
        raise ValueError("서로 다른 AZ의 private subnet이 두 개 이상 필요합니다.")
    if not all(re.fullmatch(r"subnet-[0-9a-f]+", subnet) for subnet in subnets):
        raise ValueError("PrivateSubnetIds 형식이 올바르지 않습니다.")
    return subnets


def _validate_network(subnets: list[str], vpc_id: str, region: str) -> None:
    """subnet이 대상 VPC의 서로 다른 AZ에 실제 존재하는지 확인합니다."""
    response = boto3.client("ec2", region_name=region).describe_subnets(SubnetIds=subnets)
    found = {item["SubnetId"]: item for item in response.get("Subnets", [])}
    if set(found) != set(subnets):
        raise ValueError("일부 private subnet을 조회할 수 없습니다.")
    if any(item.get("VpcId") != vpc_id for item in found.values()):
        raise ValueError("모든 private subnet은 스택의 VPC에 속해야 합니다.")
    availability_zones = {item.get("AvailabilityZone") for item in found.values()}
    if None in availability_zones or len(availability_zones) != len(subnets):
        raise ValueError("private subnet은 각각 서로 다른 AZ에 있어야 합니다.")


def configure(config: dict, outputs: dict[str, str]) -> dict:
    """CloudFormation 출력으로 CodingService의 VPC·S3 Files 설정을 구성합니다."""
    required = {
        "S3FilesAccessPointArn",
        "RuntimeSecurityGroupId",
        "PrivateSubnetIds",
        "VpcId",
    }
    missing = sorted(required - outputs.keys())
    if missing:
        raise ValueError(f"스토리지 스택 출력이 부족합니다: {', '.join(missing)}")

    runtime = next(
        (item for item in config.get("runtimes", []) if item.get("name") == "CodingService"),
        None,
    )
    if runtime is None:
        raise ValueError("agentcore.json에 CodingService Runtime이 없습니다.")

    subnets = _subnet_ids(outputs["PrivateSubnetIds"])
    vpc_id = outputs["VpcId"]
    if not re.fullmatch(r"vpc-[0-9a-f]+", vpc_id):
        raise ValueError("VpcId 형식이 올바르지 않습니다.")
    security_group = outputs["RuntimeSecurityGroupId"]
    if not re.fullmatch(r"sg-[0-9a-f]+", security_group):
        raise ValueError("RuntimeSecurityGroupId 형식이 올바르지 않습니다.")
    access_point_arn = outputs["S3FilesAccessPointArn"]
    if not re.fullmatch(
        r"arn:aws(?:-[a-z]+)?:s3files:[a-z0-9-]+:\d{12}:"
        r"file-system/fs-[A-Za-z0-9-]+/access-point/fsap-[A-Za-z0-9-]+",
        access_point_arn,
    ):
        raise ValueError("S3FilesAccessPointArn 형식이 올바르지 않습니다.")

    runtime["networkMode"] = "VPC"
    runtime["networkConfig"] = {
        "subnets": subnets,
        "securityGroups": [security_group],
    }
    runtime["filesystemConfigurations"] = [
        {"sessionStorage": {"mountPath": "/mnt/workspace"}},
        {
            "s3FilesAccessPoint": {
                "accessPointArn": access_point_arn,
                "mountPath": "/mnt/persistent",
            }
        },
    ]
    _set_env(runtime, "PERSISTENT_ROOT", "/mnt/persistent")
    return config


def _write_atomic(path: Path, content: str) -> None:
    """동일 디렉터리 임시 파일을 fsync한 뒤 설정 파일을 원자적으로 교체합니다."""
    temporary_path: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="w",
            encoding="utf-8",
            newline="\n",
            dir=path.parent,
            prefix=f".{path.name}.",
            suffix=".tmp",
            delete=False,
        ) as handle:
            temporary_path = Path(handle.name)
            handle.write(content)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary_path, path)
        temporary_path = None
    finally:
        if temporary_path is not None:
            temporary_path.unlink(missing_ok=True)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--stack-name", default="coding-service-storage")
    parser.add_argument("--region", default="us-west-2")
    parser.add_argument("--config", type=Path, default=DEFAULT_CONFIG)
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    outputs = _stack_outputs(args.stack_name, args.region)
    config = json.loads(args.config.read_text(encoding="utf-8"))
    updated = configure(config, outputs)
    runtime = next(item for item in updated["runtimes"] if item["name"] == "CodingService")
    _validate_network(runtime["networkConfig"]["subnets"], outputs["VpcId"], args.region)

    rendered = json.dumps(updated, ensure_ascii=False, indent=2) + "\n"
    if args.dry_run:
        print(rendered)
    else:
        _write_atomic(args.config, rendered)
        print(f"갱신 완료: {args.config}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
