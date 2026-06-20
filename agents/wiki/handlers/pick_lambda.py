"""PickDomain 람다 핸들러 (얇은 어댑터) — due()+claim() 호출만 한다.

배치(EventBridge 하루 5번)의 시작점. due-index에서 pending 우선, 없으면
만료된 done(next_refresh_at <= now)을 1개 골라 claim(찜)한다.
선택·찜 로직은 index_store(due/claim)에 있고, 여기선 호출만 — 0줄 로직.

반환:
  {"found": True,  "category": ..., "domain": ...}  → 크롤 단계로
  {"found": False}                                   → 할 것 없음 또는 동시 선점 패배

P2 메모: 선택 정책(pending→만료 done)을 core로 옮기고 싶으면 index_store에
pick() 헬퍼를 추가하고 이 핸들러는 그걸 호출만 하도록 더 얇게 만들 수 있음.
"""
from __future__ import annotations

from typing import Any

from agents.wiki.index_store import WikiIndexStore


def handler(event: dict[str, Any], context: Any = None) -> dict:
    index = WikiIndexStore()

    # pending 우선, 없으면 만료된 done (due() 내부에서 now=현재 epoch 기본값)
    candidates = index.due("pending", 1) or index.due("done", 1)
    if not candidates:
        return {"found": False}

    item = candidates[0]
    domain = item["domain"]

    # claim 실패 = 다른 실행이 먼저 선점 → 이번 회차 패스
    if not index.claim(domain):
        return {"found": False}

    return {"found": True, "category": item["category"], "domain": domain}
