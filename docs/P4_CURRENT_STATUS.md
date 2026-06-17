# P4 현재 상황 — 생성·검증·리포트 멀티에이전트

> 작성일: 2026-06-16  
> 작성자: P3/P4 담당  
> 브랜치: `feature/4-generation-agent-pipeline`

---

## 1. 개발 주간 전체 맥락

| 담당 | 영역 |
|---|---|
| P1 | LangGraph 챗봇 → Contract JSON 생성 |
| P2 | Wiki 자동 보강 / 크롤링 비동기 리서치 |
| **P3** | **JSON 렌더링 → 정적 파일 빌드 → S3 업로드** |
| **P4 (나)** | **생성 에이전트 → 검증 에이전트 → 리포트 에이전트** |
| P5 | AWS 인프라 / Step Functions 오케스트레이션 / 모니터링 |

**전제 조건 (오늘 기준)**
- 프론트엔드 · 백엔드: 로컬 개발 완료
- P3: 어제 대부분 완료. render_spec.json → 정적 HTML/CSS 렌더링 확인
- P1: Contract JSON 생성 완료로 가정 (schema_version: 0.1.0, G slot-based)

---

## 2. P4 목표 요약

```
[Step Functions 파이프라인 - P5 오케스트레이션]
  ①  UploadContractToS3   (Lambda: contract_uploader)
        ↓
  ②  InvokeGenerationAgent (Bedrock AgentCore: hezo-generation-agent)
        Contract JSON → render_spec.json → S3
        ↓
  ③  [P3 Build Worker]    render_spec → HTML/CSS → S3 hezo-sites
        ↓
  ④  InvokeValidationAgent (Bedrock AgentCore: hezo-validation-agent)
        3층 검증 → validation_report.json → S3
        ↓
  ⑤  [P5] 배포 확정 or 차단 판정
        ↓ (비차단·비동기·기본 OFF)
  ⑥  InvokeReportAgent   (Bedrock AgentCore: hezo-report-agent)
        질의 3개 → 외부 LLM 시뮬레이션 → llm_report.json → S3
```

---

## 3. 현재 구현 상태

### 3-1. 생성 에이전트 (Generation Agent) — ✅ 완료

| 파일 | 역할 | 상태 |
|---|---|---|
| `agents/generation/agent_config.yaml` | Bedrock AgentCore 정의, 시스템 프롬프트, Action Group OpenAPI 스키마 | ✅ |
| `agents/generation/action_groups/contract_uploader.py` | Step Functions 1단계 Lambda — S3에 contract_final.json 업로드 | ✅ |
| `agents/generation/action_groups/contract_loader.py` | Action Group Lambda — `/get-contract`, `/get-crawl-snapshot` | ✅ |
| `agents/generation/action_groups/render_spec_saver.py` | Action Group Lambda — `/save-render-spec` + CW 메트릭 기록 | ✅ |
| `agents/generation/render_spec_schema.json` | render_spec 출력 스키마 | ✅ |
| `agents/generation/test_agent_local.py` | 로컬 BLOCKING 조건 검증 스크립트 | ✅ |
| `agents/generation/fixtures/contract_13_tax_landing.json` | 세무사 사무소 예제 Contract JSON | ✅ |
| `agents/generation/fixtures/render_spec_13_tax_landing.json` | 세무사 예제 render_spec 레퍼런스 출력 | ✅ |
| `agents/generation/deploy.sh` | Lambda 패키징·배포 스크립트 | ✅ |

**에이전트 흐름 (구현 완료)**
```
Step Functions InputText
  → AgentCore가 load_contract 툴 호출 → S3 contract_final.json 읽기
  → render_spec 조립 (SEO / JSON-LD / FAQ 5~7개 / QuickAnswer / llms.txt)
  → save_render_spec 툴 호출 → S3 render_spec.json 저장 + CW 메트릭
  → 완료 응답: "render_spec_saved — site_id: {id}"
```

