"""generate_and_store 실패 경로가 reject로 재큐잉하는지 단위 테스트 (orphan 방지, #169).

생성실패·검수호출실패는 reject()를 불러 도메인을 pending으로 돌려야 한다(안 그러면 claim한
crawling 상태에 갇힘). 통과 경로는 reject를 부르지 않아야 한다. 가짜 함수로 AWS 불필요.

run: PYTHONUTF8=1 py -m agents.wiki.test_pipeline
"""
from __future__ import annotations

import agents.wiki.pipeline as P


class FakeGen:
    def __init__(self, ok, reason="", body="", selected=None):
        self.ok = ok
        self.reason = reason
        self.body = body
        self.selected = selected or []


class FakeRev:
    def __init__(self, ok=True, passed=True, score=0.8, reason="", gate_failed=None):
        self.ok = ok
        self.passed = passed
        self.score = score
        self.reason = reason
        self.gate_failed = gate_failed


class FakePc:
    def __init__(self, passed, violations=None, stats=None):
        self.passed = passed
        self.violations = violations or []
        self.stats = stats or {}


class FakeIndex:
    """reject 호출만 기록하는 가짜 인덱스."""

    def __init__(self):
        self.reject_calls: list[str] = []

    def reject(self, domain):
        self.reject_calls.append(domain)
        return len(self.reject_calls)


def check(name, cond):
    print(f"  {'OK ' if cond else 'FAIL'} {name}")
    assert cond, name


def _base_patch():
    """공통 가짜: get_entry·read_json. 각 테스트가 generate/review만 바꾼다."""
    P.get_entry = lambda d: {"label": "L", "category": "landing", "volatility": "mid"}
    P.read_json = lambda b, k: {"docs": [{"url": "u"}]}


def test_generate_failed_rejects():
    _base_patch()
    P.generate = lambda c, d, docs, llm=None: FakeGen(ok=False, reason="generation_truncated")
    idx = FakeIndex()
    out = P.generate_and_store("landing", "wedding_studio", "raw/k", index=idx)
    check("stage=generate_failed", out["stage"] == "generate_failed")
    check("reject 호출됨(재큐잉)", idx.reject_calls == ["wedding_studio"])
    check("attempts 반환", out.get("attempts") == 1)


def test_review_failed_rejects():
    _base_patch()
    P.generate = lambda c, d, docs, llm=None: FakeGen(ok=True, body="b", selected=[{"url": "u"}])
    P.precheck = lambda body, sel: FakePc(passed=True)
    P.review = lambda c, d, body, sel, llm=None: FakeRev(ok=False, reason="bedrock_error")
    idx = FakeIndex()
    out = P.generate_and_store("landing", "x", "raw/k", index=idx)
    check("stage=review_failed", out["stage"] == "review_failed")
    check("reject 호출됨(재큐잉)", idx.reject_calls == ["x"])


def test_precheck_failed_rejects():
    _base_patch()
    P.generate = lambda c, d, docs, llm=None: FakeGen(ok=True, body="b", selected=[{"url": "u"}])
    P.precheck = lambda body, sel: FakePc(passed=False, violations=["too_short"])
    idx = FakeIndex()
    out = P.generate_and_store("landing", "x", "raw/k", index=idx)
    check("stage=precheck_failed", out["stage"] == "precheck_failed")
    check("reject 호출됨", idx.reject_calls == ["x"])


def test_rejected_low_score():
    _base_patch()
    P.generate = lambda c, d, docs, llm=None: FakeGen(ok=True, body="b", selected=[{"url": "u"}])
    P.precheck = lambda body, sel: FakePc(passed=True)
    P.review = lambda c, d, body, sel, llm=None: FakeRev(ok=True, passed=False, score=0.5,
                                                         gate_failed=["factual_accuracy"])
    idx = FakeIndex()
    out = P.generate_and_store("landing", "x", "raw/k", index=idx)
    check("stage=rejected", out["stage"] == "rejected")
    check("reject 호출됨", idx.reject_calls == ["x"])


def test_committed_no_reject():
    _base_patch()
    P.generate = lambda c, d, docs, llm=None: FakeGen(ok=True, body="b", selected=[{"url": "u"}])
    P.precheck = lambda body, sel: FakePc(passed=True)
    P.review = lambda c, d, body, sel, llm=None: FakeRev(ok=True, passed=True, score=0.8)
    P.assemble_markdown = lambda *a, **k: "md"
    P.save_industry_versioned = lambda *a, **k: {"version_id": "v1", "bytes": 10, "committed": True}
    idx = FakeIndex()
    out = P.generate_and_store("landing", "x", "raw/k", index=idx)
    check("stage=committed", out["stage"] == "committed")
    check("통과 시 reject 호출 안 됨", idx.reject_calls == [])


if __name__ == "__main__":
    for fn in [test_generate_failed_rejects, test_review_failed_rejects,
               test_precheck_failed_rejects, test_rejected_low_score, test_committed_no_reject]:
        print(f"\n[{fn.__name__}]")
        fn()
    print("\n전부 통과 ✅")
