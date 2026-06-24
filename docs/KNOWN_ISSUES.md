# Known Issues

발견된 버그·불일치 목록. 수정 전까지 트래킹용.

---

## ISSUE-001: `wine-market` 하이픈 도메인 → reinforce Lambda `rejected_bad_domain`

**발견일:** 2026-06-24  
**심각도:** Medium (데이터 손실, 기능 마비는 아님)  
**상태:** 미수정

### 현상

hezo-wiki-reinforce Lambda가 `wine-market` (하이픈) 도메인을 인식 못하고 거부:

```
stage=rejected_bad_domain  adopted=False  domain=wine-market
reason=unknown domain: 'wine-market'
```

24시간 기준 약 15회 이상 반복 거부 확인.

### 원인

**구 P1 이미지 (PR #213 이전)** 가 `hezo-wiki-staging/pending/` 파일명에 `wine-market` (하이픈)을 그대로 사용:

```
wine-market_{site_id}.md  ← Lambda가 도메인 키로 파일명 파싱 → 인식 실패
```

reinforce Lambda 내부 도메인 허용 목록이 `wine_market` (언더스코어)만 등록되어 있어 하이픈 형태를 알 수 없는 도메인으로 처리.

**PR #213 이후 신규 이미지는 `wine_market` (언더스코어) 저장 → Lambda 정상 커밋 (score=0.824, adopted=True) ✅**

### 영향 범위

- 구 이미지가 생성한 `wine-market_*.md` 파일들이 staging에 잔존 (Lambda가 처리 후 삭제하는지 확인 필요)
- `tax-accounting`, `beauty-salon` 등 하이픈 도메인 전체 동일 문제 가능성

### 수정 방향

A. reinforce Lambda 도메인 정규화 추가 — 파일명 파싱 시 하이픈→언더스코어 변환 후 매칭  
B. staging에 남은 `wine-market_*.md` 파일 수동 삭제 또는 재저장

---

## ISSUE-002: `graph_smoke` 경로에서 `source_s3_key` 하이픈/언더스코어 불일치

**발견일:** 2026-06-24  
**심각도:** Low (`graph_smoke`는 프로덕션 경로 아님, 테스트/진단 경로만 영향)  
**상태:** 미수정

### 현상

`action = "graph_smoke"` (기본값) 로 chat agent 호출 시 400 에러:

```
ValueError: artifact_not_found
  File chat_graph.py line 191, p2_markdown_load_node
  → hezo-wiki/industries/store/wine-market.md (NoSuchKey)
     실제 파일명: wine_market.md (언더스코어)
```

### 원인

`chat_http_handler.py` → `_run_graph_smoke()` → `ChatGraphState` 초기화 시:

```python
# chat_http_handler.py
p2_source_s3_key=_optional_text(session_attrs.get("source_s3_key")),
# session_attrs에 source_s3_key 없으면 None
```

`chat_graph.py` → `_p2_source_s3_key(state)`:

```python
def _p2_source_s3_key(state: ChatGraphState) -> str:
    if state.p2_source_s3_key and state.p2_source_s3_key.strip():
        return state.p2_source_s3_key.strip()
    return f"industries/{state.category}/{state.domain}.md"
    # domain = "wine-market" → "industries/store/wine-market.md" (존재하지 않음)
```

이미 `_wiki_s3_key(domain, template_id)` 함수가 정규화를 수행하지만 이 fallback에서는 사용 안 함.

### 영향 범위

- 프로덕션 chat 경로(`action = "chat_turn"`)는 `_run_chat_turn()` → `_load_wiki_content()` 경유로 **영향 없음**
- `graph_smoke` 경로 (테스트/진단용) 만 영향

### 수정 방향

`_run_graph_smoke()` 또는 `_p2_source_s3_key()` 에서 `_wiki_s3_key(domain, template_id)` 를 fallback으로 사용:

```python
# _run_graph_smoke() 수정 예시
_domain = str(session_attrs.get("domain", DEFAULT_DOMAIN))
_tmpl = str(session_attrs.get("selected_template", DEFAULT_TEMPLATE))
p2_source_s3_key=_optional_text(session_attrs.get("source_s3_key")) or _wiki_s3_key(_domain, _tmpl),
```
