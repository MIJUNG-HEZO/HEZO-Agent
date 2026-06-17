# Contract JSON — P1 정합성 전달 문서

> 작성일: 2026-06-16  
> 작성자: P3/P4 담당  
> 목적: P1(챗봇) 팀이 생성할 `contract_final.json`의 구조·설계 이유·필수 규칙을 전달하여 P4 에이전트와 정합성을 맞춘다.

---

## 1. 전체 데이터 흐름

```
[P1 챗봇]
  사용자와 대화 → 슬롯 수집 → contract_final.json 생성
  → S3 업로드: hezo-artifacts/sites/{site_id}/contract_final.json
        ↓
[P4 생성 에이전트 — AgentCore Runtime]
  contract_final.json 로드
  → Claude Sonnet에게 Contract 전체를 컨텍스트로 전달
  → render_spec.json + GEO 4종 파일 생성
  → S3 저장: hezo-artifacts/sites/{site_id}/render_spec.json
             hezo-sites/sites/{site_id}/llms.txt 외 3종
        ↓
[P3 빌드 워커 — ECS]
  render_spec.json 로드 → HTML 템플릿 렌더링
  → S3 저장: hezo-sites/sites/{site_id}/dist/index.html
```

**핵심**: Contract JSON은 P1이 **유일하게 작성**하는 문서이고, P4가 이를 그대로 Claude에게 넘겨 홈페이지를 생성한다. 슬롯 하나가 빠지면 Claude가 임의로 채우거나 품질이 떨어진다.

---

## 2. 우리가 가정으로 만든 Contract JSON (원문)

```json
{
  "_comment": "P1 확정 Contract JSON — G slot-based schema v0.1.0 / landing template #13 (13-tax-accounting)",
  "schema_version": "0.1.0",
  "ids": {
    "project_id": "project_tax_13_001",
    "site_id": "site_tax_13_001",
    "tenant_id": "tenant_hezo_test"
  },
  "template": {
    "category": "landing",
    "template_id": "landing_tax",
    "slug": "tax"
  },
  "slots": {
    "business_name": "해조세무회계",
    "industry": "tax_accounting",
    "business_type": "세무사 사무소",
    "business_region": "서울 강남",
    "site_goal": "lead_capture",
    "target_audience": ["개인사업자", "소상공인", "초기 법인 대표"],
    "core_services": ["기장 대행", "부가세 신고", "종합소득세 신고", "법인세 신고", "창업 세무 상담"],
    "pain_points": ["세금 신고 일정 관리가 어렵다", "절세 가능 여부를 알고 싶다", "사업 초기 세무 구조를 잡고 싶다"],
    "required_sections": ["hero", "services", "process", "faq", "contact_form", "footer"],
    "tone_style": ["professional", "trustworthy", "clear"],
    "brand_keywords": ["정확한 신고", "빠른 상담", "절세 전략", "사업자 맞춤 관리"],
    "cta": ["무료 세무 상담 신청", "카카오톡 상담"],
    "contact_method": ["phone", "kakao", "contact_form"],
    "phone": "02-1234-5678",
    "kakao_channel": "@hezo-tax",
    "business_hours": "평일 09:00-18:00",
    "business_number": null,
    "reference_site_exists": false,
    "reference_sites": []
  },
  "slot_status": {
    "business_name":     { "status": "filled",   "confidence": 1.0,  "source": "user",  "ask_count": 1 },
    "industry":          { "status": "filled",   "confidence": 0.95, "source": "user",  "ask_count": 1 },
    "business_region":   { "status": "filled",   "confidence": 1.0,  "source": "user",  "ask_count": 1 },
    "site_goal":         { "status": "filled",   "confidence": 0.9,  "source": "user",  "ask_count": 1 },
    "target_audience":   { "status": "filled",   "confidence": 0.85, "source": "user",  "ask_count": 1 },
    "core_services":     { "status": "filled",   "confidence": 0.78, "source": "wiki",  "ask_count": 1 },
    "required_sections": { "status": "filled",   "confidence": 0.82, "source": "wiki",  "ask_count": 0 },
    "cta":               { "status": "filled",   "confidence": 0.9,  "source": "user",  "ask_count": 1 },
    "phone":             { "status": "filled",   "confidence": 1.0,  "source": "user",  "ask_count": 1 },
    "kakao_channel":     { "status": "filled",   "confidence": 1.0,  "source": "user",  "ask_count": 1 },
    "business_hours":    { "status": "filled",   "confidence": 1.0,  "source": "user",  "ask_count": 1 },
    "contact_method":    { "status": "filled",   "confidence": 0.88, "source": "user",  "ask_count": 1 },
    "business_number":   { "status": "missing",  "confidence": 0,    "source": null,    "ask_count": 0 }
  },
  "evidence": {
    "wiki_refs": [
      { "doc_id": "industries/tax_accounting.md",  "reason": "세무/회계 업종의 추천 섹션 및 CTA 근거" },
      { "doc_id": "templates/landing_tax.md",      "reason": "13-tax-accounting 템플릿 지원 섹션 및 레이아웃 근거" },
      { "doc_id": "api_profiles/lead_form_basic.md", "reason": "상담 신청 폼 지원 여부 근거" }
    ],
    "research_refs": []
  },
  "gates": {
    "completeness_score": 0.92,
    "preview_ready": true,
    "generation_ready": true,
    "missing_items": [
      { "slot_key": "business_number", "reason": "generation 단계 사업자등록번호 표시에 필요. 없어도 generation 진행 가능." }
    ],
    "unresolved_items": []
  }
}
```

