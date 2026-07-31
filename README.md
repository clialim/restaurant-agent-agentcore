# Restaurant Agent AgentCore

Strands Agents SDK로 구현한 레스토랑 에이전트를 Amazon Bedrock AgentCore Runtime에 배포하고, 버전·엔드포인트 기반 카나리 배포와 롤백, 평가 게이트 기반 CI/CD 파이프라인까지 구성한 프로젝트입니다.

## 아키텍처

```mermaid
flowchart TB
    User[사용자] --> Prod[production 엔드포인트]
    User --> Default[DEFAULT 엔드포인트]

    subgraph Runtime[AgentCore Runtime]
        Prod --> V1[버전 V1<br/>안정 버전]
        Default --> V2[버전 V2<br/>신규 버전]
    end

    subgraph Agent[RestaurantAgent - Strands]
        Tools[도구<br/>search_restaurants<br/>get_restaurant_reviews<br/>check_reservations<br/>create_reservation]
        Prompt[SYSTEM_PROMPT<br/>주제 제한·보안 가드레일]
    end

    V1 -.-> Agent
    V2 -.-> Agent

    Ops[ops 운영 스크립트<br/>버전 조회·승격·롤백·자동 승격] --> Runtime
```

카나리 배포는 신규 버전(V2)을 `DEFAULT` 엔드포인트에 먼저 올려 검증하고, 오류율이 기준 이하이면 `production` 엔드포인트를 V2로 승격합니다. 문제가 있으면 이전 버전으로 롤백합니다.

## 프로젝트 구조

```text
RestaurantAgent/
├── agentcore/              # AgentCore CLI 설정(agentcore.json, aws-targets.json, .env.local)
├── app/
│   └── RestaurantAgent/    # 에이전트 애플리케이션 코드(main.py)
├── ops/                    # 버전·엔드포인트 운영 스크립트
├── scripts/
│   └── build_source_bundle.py  # CI/CD 소스 번들(ZIP) 생성
├── tests/
│   └── eval_gate.py        # 배포 전 품질 평가 게이트
├── buildspec-test.yml      # CodeBuild: 평가 게이트 실행
├── buildspec-deploy.yml    # CodeBuild: 평가 통과 시 자동 배포
├── .env.example            # 로컬 환경 변수 키 템플릿
├── pyproject.toml
└── uv.lock
```

`ops/`에는 버전·엔드포인트 운영 스크립트가 순서대로 있습니다.

1. `01_list_versions.py`: 버전·엔드포인트 조회
2. `02_create_endpoint.py`: 버전이 고정된 production 엔드포인트 생성
3. `03_invoke_endpoint.py`: qualifier로 엔드포인트별 호출
4. `04_promote.py`: 카나리 검증 후 production 승격
5. `05_rollback.py`: 이전 버전으로 롤백
6. `06_auto_promote.py`: 카나리 오류율 자동 측정 후 기준 통과 시 자동 승격

## 환경 준비

