# 배포 롤백 Runbook

이 runbook은 풀스택 배포(`DeployAgent → DeployApi → DeployWeb`)에서 어느 단계가 실패하거나 배포 후 문제가 발견됐을 때 각 계층을 독립적으로 되돌리는 절차를 설명합니다.

## 배포 원자성 한계

현재 CodePipeline은 `Agent(RunOrder 1) → API(RunOrder 2) → Web(RunOrder 3)` 순서로 순차 배포합니다. AWS CodePipeline은 선행 단계가 성공해도 후행 단계 실패 시 선행 변경을 자동으로 되돌리지 않습니다. 따라서 단계가 절반쯤 진행된 채로 파이프라인이 멈추면 배포가 부분 적용된 상태가 됩니다.

```
예시 시나리오:
  DeployAgent 성공 → 신규 RestaurantAgent 버전이 DEFAULT 엔드포인트에 올라감
  DeployApi  실패 → dining-web Lambda는 여전히 이전 ARN을 가리킴
  DeployWeb  실행 안 됨

이 상태에서 자동 rollback이 없으므로 아래 절차로 수동 처리합니다.
```

각 계층은 독립적으로 배포·롤백됩니다. 문제가 생긴 계층만 처리하면 되며, 반드시 역순(Web → API → Agent)으로 진행할 필요는 없습니다. 단, API가 RestaurantAgent ARN에 의존하고 Web이 API URL에 의존하므로 의존 방향을 고려해 진행하세요.

---

## 0. 사전 점검 — 현재 상태 파악

**파이프라인 실행 상태 확인**

```bash
aws codepipeline get-pipeline-state \
  --name restaurant-agent-fullstack-pipeline \
  --region us-west-2 \
  --query 'stageStates[*].{Stage:stageName,Status:latestExecution.status}'
```

**RestaurantAgent Runtime 버전·엔드포인트 확인**

```bash
uv run python ops/01_list_versions.py
```

`production` 엔드포인트가 가리키는 버전과 `DEFAULT` 엔드포인트 버전을 기록해 두세요.

**CodingService Runtime 상태 확인**

```bash
# services/CodingService 작업 디렉터리에서 실행
agentcore status
```

**CloudWatch 알람 상태 확인**

```bash
uv run python ops/07_observability_check.py --window-min 15
```

---

## 1. RestaurantAgent Runtime 롤백

RestaurantAgent는 버전 기반 배포를 사용합니다. `agentcore deploy`는 새 버전을 만들 뿐 `production` 엔드포인트는 명시적으로 승격하기 전까지 바뀌지 않습니다.

### 1-1. `DEFAULT` 엔드포인트(카나리)만 문제인 경우

`DEFAULT` 엔드포인트는 항상 최신 버전을 가리킵니다. 직접 롤백할 수 없으며, `production` 승격을 보류하고 신규 수정을 배포해 `DEFAULT`를 다시 덮어쓰는 것이 올바른 흐름입니다.

### 1-2. `production` 엔드포인트를 이전 버전으로 되돌려야 하는 경우

```bash
# 직전 버전으로 롤백 (가장 높은 이전 버전 번호를 자동 선택)
uv run python ops/05_rollback.py

# 특정 버전으로 롤백 (예: V3으로)
uv run python ops/05_rollback.py 3

# 롤백 후 엔드포인트 상태 확인
uv run python ops/01_list_versions.py
```

롤백 후 `production` 엔드포인트 `liveVersion`이 원하는 버전인지 확인합니다.

### 1-3. 모든 엔드포인트를 비상 정지해야 하는 경우

현재 AgentCore CLI는 엔드포인트 비활성화를 직접 지원하지 않습니다. 아래 IAM 정책을 Runtime IAM 역할에 Deny로 추가하거나, API Lambda의 `ReservedConcurrentExecutions`를 0으로 줄여 Agent에 도달하는 트래픽을 차단합니다.