---

## 3. 각 섹션 설계 이유

### 3.1 스키마 선택 — `G_slot_based`

우리가 `B_category_discriminated`, `D_block_driven`, `E_lifecycle_partitioned`, `G_slot_based` 중 G를 선택한 이유:

| 스키마 | 특징 | 탈락 이유 |
|---|---|---|
| B | template_category로 discriminate | 슬롯 상태(filled/missing) 개념 없음 → P1 챗봇 흐름 제어 불가 |
| D | block 단위로 콘텐츠를 직접 정의 | P1이 최종 HTML 구조까지 알아야 함 → 역할 초과 |
| E | 라이프사이클 단계별 필드 분리 | 너무 복잡, 단계 전환 로직이 P1 내부에 들어가야 함 |
| **G** | **slots(값) + slot_status(상태) 분리** | **P1이 "무엇을 물어봤고 무엇이 채워졌는가"를 정확히 표현 가능** |

**G를 선택한 핵심 이유**: P1 챗봇은 슬롯을 하나씩 채워나가는 구조다. `slot_status`가 있어야 "이 슬롯은 사용자가 직접 말했다 vs wiki에서 채웠다 vs 아직 모른다"를 P4가 구분할 수 있다. P4의 Claude 프롬프트는 Contract JSON 전체를 보고 홈페이지를 생성하므로, 슬롯의 신뢰도(confidence)가 낮으면 더 보수적으로 생성한다.

---

### 3.2 `ids` 섹션

```json
"ids": {
  "project_id": "project_tax_13_001",
  "site_id": "site_tax_13_001",
  "tenant_id": "tenant_hezo_test"
}
```

| 필드 | 설명 | 생성 규칙 |
|---|---|---|
| `site_id` | **파이프라인 전체의 기본 키** — S3 경로, AgentCore 호출, P3 빌드 모두 이걸로 참조 | `site_{industry}_{template_num}_{seq}` |
| `project_id` | 프로젝트 단위 식별자 (여러 사이트가 하나의 프로젝트에 속할 수 있음) | `project_{industry}_{seq}` |
| `tenant_id` | 고객(사업자) 식별자 — 향후 멀티테넌시 과금 기준 | `tenant_{name}` |

**P4가 실제로 사용하는 것**: `site_id`만 사용. S3 경로 `hezo-artifacts/sites/{site_id}/contract_final.json`으로 파일을 로드한다.

> ⚠️ **P1이 반드시 지킬 규칙**: `site_id`의 값이 S3 경로명이 된다. 영문 소문자·숫자·언더스코어만 사용. 하이픈(-)도 허용하지만 통일 권장.

---

### 3.3 `template` 섹션

```json
"template": {
  "category": "landing",
  "template_id": "landing_tax",
  "slug": "tax"
}
```

| 필드 | 설명 | 허용값 |
|---|---|---|
| `category` | 템플릿 대분류 | `landing` / `blog` / `store` |
| `template_id` | 60개 템플릿 중 하나 | `{category}_{slug_underscore}` — shared_vocab.md 카탈로그 기준 |
| `slug` | URL에 쓰이는 kebab-case 이름 | shared_vocab.md slug 컬럼 기준 |