- Python 3.13
- [uv](https://docs.astral.sh/uv/)
- Node.js와 npm(AgentCore CLI 설치는 첫 배포 브랜치에서 진행)
- Amazon Bedrock 및 AgentCore를 사용할 수 있는 AWS 자격 증명

```powershell
uv sync --frozen
```

`.env.example`을 참고해 로컬 `.env`를 구성합니다. `.env`와 `agentcore/.env.local`은 Git에 커밋하지 않습니다.

## CI/CD 파이프라인

에이전트 코드가 변경될 때마다 품질을 자동 평가하고, 평가를 통과할 때만 배포되는 CI/CD 루프를 AWS CodeBuild와 CodePipeline으로 구성했습니다.

```mermaid
flowchart LR
    Dev[소스 ZIP 업로드] --> S3[(S3 소스 버킷)]
    S3 --> Source[Source<br/>소스 가져오기]

    subgraph Pipeline[CodePipeline: restaurant-agent-pipeline]
        Source --> Build[Build 평가 게이트<br/>buildspec-test.yml<br/>tests/eval_gate.py]
        Build -->|평균 점수 &ge; 0.7| Deploy[Deploy<br/>buildspec-deploy.yml<br/>agentcore deploy]
        Build -->|평균 점수 &lt; 0.7| Blocked[배포 차단<br/>파이프라인 중단]
    end

    Deploy --> Runtime[AgentCore Runtime<br/>새 버전 배포]
```

- **평가 게이트**(`tests/eval_gate.py`): `strands-agents-evals`의 LLM-as-a-Judge로 세 가지 시나리오(이탈리안 추천, 매운맛 제외, 미확인 정보 추측 금지)를 채점합니다. 평균 점수가 임계값 미달이면 `exit 1`로 배포를 차단합니다.
- 평가 게이트는 배포되는 코드와 동일한 `SYSTEM_PROMPT`·도구 구성을 재사용해, 실제로 배포될 에이전트를 평가합니다.

로컬에서 평가 게이트만 실행하려면:

```powershell
uv run python tests/eval_gate.py
```

### 소스 번들 생성과 배포

파이프라인의 Source는 S3에 올라온 ZIP을 사용합니다. `scripts/build_source_bundle.py`는 `git ls-files`로 추적 중인 파일만 담아 재현 가능한 소스 번들을 만듭니다. 작업 트리의 현재 내용을 담으므로 `.venv`·`node_modules`·`cdk.out`·`.cache` 같은 생성물이 포함되지 않습니다.

```powershell
# 소스 번들 생성
uv run python scripts/build_source_bundle.py

# S3 업로드 → 파이프라인 트리거
aws s3 cp restaurant-agent-src.zip s3://restaurant-agent-src-262428258542/restaurant-agent-src.zip
```

### 평가 게이트 동작 검증 (실패 → 복구)

평가 게이트가 실제로 저품질 변경의 배포를 막는지 확인하기 위해, 도구 데이터에 회귀(추천 대상 식당 데이터 누락, 매운맛 플래그 오설정)를 주입해 파이프라인을 실행했습니다.

| 구분 | 소스 상태 | 평가 평균 | 결과 |
| --- | --- | --- | --- |
| 실패 | 도구 데이터 회귀 주입 | 0.40 (< 0.7) | Build 실패 → **Deploy 차단** |
| 복구 | 정상 데이터 복원 | 0.73 (≥ 0.7) | Build 통과 → **Deploy 성공** |

품질이 임계값 아래로 떨어진 변경은 Build 단계에서 `exit 1`로 차단되어 프로덕션까지 도달하지 못하고, 정상 복원 후에는 전체 스테이지가 통과해 자동 배포되는 것을 확인했습니다.

1. 게이트 차단 — Build 실패, Deploy 미실행
회귀가 주입된 소스로 평가 평균이 0.40이 되어 Build가 실패하고 Deploy 스테이지가 실행되지 않은 파이프라인 화면.
<!-- 이미지 자리: 실패 파이프라인 -->

2. 게이트 차단 로그 — CodeBuild
`평균 점수: 0.40 (임계 0.7)` / `게이트 미달 — 배포를 차단합니다.`가 찍힌 CodeBuild 빌드 로그.
<!-- 이미지 자리: 실패 빌드 로그 -->

3. 복구 — 전체 스테이지 성공
정상 데이터로 복원한 뒤 Source → Build → Deploy가 모두 성공한 파이프라인 화면.
<!-- 이미지 자리: 복구 파이프라인 -->

## 브랜치와 PR 단위

| 순서 | 브랜치 | 범위 |
| --- | --- | --- |
| 1 | `feature/agentcore-deployment` | Part 1: 프로젝트 확인, CLI 설정, 최초 배포·호출 |
| 2 | `feature/agent-tool-redeployment` | Part 2와 3: 도구 추가, 재배포, 콘솔 검증 |
| 3 | `feature/agentcore-version-endpoints` | Part 4 전반: `01`~`03` 조회·생성·호출 스크립트 |
| 4 | `feature/agentcore-canary-rollout` | Part 4 후반: `04`~`05` 승격·롤백 스크립트 |
| 5 | `feature/ci-pipeline` | 평가 게이트 기반 CodeBuild/CodePipeline CI/CD 구성 |
| 6 | `feature/eval-gate-demo` | 소스 번들 스크립트, 평가 게이트 실패/복구 검증 |

각 브랜치는 `main`에서 생성하고, 검토와 검증을 마친 뒤 PR을 squash merge하고 삭제합니다. 콘솔 확인만 필요한 Part 3은 별도 코드 브랜치로 나누지 않고 Part 2 PR의 검증 결과에 기록합니다.

## 참고

- [AWS AgentCore Runtime Python 배포 가이드](https://docs.aws.amazon.com/bedrock-agentcore/latest/devguide/runtime-get-started-code-deploy-python.html)
- [Strands Agents 문서](https://strandsagents.com/latest/)
