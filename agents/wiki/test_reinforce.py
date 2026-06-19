"""⑥ 보강 A 단위 테스트 (가짜 LLM/스토어/index, AWS·API 불필요).

run: PYTHONUTF8=1 py -m agents.wiki.test_reinforce
"""
from __future__ import annotations

import json

from agents.wiki.llm import LLMResult
from agents.wiki.index_store import ConcurrencyConflict
from agents.wiki import reinforce as rf
from agents.wiki.reinforce import parse_frontmatter, split_body_sources, reinforce
from agents.wiki.handlers import reinforce_lambda


def _review_json(scores):
    return json.dumps({"scores": scores, "notes": "t"})


GOOD = {"factual_accuracy": 5, "groundedness": 4, "coverage": 4, "domain_relevance": 5,
        "specificity": 4, "structure": 4, "neutrality": 5, "freshness": 3}      # ~0.86
MID = {**GOOD, "coverage": 3, "specificity": 3, "structure": 3, "freshness": 2}  # 낮음
GATEFAIL = {**GOOD, "factual_accuracy": 2}


class SeqLLM:
    """review 응답을 순서대로(P1 먼저, 그다음 기존, 재시도마다 기존) 반환."""
    def __init__(self, *review_scores):
        self._q = [_review_json(s) for s in review_scores]

    def complete(self, system, user, **kw):
        text = self._q.pop(0) if self._q else _review_json(GOOD)
        return LLMResult(text=text, input_tokens=5, output_tokens=5, latency_ms=1, ok=True, stop_reason="end_turn")


class FakeIndex:
    def __init__(self, latest_version="v0"):
        self._lv = latest_version

    def get(self, domain):
        return {"latest_version": self._lv} if self._lv else None


# 우리 위키 md 형식 (frontmatter + H2 + 출처). precheck 길이(400자) 넘게 본문 충분히.
def _md(extra_section=""):
    return (
        "---\ndomain: tax_accounting\ncategory: landing\nlabel: 세무/회계\nconfidence: 0.80\n---\n"
        "## 개요\n세무와 회계는 기업 및 개인의 세금 신고, 장부 작성, 재무 보고를 다루는 전문 분야로 "
        "세법과 회계기준에 따라 정확한 신고와 절세 전략 수립을 지원한다 [S1].\n\n"
        "## 핵심 개념·용어\n부가가치세, 원천징수, 종합소득세, 법인세, 세무조정 등이 핵심 용어이며 "
        "각각 과세 대상과 신고 시기가 다르게 규정되어 있다 [S1][S2].\n\n"
        "## 비즈니스 형태·서비스\n세무사 사무소는 기장 대행, 세무 조정, 세무조사 대응, 절세 컨설팅 "
        "서비스를 제공하며 월 단위 기장 계약이 일반적인 형태이다 [S2].\n\n"
        "## 타깃 고객\n소상공인, 개인사업자, 중소기업이 주요 고객층이며 창업 초기 사업자의 수요가 크다 [S1].\n\n"
        "## 차별화·경쟁 포인트\n업종 전문성과 절세 컨설팅 역량, 신속한 응대가 핵심 차별점으로 작용한다 [S2].\n\n"
        "## 규제·인증·유의사항\n세무사 자격은 국가공인이며 세무사법과 관련 법령을 준수해야 한다 [S1].\n"
        f"{extra_section}\n"
        "## 출처\n[S1] 국세청 — https://www.nts.go.kr/\n[S2] 한국세무사회 — https://www.kacta.or.kr/\n"
    )


P1_MD = _md(extra_section="\n## 트렌드·시장 동향\n전자세금계산서, 간편장부, 홈택스 기반 비대면 신고 "
            "도입이 빠르게 확산되고 있다 [S2].\n")
EXISTING_MD = _md()


def check(name, cond):
    print(f"  {'OK ' if cond else 'FAIL'} {name}")
    assert cond, name


def _setup(read_md, *, exists=True, existing_md=EXISTING_MD, conflict_times=0):
    state = {"deleted": None, "saved_md": None, "save_calls": 0, "save_kw": None}
    rf._read_pending = lambda k: read_md
    rf._delete_pending = lambda k: state.__setitem__("deleted", k)
    rf.industry_exists = lambda c, d: exists
    rf.get_industry = lambda c, d: existing_md

    def fake_save(c, d, md, **kw):
        state["save_calls"] += 1
        state["save_kw"] = kw
        if state["save_calls"] <= conflict_times:
            raise ConcurrencyConflict(d)
        state["saved_md"] = md
        return {"version_id": "v-new", "bytes": len(md), "committed": {}}

    rf.save_industry_versioned = fake_save
    return state


def test_parse():
    fm, body = parse_frontmatter(P1_MD)
    check("frontmatter domain", fm["domain"] == "tax_accounting")
    main, src = split_body_sources(P1_MD)
    check("출처 2개 파싱", len(src) == 2 and src[0]["url"].startswith("https://www.nts"))
    check("출처 등급(go.kr=high)", src[0]["source_grade"] == "high")
    check("본문에 출처섹션 없음", "## 출처" not in main)