**P3 빌더가 사용**: `template_id`로 로컬 HTML 파일을 찾는다. `landing_tax` → `agents/build/templates/landing/13-tax-accounting.html`. 이 매핑이 틀리면 빌더가 `FileNotFoundError`를 낸다.

**P4 Claude 프롬프트가 사용**: `template_id`에서 업종을 유추해 Schema.org 타입을 결정한다. `tax_accounting → Accountant`.

> ⚠️ **반드시 shared_vocab.md의 60개 카탈로그 중 하나여야 함. 임의로 만들면 P3에서 빌드 실패.**

---

### 3.4 `slots` 섹션 — 핵심 비즈니스 데이터

P4 Claude가 render_spec.json을 생성할 때 Contract JSON 전체를 보지만, `slots`가 가장 직접적으로 사용된다.

#### 필수 슬롯 (없으면 Claude가 임의로 채움 — 품질 저하)

| 슬롯 | 타입 | P4에서의 역할 | 예시 |
|---|---|---|---|
| `business_name` | string | H1, SEO title, Schema.org name, QuickAnswer 모두에 직접 삽입 | `"해조세무회계"` |
| `industry` | string | Schema.org 타입 결정 (`tax_accounting → Accountant`) | `"tax_accounting"` |
| `business_region` | string | H1, SEO keywords, Schema.org address에 삽입 | `"서울 강남"` |
| `site_goal` | enum | Hero CTA 방향 결정 (`lead_capture` → 상담 신청 버튼) | `"lead_capture"` |
| `core_services` | array | Services 블록 직접 구성, FAQPage 질문 생성 근거 | `["기장 대행", ...]` |
| `phone` | string | Contact 블록, Schema.org telephone | `"02-1234-5678"` |
| `business_hours` | string | Contact 블록, Schema.org openingHours | `"평일 09:00-18:00"` |

#### 선택 슬롯 (있으면 더 나은 결과)

| 슬롯 | 없을 때 동작 |
|---|---|
| `target_audience` | Claude가 업종에서 유추 |
| `pain_points` | FAQ 질문이 일반적으로 생성됨 |
| `brand_keywords` | SEO keywords가 업종 일반어로만 생성 |
| `tone_style` | 기본 professional 톤으로 생성 |
| `cta` | 업종 일반 CTA 사용 (`상담 신청` 등) |
| `kakao_channel` | Contact 블록에서 카카오 생략 |
| `business_number` | Schema.org에서 사업자번호 생략 |
| `reference_sites` | 경쟁사 분석 없이 생성 |

#### `industry` 허용값 (P4 프롬프트 기준)

```
tax_accounting   → Schema.org: Accountant
medical_clinic   → Schema.org: MedicalClinic
dental_clinic    → Schema.org: Dentist
law_firm         → Schema.org: LegalService
restaurant       → Schema.org: FoodEstablishment
fitness          → Schema.org: SportsActivityLocation
salon / nail     → Schema.org: BeautySalon
real_estate      → Schema.org: RealEstateAgent
education        → Schema.org: EducationalOrganization
그 외            → Schema.org: LocalBusiness
```

---

### 3.5 `slot_status` 섹션 — 슬롯 품질 메타데이터

```json
"slot_status": {
  "business_name": { "status": "filled", "confidence": 1.0, "source": "user", "ask_count": 1 },
  "core_services":  { "status": "filled", "confidence": 0.78, "source": "wiki", "ask_count": 1 },
  "business_number":{ "status": "missing", "confidence": 0, "source": null, "ask_count": 0 }
}
```

**이 섹션을 만든 이유**: P1 챗봇이 슬롯을 어떻게 채웠는지(직접 물어봤나, wiki에서 유추했나, 아직 모르나)를 P4가 알아야 Claude 프롬프트 품질이 올라간다. 현재 P4는 이 값을 직접 파싱하진 않지만, Claude는 Contract JSON 전체를 읽으므로 간접적으로 영향을 준다.

| 필드 | 허용값 | 의미 |
|---|---|---|
| `status` | `filled` / `missing` / `unresolved` | 슬롯 수집 상태 |
| `confidence` | 0.0 ~ 1.0 | 값의 신뢰도 (1.0 = 사용자가 직접 입력) |
| `source` | `user` / `wiki` / `research` / `default` / `inferred` | 값의 출처 |
| `ask_count` | 정수 | P1이 이 슬롯에 대해 재질문한 횟수 (2회↑ = 옵션형 질문으로 전환 기준) |

