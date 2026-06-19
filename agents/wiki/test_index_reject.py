"""reject() 무인 백오프 재시도 단위 테스트 (가짜 테이블, AWS 불필요).

run: PYTHONUTF8=1 py -m agents.wiki.test_index_reject
"""
from __future__ import annotations

from agents.wiki.index_store import STATUS_PENDING, WikiIndexStore, _backoff_days, _DAY


class FakeTable:
    """get_item / update_item(SET ...)만 흉내내는 최소 가짜 DDB 테이블."""

    def __init__(self, item: dict):
        self.item = dict(item)

    def get_item(self, Key):
        return {"Item": dict(self.item)} if self.item else {}

    def update_item(self, Key, UpdateExpression, ExpressionAttributeNames=None,
                    ExpressionAttributeValues=None, **kw):
        v = ExpressionAttributeValues
        self.item["status"] = v[":pending"]
        self.item["attempts"] = v[":a"]
        self.item["next_refresh_at"] = v[":nra"]
        self.item["last_updated"] = v[":lu"]
        return {"Attributes": dict(self.item)}


def check(name, cond):
    print(f"  {'OK ' if cond else 'FAIL'} {name}")
    assert cond, name


def test_backoff_schedule():
    check("백오프 1회=1일", _backoff_days(1) == 1)
    check("백오프 2회=3일", _backoff_days(2) == 3)
    check("백오프 3회=7일", _backoff_days(3) == 7)
    check("백오프 4회+=30일", _backoff_days(4) == 30 and _backoff_days(9) == 30)


def test_reject_requeues_with_backoff():
    now = 1_000_000
    table = FakeTable({"domain": "tax_accounting", "status": "crawling", "attempts": 0})
    store = WikiIndexStore(table=table)

    a1 = store.reject("tax_accounting", now=now)
    check("1차 reject: attempts=1", a1 == 1)
    check("1차 reject: status→pending(재큐잉)", table.item["status"] == STATUS_PENDING)
    check("1차 reject: next_refresh_at=now+1일", table.item["next_refresh_at"] == now + 1 * _DAY)

    a2 = store.reject("tax_accounting", now=now)
    check("2차 reject: attempts=2", a2 == 2)
    check("2차 reject: 간격 3일로 확대", table.item["next_refresh_at"] == now + 3 * _DAY)

    a3 = store.reject("tax_accounting", now=now)
    check("3차 reject: 7일", a3 == 3 and table.item["next_refresh_at"] == now + 7 * _DAY)

    a4 = store.reject("tax_accounting", now=now)
    check("4차 reject: 30일", a4 == 4 and table.item["next_refresh_at"] == now + 30 * _DAY)
    check("계속 pending 유지(포기 없음)", table.item["status"] == STATUS_PENDING)


class ConditionalCheckFailedException(Exception):
    """boto3가 조건부 쓰기 실패 시 던지는 예외 이름을 흉내."""


class CondTable:
    """ConditionExpression(CAS)을 흉내내는 가짜 테이블."""

    def __init__(self, item: dict):
        self.item = dict(item)

    def get_item(self, Key):
        return {"Item": dict(self.item)} if self.item else {}

    def update_item(self, Key, UpdateExpression, ExpressionAttributeNames=None,
                    ExpressionAttributeValues=None, ConditionExpression=None, **kw):
        if ConditionExpression:
            cur = self.item.get("latest_version")
            if "attribute_not_exists" in ConditionExpression:
                if cur is not None:
                    raise ConditionalCheckFailedException()
            elif "latest_version = :expv" in ConditionExpression:
                if cur != ExpressionAttributeValues.get(":expv"):
                    raise ConditionalCheckFailedException()
        self.item["latest_version"] = ExpressionAttributeValues.get(":lv")
        self.item["status"] = "done"
        return {"Attributes": dict(self.item)}


def test_commit_cas():
    from agents.wiki.index_store import ConcurrencyConflict

    # 기존 latest_version = v1 → expected=v1 일치 → 성공(v2)
    t = CondTable({"domain": "tax_accounting", "latest_version": "v1"})
    s = WikiIndexStore(table=t)
    s.commit("tax_accounting", latest_version="v2", confidence=0.8, source_urls=[],
             check_version=True, expected_version="v1")
    check("CAS 일치 → 성공(v2)", t.item["latest_version"] == "v2")

    # 이제 현재는 v2인데 expected=v1로 commit → 충돌
    try:
        s.commit("tax_accounting", latest_version="v3", confidence=0.8, source_urls=[],
                 check_version=True, expected_version="v1")
        check("CAS 불일치 → 충돌 예외", False)
    except ConcurrencyConflict:
        check("CAS 불일치 → 충돌 예외", True)

    # 첫 저장(latest_version 없음) + expected=None → 성공
    t2 = CondTable({"domain": "tax_accounting"})
    s2 = WikiIndexStore(table=t2)
    s2.commit("tax_accounting", latest_version="v1", confidence=0.8, source_urls=[],
              check_version=True, expected_version=None)
    check("CAS 첫저장(없을때) → 성공", t2.item["latest_version"] == "v1")

    # 이미 있는데 expected=None(첫저장 가정) → 충돌
    try:
        s2.commit("tax_accounting", latest_version="v2", confidence=0.8, source_urls=[],
                  check_version=True, expected_version=None)
        check("CAS 첫저장인데 이미 있음 → 충돌", False)
    except ConcurrencyConflict:
        check("CAS 첫저장인데 이미 있음 → 충돌", True)


if __name__ == "__main__":
    for fn in [test_backoff_schedule, test_reject_requeues_with_backoff, test_commit_cas]:
        print(f"\n[{fn.__name__}]")
        fn()
    print("\n전부 통과 ✅")
