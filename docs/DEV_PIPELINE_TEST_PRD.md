# HEZO 에이전트 파이프라인 통합 테스트 PRD

> 작성일: 2026-06-16  
> 브랜치: `feature/4-generation-agent-pipeline`  
> 상태: **진행 중**

---

## 1. 현재 개발 단계 목표

### 1-1. 이번 테스트 범위

```
[프론트엔드] 챗봇 → Contract JSON 생성 (가정 완료)
      ↓
[프론트엔드] 프리뷰 이미지 빌더 화면 표시
      ↓ 사용자 "발행" 클릭
      ↓
[P4] 생성 에이전트 (Bedrock AgentCore)
  ① contract_final.json (hezo-artifacts) 읽기
  ② render_spec.json 생성 → hezo-artifacts 저장
  ③ GEO 파일 생성 → hezo-sites 직접 저장
     llms.txt · llms-full.txt · sitemap.xml · robots.txt
      ↓
[P3] 빌드 워커 (ECS Fargate)
  ④ render_spec.json 읽기
  ⑤ Next.js next build/export → dist/
  ⑥ dist/ → hezo-sites/{site_id}/dist/ 업로드 (P5)
```

### 1-2. S3 최종 결과물

```
hezo-sites/{site_id}/
├── llms.txt           ← P4 생성 에이전트
├── llms-full.txt      ← P4 생성 에이전트
├── sitemap.xml        ← P4 생성 에이전트
├── robots.txt         ← P4 생성 에이전트
└── dist/              ← P3 빌드 워커
    ├── index.html
    └── assets/

hezo-artifacts/{site_id}/
├── contract_final.json
├── render_spec.json
└── validation_report.json (차후)
```

---

## 2. 에이전트별 개발 상태

### P4 생성 에이전트 — ✅ 구현 완료

| 파일 | 상태 |
|---|---|
| `agents/generation/agent_config.yaml` | ✅ |
| `agents/generation/action_groups/contract_loader.py` | ✅ |
| `agents/generation/action_groups/render_spec_saver.py` | ✅ (GEO 파일 저장 로직 추가 필요) |
| `agents/generation/action_groups/contract_uploader.py` | ✅ |
| `agents/generation/fixtures/contract_13_tax_landing.json` | ✅ |
| `agents/generation/fixtures/render_spec_13_tax_landing.json` | ✅ |
| `infra/step-functions/hezo_pipeline.json` | ✅ (검증·빌드 스텁) |

**⚠️ render_spec_saver.py 추가 작업 필요**  
- `SITE_BUCKET` 환경변수 추가
- `supplementary_files` → llms.txt / llms-full.txt / sitemap.xml / robots.txt → hezo-sites 직접 저장

### P3 빌드 워커 — ⏳ 연동 대기

| 항목 | 상태 |
|---|---|
| render_spec → next build/export | ✅ 스파이크 완료 |
| ECS Fargate Task 정의 | ⏳ P3 팀 작업 중 |
| Step Functions `WaitForBuildWorker` 실 연결 | ❌ 현재 Pass 스텁 |

### P4 검증 에이전트 — ❌ 미구현 (차후)

**입력**: P2 크롤링 결과(`crawl_snapshot.json`) + P3 빌드 정적 홈페이지  
**출력**: AI 친화 점수(M1~M8, 0~100점) + `validation_report.json`

3층 검증:
1. 정보 보존 — 회사명·연락처·CTA·서비스명 + 엔티티 일관성 (AgentCore LLM)
2. 요구사항 정합성 — 필수 섹션·unsupported feature (Rule Engine Lambda)
3. AI 친화 구조 — JSON-LD·llms.txt·H1·alt·질문형 H2 (BeautifulSoup)

### P4 리포트 에이전트 — ❌ 최소 구현만 (장기)

> **전제 조건**: 실제 고객 홈페이지 프론트+백엔드+배포 완료 후에야 의미 있음.  
> 현재는 뼈대(agent_config.yaml + 저장 Lambda)만 최소 구현.

**역할**: 질의셋 3개 → 상용 LLM(ChatGPT·Claude·Perplexity) 시뮬레이션 → `llm_report.json`

---

## 3. 레포 작업 필수 규칙 (HEZO-Agent-Convention.md)

### 작업 시작 전 체크리스트
```bash
git checkout main
git pull origin main        # 반드시 최신화
```

### 이슈 → 브랜치 → PR 순서 (절대 역순 금지)
```
1. GitHub Issue 생성 ([Feature] / [Task] / [Bug] 템플릿)
2. 이슈 번호 확인 후 브랜치 생성
   git checkout -b feature/<issue-number>-<description>
3. 작업 + 커밋
   git commit -m "feature(generation): <한 줄 설명>"
4. PR 생성 (Closes #이슈번호 포함)
```

### 브랜치명 예시
```
feature/7-render-spec-saver-geo-files
feature/8-p3-build-worker-integration
feature/9-validation-agent-rule-engine
fix/10-sitemap-xml-encoding
```

### 커밋 메시지 scope 목록
`generation` / `validation` / `report` / `infra` / `agent` / `schema` / `fixture` / `contract`

---

## 4. 다음 작업 우선순위

| 순서 | 작업 | 브랜치 예시 |
|---|---|---|
| 1 | `render_spec_saver.py` GEO 파일 저장 추가 | `feature/N-geo-file-save-in-render-spec-saver` |
| 2 | P3 ECS 빌드 워커 Step Functions 연동 | P3 팀과 seam 합의 후 |
| 3 | 검증 에이전트 구현 (artifact_fetcher + rule_engine + saver) | `feature/N-validation-agent` |
| 4 | 리포트 에이전트 최소 뼈대 | `feature/N-report-agent-skeleton` |

---

## 5. 의존성 & 인터페이스

| From | To | 데이터 | 경로 |
|---|---|---|---|
| P1 (챗봇) | P4 | Contract JSON | `hezo-artifacts/sites/{id}/contract_final.json` |
| P2 (크롤링) | P4 검증 | crawl_snapshot | `hezo-artifacts/sites/{id}/crawl_snapshot.json` |
| P4 생성 | P3 | render_spec | `hezo-artifacts/sites/{id}/render_spec.json` |
| P4 생성 | 사이트 | GEO 파일 | `hezo-sites/{id}/llms.txt` 외 3종 |
| P3 빌드 | 사이트 | 정적 파일 | `hezo-sites/{id}/dist/` |
| P3 → P4 검증 | P4 검증 | 빌드 산출물 | `hezo-sites/{id}/dist/` |