def test_p1_wins():
    st = _setup(P1_MD)
    out = reinforce("pending/x.md", llm=SeqLLM(GOOD, MID), index=FakeIndex("v0"))
    check(f"P1 우세 → committed (stage={out['stage']})", out["adopted"] and out["stage"] == "committed")
    check("새 버전 저장됨", st["saved_md"] is not None and out["version_id"] == "v-new")
    check("저장 md frontmatter 재조립", "domain: tax_accounting" in st["saved_md"])
    check("CAS: check_version=True·expected=v0", st["save_kw"]["check_version"] and st["save_kw"]["expected_version"] == "v0")
    check("pending 삭제됨", st["deleted"] == "pending/x.md")


def test_existing_wins():
    st = _setup(P1_MD)
    out = reinforce("pending/x.md", llm=SeqLLM(MID, GOOD), index=FakeIndex("v0"))
    check(f"기존 우세 → kept_existing (stage={out['stage']})", (not out["adopted"]) and out["stage"] == "kept_existing")
    check("저장 안 함", st["saved_md"] is None and st["save_calls"] == 0)


def test_p1_gate_fail():
    st = _setup(P1_MD)
    out = reinforce("pending/x.md", llm=SeqLLM(GATEFAIL), index=FakeIndex("v0"))
    check(f"P1 게이트 미달 → 기각 (stage={out['stage']})", (not out["adopted"]) and out["stage"] == "rejected_p1_review")
    check("저장 안 함 + pending 삭제", st["save_calls"] == 0 and st["deleted"] == "pending/x.md")


def test_no_existing_adopts_p1():
    st = _setup(P1_MD, exists=False)
    out = reinforce("pending/x.md", llm=SeqLLM(GOOD), index=FakeIndex(None))
    check(f"기존 없음 + P1 통과 → committed (stage={out['stage']})", out["adopted"] and out["stage"] == "committed")
    check("CAS: 첫 저장이라 expected=None", st["save_kw"]["expected_version"] is None)


def test_cas_conflict_then_retry():
    # 첫 저장에서 동시 충돌 → 최신 재읽기·재채점·재시도 → 두 번째 성공
    st = _setup(P1_MD, conflict_times=1)
    out = reinforce("pending/x.md", llm=SeqLLM(GOOD, MID, MID), index=FakeIndex("v0"))
    check(f"충돌 후 재시도 → committed (stage={out['stage']})", out["adopted"] and out["stage"] == "committed")
    check("save 2번 호출(1충돌+1성공)", st["save_calls"] == 2)


def test_precheck_reject():
    bad = "---\ndomain: tax_accounting\ncategory: landing\n---\n## 개요\n짧음\n## 출처\n[S1] x — https://x\n"
    st = _setup(bad)
    out = reinforce("pending/x.md", llm=SeqLLM(GOOD), index=FakeIndex("v0"))
    check(f"precheck 실패 → 기각 (stage={out['stage']})", (not out["adopted"]) and out["stage"] == "rejected_precheck")
    check("LLM 안 부르고 기각", st["save_calls"] == 0)


def test_identical_reject():
    st = _setup(EXISTING_MD)  # P1이 기존과 동일
    out = reinforce("pending/x.md", llm=SeqLLM(GOOD), index=FakeIndex("v0"))
    check(f"기존과 동일 → 기각 (stage={out['stage']})", out["stage"] == "rejected_no_change")


def test_bad_domain():
    bad = "---\ndomain: not_a_domain\ncategory: landing\n---\n## 개요\n내용\n"
    st = _setup(bad)
    out = reinforce("pending/x.md", llm=SeqLLM(GOOD), index=FakeIndex("v0"))
    check(f"잘못된 도메인 → 기각 (stage={out['stage']})", out["stage"] == "rejected_bad_domain")


def test_handler_extract_key():
    captured = {}
    reinforce_lambda.reinforce = lambda k: captured.update(key=k) or {"stage": "committed"}
    reinforce_lambda.handler({"Records": [{"s3": {"object": {"key": "pending/a%2Fb.md"}}}]})
    check("S3 이벤트 키 추출(+unquote)", captured["key"] == "pending/a/b.md")
    reinforce_lambda.handler({"Records": [{"body": json.dumps({"key": "pending/c.md"})}]})
    check("SQS {key} 추출", captured["key"] == "pending/c.md")
    wrapped = json.dumps({"Records": [{"s3": {"object": {"key": "pending/d.md"}}}]})
    reinforce_lambda.handler({"Records": [{"body": wrapped}]})
    check("S3-in-SQS 키 추출", captured["key"] == "pending/d.md")


if __name__ == "__main__":
    for fn in [test_parse, test_p1_wins, test_existing_wins, test_p1_gate_fail,
               test_no_existing_adopts_p1, test_cas_conflict_then_retry, test_precheck_reject,
               test_identical_reject, test_bad_domain, test_handler_extract_key]:
        print(f"\n[{fn.__name__}]")
        fn()
    print("\n전부 통과 ✅")