**`source` 구분 기준**:
- `user` — 사용자가 직접 채팅에서 말한 것
- `wiki` — 업종 내부 지식베이스에서 P1이 유추한 것 (P2가 아직 크롤링 안 한 경우)
- `research` — P2 크롤링 에이전트가 채운 것
- `inferred` — P1이 다른 슬롯에서 추론한 것 (예: `business_region`에서 `target_audience` 추론)

---

### 3.6 `evidence` 섹션 — 근거 추적

```json
"evidence": {
  "wiki_refs": [
    { "doc_id": "industries/tax_accounting.md", "reason": "세무/회계 업종의 추천 섹션 및 CTA 근거" }
  ],
  "research_refs": []
}
```

**설계 이유**: P1이 슬롯을 왜 이렇게 채웠는지 추적하기 위함. 디버깅 용도가 크고, 향후 P2(크롤링) 에이전트가 `research_refs`를 채운다.

- `wiki_refs` — P1 내부 지식베이스(wiki) 문서 참조
- `research_refs` — P2 크롤링 결과 참조 (현재 P2가 없으므로 빈 배열)

**P4는 이 필드를 직접 파싱하지 않는다.** Claude에 컨텍스트로 전달될 뿐.

---

### 3.7 `gates` 섹션 — 파이프라인 제어 핵심

```json
"gates": {
  "completeness_score": 0.92,
  "preview_ready": true,
  "generation_ready": true,
  "missing_items": [
    { "slot_key": "business_number", "reason": "없어도 generation 진행 가능." }
  ],
  "unresolved_items": []
}
```

**이 섹션이 가장 중요하다.** P4 에이전트가 시작 직후 `gates.generation_ready`를 체크하고, `false`면 즉시 종료한다.

```python
# agents/generation/agent.py 실제 코드
if not contract.get("gates", {}).get("generation_ready", False):
    return {"status": "skipped", "reason": "generation_ready=false"}
```

| 필드 | P4 동작 |
|---|---|
| `generation_ready: true` | P4가 생성 시작 |
| `generation_ready: false` | P4가 즉시 종료 (`status: skipped` 반환) |
| `completeness_score` | 현재 P4가 직접 체크하지 않음. 향후 임계값 검사 예정 |
| `missing_items` | P4가 직접 사용하지 않음. Claude에 컨텍스트로 전달 |

> ⚠️ **P1이 반드시 지킬 규칙: 사용자가 미리보기 승인을 마치면 `generation_ready: true`로 세팅 후 S3에 업로드해야 P4가 작동한다.**

---

## 4. P4가 Contract에서 실제로 만들어내는 것

Contract JSON 하나로 P4가 생성하는 파일 목록:

```
hezo-artifacts/sites/{site_id}/
  └── render_spec.json          ← P3 빌더 입력 (HTML 렌더링 지시서)

hezo-sites/sites/{site_id}/
  ├── llms.txt                  ← AI 크롤러용 간결 소개 (GPT, Claude, Perplexity)
  ├── llms-full.txt             ← AI 크롤러용 상세 설명
  ├── sitemap.xml               ← 검색엔진 사이트맵
  └── robots.txt                ← GPTBot/ClaudeBot/PerplexityBot Allow 규칙
```

**실제 생성 예시** (`site_tax_13_001` 기준):
- H1: `"강남 전문 세무사가 제공하는 맞춤형 세무회계 서비스"` ← `business_name` + `business_region` + `industry` 조합
- SEO title: `"강남 세무사 사무소 | 기장대행 및 세무상담 전문 | 해조세무회계"` ← `business_region` + `core_services` + `business_name`
- FAQ 6개: `core_services` + `pain_points`에서 질문 생성
- QuickAnswer: `"서울 강남에 위치한 해조세무회계는..."` ← 50~120자 업체 한줄 요약
- Schema.org type: `Accountant` ← `industry: "tax_accounting"` 매핑 결과

---

## 5. P1이 반드시 지켜야 할 규칙 요약

### 5.1 S3 저장 경로

```
s3://hezo-artifacts/sites/{site_id}/contract_final.json
```

파일명은 반드시 `contract_final.json`이어야 한다. P4 코드에 하드코딩됨.

