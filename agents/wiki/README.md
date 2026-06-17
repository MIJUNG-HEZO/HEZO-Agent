# HEZO Wiki Agent (P2)

P2 위키 에이전트는 P1 챗봇이 적극적 질의 근거로 읽을 **업종 지식 위키(md)**를 만들고, 백그라운드 보강으로 위키를 복리로 키우는 **지식 레이어 + 크롤 워커**입니다.

## 책임 범위

- 업종 지식 위키 저장·구조·검색(파일명 기반)
- 룰 거름망 + LLM 검수(채점)
- 자동 탐색·크롤링(수집)
- 비교 merge 갱신
- 신선도(TTL) 관리

## 비범위

- 부족 판단·보완 생성 → **P1 책임** (P1이 보완한 md를 받아 비교만 함)
- 사용자 노출 Q&A → 위키는 P1의 질의 근거용이지 사용자 직접 노출 아님
- 자율 판단 에이전트 → 검수는 **고정 파이프라인**(아래 참조)

## MVP 스켈레톤 범위

이번 스켈레톤은 실제 AWS/Bedrock/크롤링 연동을 포함하지 않습니다.

포함:

- `agents/wiki/` 골격(`__init__`)
- 범위 README
- 위키 경로/버킷 상수 + 키 빌더 (`constants.py`)

제외(후속 이슈):

- 실제 S3 입출력(저장 계층)
- S3 버킷 생성(infra)
- 시드 적재 / 신규 대기열 초기화
- Brave 검색 + trafilatura 크롤링
- LLM 검수(룰 거름망 + 8항목 채점)
- frontmatter 파서
- Lambda / EventBridge / S3 이벤트

## 보강 두 경로

| | 보강 A: P1 보완 비교 | 보강 B: 배치 크롤링 |
|---|---|---|
| 트리거 | P1이 보완 md를 S3 업로드 → **S3 이벤트** | **EventBridge 하루 5번** (0·5·10·15·20시) |
| 하는 일 | 기존 위키 vs P1 보완 비교 검수 | 검색 → 크롤링 → 검수 → 저장 |
| LLM 정책 | **적극** (재채점 + 6항목 비교) | **최소** (8항목 채점만) |
| 검색·크롤링 | 없음 | 있음 |

## 위키 저장소 구조

```text
hezo-wiki/
├─ industries/      업종 지식 (tax/career/wine 시드 + 크롤링으로 채움)
├─ api_profiles/    API 명세 시드 (크롤링하지 않음)
└─ _internal/
    ├─ processed.json            처리 기록(중복·실패 방지)
    └─ pending_industries.json   신규 채우기 대기열
```

- 업종당 1파일, 인덱스 없음(파일명으로 검색). 경로 상수는 `constants.py`.

## 검수 정책

- **1차 룰 거름망** — 형식 체크 + PII 정규식 차단 + 출처 등급 태깅(정부 high / 기관 mid / 일반 low). 점수 없이 통과/탈락만.
- **2차 LLM 검수** — Bedrock Claude Sonnet `InvokeModel` **직접 호출**(고정 파이프라인). 베이스 0.5 + 8항목 가감점:
  - ① 일치 ② 사실성 ③ 과장 ④ 모순 ⑤ 출처등급 ⑥ 인젝션(게이트, 즉시 0.10) ⑦ 충실성(환각 방지) ⑧ 완전성·구조
  - **전 업종 단일 컷 0.70**
- **갱신 = 비교 merge**(덮어쓰기 아님): 이기면 교체(confidence·last_updated 갱신), 지면 기존 유지 + 거절 기록.

> **검수는 에이전트가 아니라 고정 파이프라인입니다.** LLM에 "채점만" 시키고 다음 행동을 LLM이 스스로 정하지 않으므로(자율 판단 없음), AgentCore가 아니라 Lambda + InvokeModel 직접 호출로 동작합니다.

## 신선도 (TTL)

업종 단위 `volatility`로 만료 판단:

| volatility | 만료 |
|---|---|
| high | 7일 |
| mid | 30일 |
| low | 만료 없음 |

새 업종은 기본 `mid`. 시드는 수동 지정(세무·커리어 high / 와인 mid).

## 신규 채우기 대기열

`_internal/pending_industries.json` = 템플릿 60개 − 시드 3종 = **57개**. 보강 B가 **신규 우선 → 대기열이 비면 만료 갱신** 순으로 처리하고, 신규 생성 성공 시 대기열에서 제거.

## 기술 스택

- **언어**: Python
- **검수 LLM**: AWS Bedrock Claude Sonnet (boto3 `InvokeModel` 직접 호출 = 고정 파이프라인, 에이전트/AgentCore 아님)
- **검색**: Brave Search API (출처 자동 탐색)
- **크롤링**: httpx + trafilatura (범용 본문 추출)
- **실행**: AWS Lambda + EventBridge(배치) + S3 이벤트(보강 A)
- **저장**: AWS S3 (md 파일)
- **관측**: `libs/telemetry` (토큰·비용·CloudWatch)

## 구현 순서

| 단계 | 내용 |
|---|---|
| 1차 | 위키 토대 — 구조 + 시드 적재 + 대기열 초기화 (+ 본 스켈레톤·버킷·저장 계층) |
| 2차 | Brave 검색 + 크롤링(trafilatura) + processed 기록 |
| 3차 | 보강 B LLM 검수(8항목) + 0.7 판정 + 비교 merge |
| 4차 | 보강 A — S3 이벤트 + 재채점 + 6항목 비교 |
| 5차 | EventBridge 배치(신규 우선 → 만료 갱신) + telemetry 연동 |

## 로컬 실행

후속 이슈(저장 계층·시드 적재)에서 추가 예정. AWS 접근은 `hezo-p2` 프로필 사용.