**BLOCKING 조건 (생성 에이전트가 반드시 충족)**
- H1 페이지당 정확히 1개
- FAQ 최소 5개 + FAQPage Schema.org JSON-LD
- llms.txt / robots.txt (GPTBot·ClaudeBot·PerplexityBot Allow)
- `gates.generation_ready = true` 이어야 에이전트 실행

---

### 3-2. Step Functions 상태머신 — ⚠️ 부분 완료

파일: `infra/step-functions/hezo_pipeline.json`

| 상태 | 구현 여부 |
|---|---|
| UploadContractToS3 | ✅ Lambda 연동 완료 |
| InvokeGenerationAgent | ✅ `bedrock:invokeAgent` 연동 완료 |
| CheckGenerationResult | ✅ `render_spec_saved` 키워드 감지 |
| MarkGenerationComplete | ✅ DynamoDB 상태 기록 |
| WaitForBuildWorker | ⚠️ 1초 대기 플레이스홀더 (P3 연동 대기) |
| BuildWorkerPlaceholder | ⚠️ Pass 스텁 (P3 완료 후 ECS RunTask로 교체) |
| ValidationAgentPlaceholder | ❌ Pass 스텁 — **검증 에이전트 미구현** |
| PipelineSuccess / MarkFailed | ✅ DynamoDB 실패 기록 포함 |

---

### 3-3. 검증 에이전트 (Validation Agent) — ❌ 미구현

**목표**: P3가 생성한 정적 HTML에 대해 3층 검증 실행

| 층 | 항목 | 처리 방식 |
|---|---|---|
| 1층 정보보존 | 회사명·연락처·CTA·서비스명 보존 여부 | AgentCore LLM (의미 비교) |
| 2층 정합성 | 필수 섹션 누락 / unsupported feature | Rule Engine Lambda |
| 3층 AI친화 | JSON-LD / llms.txt / robots / sitemap / H1·H2 / alt | Rule Engine Lambda (BeautifulSoup) |

**판정 결과**
- `FAIL_BLOCKING`: blocking 이슈 1개 이상 → Step Functions 배포 차단
- `PASS_WITH_WARNINGS`: warning만 → 배포 가능, 경고 기록
- `PASS`: 이슈 없음

**필요한 파일 (미생성)**
```
agents/validation/
  agent_config.yaml           (hezo-validation-agent Bedrock 정의)
  action_groups/
    artifact_fetcher.py       (hezo-p4-artifact-fetcher: S3 HTML + contract 읽기)
    rule_engine.py            (hezo-p4-rule-engine: BeautifulSoup 3층 검증)
    validation_report_saver.py (hezo-p4-validation-saver: validation_report.json → S3)
  fixtures/                   (테스트용 빌드 산출물 예제)
  test_agent_local.py         (BLOCKING 조건 검증 스크립트)
```

**출력 포맷** (`validation_report.json`)
```json
{
  "site_id": "...",
  "validation_status": "PASS | PASS_WITH_WARNINGS | FAIL_BLOCKING",
  "publish_ready": true,
  "layers": {
    "layer1_info_preservation": { "status": "PASS", "issues": [] },
    "layer2_requirements":      { "status": "PASS", "issues": [] },
    "layer3_ai_friendly":       { "status": "PASS_WITH_WARNINGS", "issues": [...] }
  },
  "blocking_issues": [],
  "warnings": [...],
  "checked_at": "..."
}
```

---

### 3-4. 리포트 에이전트 (Report Agent) — ❌ 미구현

> 기본 OFF, 배포 차단 경로 밖, 비동기·비차단. 우선순위 낮음.

**목표**: 외부 LLM 관점의 AI 가시성 관찰 리포트 (`llm_report.json`)

**평가 질문 3개 (LLM 시뮬레이션)**
```
Q1. "{업종} 분야에서 이 서비스는 어떤 특징이 있나요?"
Q2. "이 서비스를 이용하거나 문의하려면 어떻게 해야 하나요?"
Q3. "이 업체의 핵심 강점과 차별점은 무엇인가요?"
```