```bash
# DiningFunction Lambda 동시성을 0으로 줄여 트래픽 차단
aws lambda put-function-concurrency \
  --function-name <DiningFunctionName> \
  --reserved-concurrent-executions 0 \
  --region us-west-2

# 복구 시 원래 값으로 복원
aws lambda put-function-concurrency \
  --function-name <DiningFunctionName> \
  --reserved-concurrent-executions 5 \
  --region us-west-2
```

> `<DiningFunctionName>`은 CloudFormation 스택 출력 또는 콘솔에서 확인합니다.

---

## 2. API (dining-web Lambda + API Gateway) 롤백

`dining-web` SAM 스택이 관리하는 Lambda와 API Gateway를 되돌립니다.

### 2-1. 이전 SAM 템플릿으로 재배포

파이프라인이 소스를 변경하지 않은 상태에서 API만 이전 상태로 되돌리려면 이전 커밋의 소스로 파이프라인을 재실행하거나, 로컬에서 직접 이전 버전을 배포합니다.

```bash
# 이전 버전의 코드를 체크아웃한 상태에서 수동 배포
cd labs/dining-web
sam build
sam deploy
```

### 2-2. Agent ARN 미스매치 수정

`DeployAgent` 성공 후 `DeployApi`가 새 ARN을 받지 못했다면 samconfig.toml을 수동으로 갱신하고 재배포합니다.

```bash
# 현재 배포된 Runtime ARN 조회
AGENT_RUNTIME_ARN=$(aws bedrock-agentcore-control list-agent-runtimes \
  --region us-west-2 \
  --query "agentRuntimes[?agentRuntimeName=='RestaurantAgent_RestaurantAgent'].agentRuntimeArn | [0]" \
  --output text)
echo "ARN: $AGENT_RUNTIME_ARN"

# labs/dining-web/samconfig.toml의 AgentRuntimeArn을 위 값으로 갱신한 뒤 재배포
cd labs/dining-web
sam deploy
```

### 2-3. 스택 자체를 롤백하려면

CloudFormation에서 이전 성공 상태로 되돌립니다.

```bash
aws cloudformation rollback-stack \
  --stack-name dining-web \
  --region us-west-2

# 롤백 완료 확인
aws cloudformation describe-stacks \
  --stack-name dining-web \
  --region us-west-2 \
  --query 'Stacks[0].{Status:StackStatus,Updated:LastUpdatedTime}'
```

> `ROLLBACK_COMPLETE` 상태의 스택은 삭제해야만 재생성할 수 있습니다. `UPDATE_ROLLBACK_COMPLETE`가 정상 복구 상태입니다.

---

## 3. Web 프론트엔드 (CloudFront + S3) 롤백

CloudFront는 캐시 무효화만 하고 S3 원본은 그대로 남으므로, 이전 빌드 아티팩트를 다시 올리거나 정적 파일을 덮어쓰면 됩니다.

### 3-1. 이전 프론트엔드 빌드를 S3에 재업로드

```bash
# 이전 커밋으로 체크아웃 후 빌드
git checkout <이전-커밋> -- labs/dining-web/frontend/
npm run build --prefix labs/dining-web/frontend

# S3에 업로드 (버킷 이름은 CloudFormation 출력에서 확인)
BUCKET=$(aws cloudformation describe-stacks \
  --stack-name dining-web-hosting \
  --region us-west-2 \
  --query 'Stacks[0].Outputs[?OutputKey==`BucketName`].OutputValue' \
  --output text)

aws s3 sync labs/dining-web/frontend/dist "s3://$BUCKET" --delete
```

### 3-2. CloudFront 캐시 무효화

S3 업로드 후 CDN 캐시를 비워야 변경이 즉시 반영됩니다.

```bash
DIST_ID=$(aws cloudformation describe-stacks \
  --stack-name dining-web-hosting \
  --region us-west-2 \
  --query 'Stacks[0].Outputs[?OutputKey==`DistributionId`].OutputValue' \
  --output text)

aws cloudfront create-invalidation \
  --distribution-id "$DIST_ID" \
  --paths "/*" \
  --region us-east-1
```