### 5.2 `site_id` 네이밍 규칙

```
site_{industry}_{template_num}_{seq}
예: site_tax_13_001 / site_dental_01_003 / site_saas_03_002
```

영문 소문자, 숫자, 언더스코어만 허용. 이 값이 S3 경로가 된다.

### 5.3 `template_id` — 반드시 60개 카탈로그 중 하나

`shared_vocab.md` 기준. `{category}_{slug_underscore}` 형식.

```
landing_tax / landing_dental / landing_saas / blog_food_travel / store_cafe_menu ...
```

### 5.4 `gates.generation_ready`

사용자가 미리보기 확인 후 승인하면 `true`로 세팅. 이게 `false`면 P4는 아무것도 안 한다.

### 5.5 필수 슬롯 목록

아래 슬롯이 없으면 Claude가 임의로 채우거나 품질이 크게 떨어진다:

```
business_name    (업체명)
industry         (업종 코드)
business_region  (지역)
site_goal        (사이트 목적: lead_capture 등)
core_services    (핵심 서비스 목록, 배열)
phone            (전화번호: 0XX-XXXX-XXXX 형식)
business_hours   (영업시간)
```

### 5.6 `slot_status.source` 허용값

```
"user" / "wiki" / "research" / "default" / "inferred"
```

이 외의 값은 스키마 위반.

---

## 6. 현재 스키마(G_slot_based.schema.json)와의 차이점 — 협의 필요

우리가 만든 contract_final.json이 G 스키마를 100% 준수하지 않는다. P1 팀과 스키마 버전을 합쳐야 하는 항목들:

| 항목 | 우리 구현 | G 스키마 원본 | 문제 |
|---|---|---|---|
| `_comment` | 최상위에 `_comment` 필드 사용 | `additionalProperties: false`로 거부 | 스키마 검증 실패 → `_comment` 제거하거나 스키마에 추가해야 함 |
| `ids` 중첩 | `ids.site_id`, `ids.project_id` | 스키마는 최상위에 `site_id`, `project_id` 요구 | 구조 불일치 → 협의 필요 |
| `evidence` | 최상위에 `evidence` 추가 | 스키마에 없는 필드 | `additionalProperties: false` 위반 |
| `slots` 확장 | `business_name`, `industry`, `phone` 등 다수 추가 | 스키마 `slots`는 몇 개 필드만 정의 | `slots` 내부는 `additionalProperties` 허용 여부 확인 필요 |

**권장 조치**: G 스키마를 v0.2.0으로 업데이트하면서:
1. `ids` 섹션을 스키마에 공식 추가
2. `evidence` 섹션 추가
3. `_comment` 제거 (주석은 JSON 표준에 없음)
4. `slots` 내 업종별 필수 슬롯 enum 정의

---

## 7. P2 연계 — `crawl_snapshot.json`

P2(크롤링 에이전트)가 완성되면 contract_final.json과 별개로 아래 파일을 같은 경로에 추가한다:

```
s3://hezo-artifacts/sites/{site_id}/crawl_snapshot.json
```

P4가 이 파일을 옵션으로 로드해 Claude 프롬프트에 추가 컨텍스트로 붙인다. 없어도 생성 진행됨.

```python
# P4 contract_loader.py
crawl_snapshot = read_json(ARTIFACTS_BUCKET, f"sites/{site_id}/crawl_snapshot.json")
# → 없으면 None으로 처리, Claude 프롬프트에서 생략
```

---

## 8. 전달 체크리스트 (P1 팀 확인 항목)

- [ ] `site_id` 네이밍 규칙 확인 및 ID 생성 로직 구현
- [ ] `template_id` 선택 로직 — shared_vocab.md 60개 카탈로그 연동
- [ ] `gates.generation_ready` 세팅 타이밍 — 사용자 미리보기 승인 시점
- [ ] `slot_status.source` enum 준수 (`wiki` vs `research` vs `user` 구분)
- [ ] S3 업로드 경로 및 파일명(`contract_final.json`) 확인
- [ ] `ids` 중첩 구조 vs 스키마 최상위 구조 — 어떤 걸 표준으로 할지 협의
- [ ] `_comment` 필드 제거 여부 결정
- [ ] `evidence.research_refs` — P2 결과를 어떻게 받아 채울지 협의
- [ ] 필수 슬롯 7개 수집 보장 로직 구현
