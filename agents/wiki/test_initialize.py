"""④ 초기화 단위 테스트 (가짜 index/collect/pipeline, AWS·API 불필요).

run: PYTHONUTF8=1 py -m agents.wiki.test_initialize
"""
from __future__ import annotations

from agents.wiki import catalog
from agents.wiki import initialize as init_mod
from agents.wiki.initialize import register_catalog, bootstrap_seeds


class FakeIndex:
    """get/register만 흉내내는 가짜 DDB 색인. items에 등록된 도메인 보관."""

    def __init__(self, existing: set[str] | None = None):
        self.items = dict.fromkeys(existing or [], {"status": "done"})
        self.registered: list[str] = []

    def get(self, domain):
        return self.items.get(domain)

    def register(self, domain, status="pending"):
        self.items[domain] = {"status": status}
        self.registered.append(domain)
        return self.items[domain]


def check(name, cond):
    print(f"  {'OK ' if cond else 'FAIL'} {name}")
    assert cond, name


def test_register_all_empty():
    idx = FakeIndex()
    out = register_catalog(index=idx)
    check(f"빈 DDB → 60개 등록 (registered={out['registered']})", out["registered"] == 60)
    check("skip 0", out["skipped"] == 0)
    check("실제 register 호출 60회", len(idx.registered) == 60)


def test_register_idempotent():
    # 시드 3개가 이미 done 상태로 존재 → skip 되어야(덮어쓰지 않음)
    seeds = set(catalog.seed_domains())
    idx = FakeIndex(existing=seeds)
    out = register_catalog(index=idx)
    check(f"기존 3개 skip (skipped={out['skipped']})", out["skipped"] == 3)
    check(f"나머지 57개만 등록 (registered={out['registered']})", out["registered"] == 57)
    check("기존 done 상태 보존(register 안 호출)", all(s not in idx.registered for s in seeds))


def test_bootstrap_seeds():
    # collect / generate_and_store 를 가짜로 치환 → 실제 API 없이 흐름 검증
    calls = []

    def fake_collect(category, domain, **kw):
        calls.append(("collect", category, domain))
        return {"raw_key": f"raw/{category}/{domain}/d.json", "kept": 5}

    def fake_gas(category, domain, raw_key, **kw):
        calls.append(("gas", category, domain, raw_key))
        return {"stage": "committed", "passed": True, "score": 0.81}

    init_mod.collect = fake_collect
    init_mod.generate_and_store = fake_gas

    results = bootstrap_seeds(index=FakeIndex())
    check(f"시드 3개 처리 (results={len(results)})", len(results) == 3)
    check("collect→gas 각 3회", sum(1 for c in calls if c[0] == "collect") == 3
          and sum(1 for c in calls if c[0] == "gas") == 3)
    check("각 도메인 카테고리 일치", all(r["category"] == catalog.get_entry(r["domain"])["category"]
                                    for r in results))
    check("gas가 collect의 raw_key를 받음",
          all(c[3] == f"raw/{c[1]}/{c[2]}/d.json" for c in calls if c[0] == "gas"))
    check("결과 stage=committed", all(r["result"]["stage"] == "committed" for r in results))


if __name__ == "__main__":
    for fn in [test_register_all_empty, test_register_idempotent, test_bootstrap_seeds]:
        print(f"\n[{fn.__name__}]")
        fn()
    print("\n전부 통과 ✅")