> CloudFront 무효화는 `us-east-1`에 요청합니다(글로벌 서비스이므로 리전 무관).

---

## 4. CodingService Runtime 롤백

CodingService는 독립 AgentCore 프로젝트입니다. `services/CodingService`를 작업 디렉터리로 사용해야 합니다.

### 4-1. 이전 컨테이너 이미지로 재배포

CodingService는 Container 빌드를 사용하므로, 이전 소스 커밋을 체크아웃한 뒤 재배포하면 이전 이미지가 재빌드돼 배포됩니다.

```bash
# 이전 소스를 가져온 상태에서 services/CodingService 작업 디렉터리 사용
git checkout <이전-커밋> -- app/CodingService/
agentcore validate --json
agentcore deploy --yes --json
agentcore status
```

### 4-2. 배포 중 문제가 생겼다면

```bash
# AgentCore 배포 상태 확인
agentcore status

# 직전 배포 로그 확인
cat agentcore/.cli/logs/deploy/$(ls -t agentcore/.cli/logs/deploy | head -1)
```

---

## 5. 배포 후 최종 검증

롤백 또는 재배포가 완료되면 다음 순서로 시스템 전체를 검증합니다.

```bash
# 1. RestaurantAgent 버전·엔드포인트 상태
uv run python ops/01_list_versions.py

# 2. CodingService 상태
agentcore status  # services/CodingService 작업 디렉터리에서

# 3. CloudWatch 알람 상태 (ALARM이 있으면 exit 1)
uv run python ops/07_observability_check.py

# 4. dining-web 스택 상태
aws cloudformation describe-stacks \
  --stack-name dining-web \
  --region us-west-2 \
  --query 'Stacks[0].{Status:StackStatus,Updated:LastUpdatedTime}'

# 5. Web 상태 확인 (배포된 API URL로 헬스 체크)
curl -s https://<api-url>/health | python -m json.tool
```

> `<api-url>`은 `dining-web` 스택의 `ApiUrl` 출력 또는 `samconfig.toml`에 기록된 값입니다.

---

## 6. 재발 방지

현재 파이프라인의 자동 롤백 부재를 보완하기 위한 운영 원칙입니다.

| 원칙 | 설명 |
| --- | --- |
| 배포 전 게이트 | 품질·보안 LLM 평가 게이트를 통과한 커밋만 배포됩니다. 평가 통과 비율을 높게 유지하는 것이 최선의 예방책입니다. |
| 카나리 검증 | 신규 버전은 `DEFAULT` 엔드포인트에서 먼저 확인하고 `ops/06_auto_promote.py` 오류율 기준을 통과한 뒤 `production`으로 승격합니다. |
| 소스 번들 보관 | S3 소스 버킷(`restaurant-agent-src.zip`)에는 버전 관리가 활성화되어 있으므로 이전 버전으로 파이프라인을 재실행할 수 있습니다. |
| 변경 단위 최소화 | Agent·API·Web 변경은 각각 독립된 PR과 커밋으로 관리해 롤백 범위를 최소화합니다. |

---

## 7. 향후 개선 과제

- **단계 간 자동 rollback**: CodePipeline의 단계별 성공/실패 이벤트에 Lambda 또는 Step Functions를 연결해 선행 단계 변경을 자동으로 되돌리는 보상 흐름 구현
- **배포 전 스냅샷**: 각 배포 전에 현재 Lambda alias ARN, S3 객체 버전 ID, CloudFront 설정 등 복구에 필요한 식별자를 DynamoDB 또는 SSM Parameter Store에 기록해 자동화된 롤백의 기반 데이터로 활용
- **smoke test 자동화**: 배포 직후 `/health` 엔드포인트와 핵심 에이전트 호출을 자동으로 실행하고, 실패하면 즉시 파이프라인에 알림을 보내는 CodeBuild 검증 단계 추가
