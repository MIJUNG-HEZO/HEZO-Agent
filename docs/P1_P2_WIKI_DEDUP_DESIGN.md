# P1 → P2 Wiki 보강 설계: 동시성·경합 처리

> 작성일: 2026-06-19  
> 대상: P1 챗봇 에이전트, P2 위키 워커

---

## 1. 전제: P2 wiki는 업종별 공유 지식 베이스

같은 업종의 사용자는 동일한 `hezo-wiki/industries/{domain}.md`를 읽는다.

```
치과 A → hezo-wiki/industries/dental.md  ┐
치과 B → hezo-wiki/industries/dental.md  ┘ 동일한 파일
```

**사용자 간 차별화는 wiki가 아닌 Contract JSON slots에서 온다.**

| 레이어 | 동일 여부 | 내용 |
|---|---|---|
| P2 wiki (`dental.md`) | 동일 | 치과 업종 공통 지식 (FAQ 패턴, 핵심 키워드) |
| Contract JSON slots | 다름 | 치과 A: "강남구, 이 원장, 임플란트 전문" / 치과 B: "마포구, 박 원장, 교정 전문" |
| P4 생성 에이전트 출력 | 다름 | wiki(공통) + contract(개인) → 개인화된 홈페이지 |

---

## 2. 문제 정의: domain.md에 대한 4개의 쓰기 소스

P2가 관리하는 `domain.md`에는 다음 4개의 소스가 쓰기를 시도한다.

| # | 소스 | 타이밍 | 쓰기 주체 |
|---|---|---|---|
| 1 | 기존 `domain.md` | 상시 존재 | P2 |
| 2 | P1 supplement MD | 챗봇 세션 중 즉시 | P1 |
| 3 | 보강 A (P1 supplement 검증·보강) | supplement 업로드 직후 | P2 |
| 4 | 보강 B (배치 크롤링) | 하루 5회 | P2 |

이 4개가 같은 파일에 동시에 쓰려 하면 충돌이 발생한다.

---

## 3. 핵심 설계 원칙

### 원칙 1: domain.md 쓰기는 P2만 한다

P1은 `domain.md`에 직접 쓰지 않는다. P1의 supplement는 **staging 경로**에만 저장한다.

```
P1  →  hezo-wiki/staging/{domain}/{uuid}.md  (staging, 임시)
P2  →  hezo-wiki/industries/{domain}.md      (canonical, P2만 씀)
```

### 원칙 2: domain.md 쓰기는 한 번에 하나만

보강 A와 보강 B가 동시에 같은 `domain.md`를 쓰면 한 쪽 결과가 덮어씌워진다.

```
P2 인스턴스 #1: dental.md 읽기 → 섹션 X 추가 → 쓰기
P2 인스턴스 #2: dental.md 읽기 → 섹션 Y 추가 → 쓰기  ← #1 결과 유실
```

S3는 마지막 쓰기가 이기는 구조(last-write-wins)라 동시 쓰기 시 데이터가 사라진다.  
**DynamoDB 플래그로 도메인별 쓰기를 직렬화한다.**

### 원칙 3: 섹션 단위 병합 (전체 파일 덮어쓰기 금지)

```
현재 domain.md를 H2 섹션으로 파싱
새 콘텐츠를 H2 섹션으로 파싱

각 새 섹션에 대해:
  domain.md에 같은 H2가 있음 → confidence 비교 → 높으면 교체
  domain.md에 없음           → 섹션 추가

병합 결과를 domain.md에 단일 쓰기
frontmatter last_updated, confidence 갱신
```

---

## 4. 경합 시나리오별 처리

### 시나리오 A, B: 동시에 같은 업종 supplement 업로드

```
A, B 동시에 P1에서 supplement 업로드 시도
  → DynamoDB 조건부 쓰기: A 성공, B 실패
  → A만 P2 트리거, B는 드롭
  → domain.md 단 1회 갱신
```

**해결 수단: DynamoDB 조건부 PutItem (원자적)**

두 P1이 동시에 같은 도메인을 체크해도 DynamoDB 조건부 쓰기는 원자적이라 하나만 성공한다.

### 시나리오 C, D: P1 읽기 중 P2 보강 완료

```
C 읽기 시작 → dental.md v1 (보강 전)
P2 보강 완료 → dental.md v2 갱신
D 읽기       → dental.md v2 (보강 후)
```

C와 D가 동시에 들어왔지만 서로 다른 버전을 받는다.

**이것은 허용 가능한 불일치다.** domain.md는 트랜잭션 데이터가 아니라 지식 베이스다.

| 상황 | 문제 여부 |
|---|---|
| C는 v1, D는 v2 — 둘 다 정확한 정보 | 허용 가능 (품질 차이, 오류 아님) |
| C가 세션 도중 wiki 버전이 바뀜 | 세션 스냅샷으로 차단 (아래 참조) |
| C가 완전히 잘못된 정보를 받음 | 발생 불가 (S3 강한 일관성) |