**필요한 파일 (미생성)**
```
agents/report/
  agent_config.yaml           (hezo-report-agent, Claude Haiku)
  action_groups/
    site_content_fetcher.py   (hezo-p4-site-fetcher: 공개 URL / llms.txt 읽기)
    report_saver.py           (hezo-p4-report-saver: llm_report.json → S3)
```

---

### 3-5. 인프라 (IAM / CloudWatch) — ✅ 완료

| 파일 | 내용 |
|---|---|
| `infra/iam/bedrock-agent-*.json` | Bedrock Agent IAM 정책·트러스트 |
| `infra/iam/lambda-*.json` | Lambda IAM 정책·트러스트 |
| `infra/iam/step-functions-*.json` | Step Functions IAM 정책·트러스트 |
| `infra/cloudwatch/dashboard.json` | 비용·지연·호출수 대시보드 |
| `infra/aws_setup.sh` | 환경 설정 스크립트 |
| `libs/telemetry/telemetry.py` | P5 telemetry 키트 (CW 메트릭 2-line 패턴) |

---

## 4. 오늘 작업 목표 (우선순위 순)

### 우선 1 — 검증 에이전트 구현
1. `agents/validation/action_groups/artifact_fetcher.py` — S3에서 HTML + contract + crawl_snapshot 읽기
2. `agents/validation/action_groups/rule_engine.py` — BeautifulSoup 3층 Rule Engine
3. `agents/validation/action_groups/validation_report_saver.py` — validation_report.json S3 저장
4. `agents/validation/agent_config.yaml` — Bedrock AgentCore 정의
5. `agents/validation/test_agent_local.py` — 로컬 검증 스크립트
6. Step Functions `hezo_pipeline.json` — `ValidationAgentPlaceholder` → 실제 `InvokeAgent` 교체

### 우선 2 — P3 Build Worker 연동
- `WaitForBuildWorker` / `BuildWorkerPlaceholder` → P3 팀과 협의 후 ECS RunTask 또는 Lambda로 교체

### 우선 3 (시간 여유 시) — 리포트 에이전트
- 검증 에이전트 완료 후 진행

---

## 5. 데이터 흐름 요약

```
P1 Contract JSON (schema_version: 0.1.0)
  → S3: sites/{site_id}/contract_final.json          [contract_uploader Lambda]
  → S3: sites/{site_id}/render_spec.json             [generation-agent → render_spec_saver Lambda]
  → S3: sites/{site_id}/html/ (정적 파일)             [P3 Build Worker]  ← 연동 대기
  → S3: sites/{site_id}/validation_report.json       [validation-agent] ← 미구현
  → S3: sites/{site_id}/llm_report.json              [report-agent]     ← 미구현
```

---

## 6. 로컬 테스트 방법

```bash
# 생성 에이전트 출력 검증 (세무사 fixture 기준)
cd HEZO-Agent/agents/generation
python test_agent_local.py --fixture contract_13_tax_landing.json

# 특정 render_spec 파일 검증
python test_agent_local.py \
  --fixture contract_13_tax_landing.json \
  --render-spec /path/to/render_spec.json
```

---

## 7. 관련 파일 위치

| 항목 | 경로 |
|---|---|
| P4 PRD | `3. 에이전트 개발/에이전트 개발 3- P1~5 분업 PRD/P4 - 생성+검증+리포트 멀티에이전트 구축/PRD_P4_확정판_v1.md` |
| 생성 에이전트 코드 | `HEZO-Agent/agents/generation/` |
| Step Functions 정의 | `HEZO-Agent/infra/step-functions/hezo_pipeline.json` |
| IAM 정책 | `HEZO-Agent/infra/iam/` |
| 텔레메트리 키트 | `HEZO-Agent/libs/telemetry/telemetry.py` |
