# Step Functions 파이프라인 재시도 설계 결정

> 작성: 2026-06-19 | 대상 독자: P5 인프라팀

---

## 배경

클라이언트가 "발급하기"를 누르면 아래 파이프라인이 실행된다.

```
[콘텐츠 파이프라인]
InvokeGenerationAgent → InvokeBuildWorker → InvokeValidationAgent
  → PASS  → MarkPublished → PublishSiteEvent → PipelineSuccess
  → FAIL  → MarkFailed   → HandleFailure(SNS) → PipelineFailed

[IaC 파이프라인] — 콘텐츠 파이프라인 완료 후 별도 트리거
EventBridge "site-published" → hezo_iac_pipeline
```

검증 에이전트가 `FAIL_BLOCKING`을 반환했을 때 Step Functions 레벨에서 자동으로
생성 에이전트부터 재시도하는 루프를 만들어야 하는지 검토했다.

---

## 현재 재시도 구조 (2-레벨)

### 레벨 1 — 검증 에이전트 내부 루프

Step Functions 바깥에서 에이전트가 자체적으로 처리한다.

```
InvokeValidationAgent (TimeoutSeconds=1800)
  └─ for attempt in 1..3:
       Layer 3 → 2 → 1 검증
       PASS         → 즉시 PASS 반환
       FAIL_BLOCKING, attempt < 3
                    → render_spec 직접 패치
                    → P3 /invocations HTTP 호출 (재빌드)
                    → 재검증
       FAIL_BLOCKING, attempt == 3
                    → validation_feedback.json S3 저장
                    → FAIL_BLOCKING 반환
```

**처리 대상:** 패치로 빠르게 고칠 수 있는 구조적 이슈
- `NO_H1`, `NO_TITLE_TAG`, `NO_LLMS_TXT` — render_spec 필드 직접 주입
- `NO_JSONLD`, `NO_FAQ_PAGE_JSONLD` — Bedrock으로 FAQPage JSON-LD 생성 후 주입
- `MULTIPLE_H1` — 두 번째 이후 블록의 `h1` → `h2` 강등

### 레벨 2 — Step Functions (현재)

```
CheckValidationResult
  → PASS / PASS_WITH_WARNINGS  → MarkPublished → PipelineSuccess
  → FAIL_BLOCKING              → MarkFailed → HandleFailure(SNS) → PipelineFailed
```

`InvokeValidationAgent`에 `Retry` 블록 없음. FAIL_BLOCKING은 파이프라인 확정 실패.

---

## Step Functions 레벨 자동 재시도를 추가하지 않는 이유

### 1. 재시도가 의미 없는 케이스

검증 에이전트 내부 3회가 전부 실패했다는 것은 패치로 해결 불가능한 근본 문제다.

- 생성 에이전트가 구조적으로 잘못된 render_spec을 만들고 있거나
- 템플릿 자체에 버그가 있는 경우

**같은 Contract JSON으로 생성부터 다시 돌려도 동일한 결과가 나올 가능성이 높다.**
자동 재시도는 실패를 지연시킬 뿐이다.

### 2. 클라이언트 대기시간 문제

Step Functions 자동 재시도 2회를 추가한다고 가정할 때 최악 케이스:

```
1회차: 생성(3분) + 빌드(2분) + 검증 내부 3회(~15분) = 20분
  → FAIL → Step Functions 재시도
2회차: 생성(3분) + 빌드(2분) + 검증 내부 3회(~15분) = 20분
  → FAIL → 최종 실패
총 대기시간: ~40분
```

발급 버튼을 눌렀다가 40분 뒤에 "실패" 메시지를 받는 UX는 허용하기 어렵다.

### 3. 실패 원인 파악이 어려워짐

자동 재시도 중에는 `publish_status`가 `building` / `validating` 상태를 반복한다.
프론트엔드와 운영자 모두 "지금 뭘 하고 있는지" 알 수 없게 된다.

---

## 확정 설계

### FAIL_BLOCKING 발생 시 처리 흐름

```
CheckValidationResult → FAIL_BLOCKING
  └─ MarkFailed
       DynamoDB publish_status = "failed"
  └─ HandleFailure
       SNS → 운영자 알림 (Slack 또는 Email)
  └─ PipelineFailed
       Step Functions 실행 상태: FAILED

프론트엔드:
  GET /sites/{id}/pipeline/status → publish_status = "failed"
  → "발급 실패" 상태 노출 + "재시도" 버튼 표시
```

### 재시도는 사용자/운영자가 의도적으로 트리거

```
사용자가 "재시도" 버튼 클릭
  → POST /sites/{id}/publish
     → Step Functions StartExecution
        → CheckIdempotency: publish_status = "failed" → MarkBuilding부터 재시작
        → 생성 에이전트: validation_feedback.json 읽어서 실패 원인 반영 후 재생성
```

`validation_feedback.json`이 S3에 저장돼 있으므로 재시도 시 생성 에이전트가
이전 실패 원인과 힌트를 반영해 더 나은 render_spec을 만든다 (`feedback_loader.py`).

---

## P5 구현 체크리스트

### 현재 ASL에서 변경할 것 없음

`hezo_pipeline.json` v5.0의 `CheckValidationResult` → `MarkFailed` 경로는 현재대로 유지.
`InvokeValidationAgent`에 Retry 블록 추가하지 않는다.

### 백엔드에서 확인할 것

`POST /sites/{id}/publish` 엔드포인트가 `publish_status = "failed"` 상태에서도
Step Functions StartExecution을 정상 호출하는지 확인.
(현재 코드: `sites.py` — `draft` 상태만 허용하면 재시도 불가)

### 프론트엔드에서 확인할 것

`publish_status = "failed"` 시 "재시도" 버튼 노출 + `validation_feedback`의
blocking 이슈 목록을 사용자에게 표시하면 이상적.

### DynamoDB `attempt` 필드

현재 `hezo_pipeline_state` 테이블에 `attempt` 필드가 설계돼 있다.
자동 재시도에는 사용하지 않지만, 수동 재시도 횟수 추적 및 운영 지표로 활용 가능.
`StartExecution` 시 `MarkBuilding`에서 `attempt ADD 1` 업데이트 추가 권장.

---

## 요약

| 항목 | 결정 |
|---|---|
| 검증 에이전트 내부 루프 (3회) | ✅ 유지 |
| Step Functions 자동 재시도 | ❌ 추가하지 않음 |
| FAIL_BLOCKING 처리 | DynamoDB failed + SNS 알림 + PipelineFailed |
| 재시도 주체 | 사용자(프론트 "재시도" 버튼) 또는 운영자(StartExecution 수동 호출) |
| validation_feedback.json | 재시도 시 생성 에이전트가 자동 참조 |
| IaC 파이프라인 영향 | 콘텐츠 파이프라인 성공 후 별도 트리거 — 재시도와 무관 |