S3는 강한 일관성(strong consistency)을 보장하므로 부분적으로 쓰인 파일은 절대 반환하지 않는다.  
C는 v1 완성본, D는 v2 완성본을 받는다.

---

## 5. 전체 흐름

```
P1 챗봇이 wiki 부족 감지
  ↓
P1이 gap을 인라인으로 채움 (supplement 내용 생성)
  ↓
S3 업로드 전 DynamoDB 조건부 쓰기 시도:
  PutItem {domain_id: "dental", ttl: 현재 + 72시간}
  ConditionExpression: attribute_not_exists(domain_id)
       ↓ 성공                        ↓ 실패
  (레코드 없었음)               (이미 존재)
       ↓                              ↓
  staging/{domain}/{uuid}.md 업로드  업로드 스킵. 끝.
       ↓
  P2 보강 A S3 이벤트 수신
       ↓
  staging + domain.md 읽기 → H2 섹션 단위 병합 → domain.md 갱신
       ↓
  staging 파일 삭제
  (TTL 만료 전까지 DynamoDB 플래그 유지 → 중복 방지)

보강 B (독립 경로):
  EventBridge 하루 5회 → 크롤링 결과 + domain.md 병합 → domain.md 갱신
  (DynamoDB 플래그로 보강 A와 동시 실행 차단)
```

---

## 6. DynamoDB 플래그 스펙

### 테이블

| 항목 | 값 |
|---|---|
| 테이블명 | `wiki_enrichment_lock` |
| PK | `domain_id` (String) |
| TTL 속성 | `ttl` (Number, epoch seconds) |
| 추가 속성 | `locked_at` (ISO 타임스탬프, 디버깅용) |

### TTL 기준

| volatility | 쿨다운 |
|---|---|
| `high` (트렌드, 이벤트성) | 24시간 |
| `medium` (일반 업종) | 72시간 |
| `low` (고정 정보) | 7일 |

volatility 값은 `domain.md` frontmatter에서 읽는다. 최초 domain.md가 없는 경우 기본값 `medium` (72시간) 적용.

---

## 7. P1 구현 포인트

### DynamoDB 락 획득

```python
import boto3
import time
from datetime import datetime
from botocore.exceptions import ClientError

dynamodb = boto3.resource("dynamodb")
table = dynamodb.Table("wiki_enrichment_lock")

def try_acquire_wiki_lock(domain: str, ttl_seconds: int = 259200) -> bool:
    """
    원자적 조건부 쓰기로 잠금 획득 시도.
    성공(True) → staging 업로드 진행
    실패(False) → 스킵
    """
    try:
        table.put_item(
            Item={
                "domain_id": domain,
                "locked_at": datetime.utcnow().isoformat(),
                "ttl": int(time.time()) + ttl_seconds,
            },
            ConditionExpression="attribute_not_exists(domain_id)",
        )
        return True
    except ClientError as e:
        if e.response["Error"]["Code"] == "ConditionalCheckFailedException":
            return False
        raise
```

호출 위치: supplement 내용 생성 직후, `s3_utils.upload_supplement()` 호출 직전.

### 세션 스냅샷 (버전 불일치 방지)

P1은 LangGraph 세션 시작 노드에서 `domain.md`를 **1회만 읽어 State에 저장**한다.  
이후 모든 노드는 S3를 재호출하지 않고 State의 wiki 스냅샷을 참조한다.

```
세션 시작 노드: domain.md 읽기 → State["wiki_snapshot"] 저장
이후 모든 노드: State["wiki_snapshot"] 참조 (S3 재호출 금지)
```

세션 도중 P2가 domain.md를 업데이트해도 진행 중인 세션에는 영향이 없다.  
다음 세션 시작 시 최신 버전을 자동으로 받는다.

---

## 8. P2 변경사항

**domain.md 직접 쓰기 로직 외 변경 없음.**

- 중복 체크 로직 불필요 (P1에서 원천 차단)
- SQS FIFO 경합 관리 불필요
- staging 파일 읽기 + domain.md 섹션 병합 로직 추가
- 보강 완료 후 staging 파일 삭제

---

## 9. 요약

| 문제 | 해결 수단 | 위치 |
|---|---|---|
| 같은 업종 동시 supplement 업로드 → P2 중복 트리거 | DynamoDB 조건부 PutItem | P1 (업로드 전) |
| P2 보강 A / 보강 B 동시 쓰기 → 데이터 유실 | DynamoDB 플래그 직렬화 | P2 (쓰기 전) |
| P1이 domain.md 직접 쓰기 | staging 경로 분리 | P1 |
| 4개 소스 병합 시 데이터 유실 | H2 섹션 단위 upsert | P2 |
| 세션 도중 wiki 버전 변경 | 세션 시작 시 1회 스냅샷 | P1 |
| P1 읽기 중 P2 쓰기 → 부분 파일 반환 | S3 강한 일관성 (추가 조치 불필요) | S3 |
