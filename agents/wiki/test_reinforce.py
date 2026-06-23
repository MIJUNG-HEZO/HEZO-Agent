"""⑥ 보강 A 단위 테스트 (가짜 LLM/스토어/index, AWS·API 불필요).

#206 이후: 기존은 재채점하지 않고 DDB 저장점수로 비교 + 축소 가드(섹션·길이).

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
MID = {**GOOD, "coverage": 3, "specificity": 3, "structure": 3, "freshness": 2}  # ~0.77
GATEFAIL = {**GOOD, "factual_accuracy": 2}                                       # 사실 게이트 미달


class SeqLLM:
    """review 응답을 순서대로 반환. #206 이후 reinforce는 P1만 채점(기존은 저장점수)."""
    def __init__(self, *review_scores):
        self._q = [_review_json(s) for s in review_scores]

    def complete(self, system, user, **kw):
        text = self._q.pop(0) if self._q else _review_json(GOOD)
        return LLMResult(text=text, input_tokens=5, output_tokens=5, latency_ms=1, ok=True, stop_reason="end_turn")


class FakeIndex:
    """get(domain) → DDB 메타(latest_version + confidence=저장점수). lv 없으면 None."""
    def __init__(self, latest_version="v0", confidence=0.80):
        self._lv = latest_version
        self._conf = confidence

    def get(self, domain):
        return {"latest_version": self._lv, "confidence": self._conf} if self._lv else None


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
            "도입이 빠르게 확산되고 있다 [S2].\n")  # 7섹션(확장)
EXISTING_MD = _md()  # 6섹션

# 룰(5섹션·3인용·400자)은 통과하지만 기존(6섹션)보다 섹션이 적은 '축소' 보강
SMALL_MD = (
    "---\ndomain: tax_accounting\ncategory: landing\nlabel: 세무/회계\nconfidence: 0.80\n---\n"
    "## 개요\n세무와 회계는 기업과 개인의 세금 신고, 장부 작성, 재무 보고를 다루는 전문 분야로, "
    "세법과 회계기준에 따라 정확한 신고와 합법적 절세 전략 수립을 종합적으로 지원하는 서비스다 [S1].\n\n"
    "## 핵심 용어\n부가가치세, 원천징수, 종합소득세, 법인세, 세무조정 등이 핵심 용어이며 각각 "
    "과세 대상과 신고 시기, 세율 체계가 다르게 규정되어 실무에서 구분이 매우 중요하다 [S1][S2].\n\n"
    "## 서비스\n세무사 사무소는 기장 대행, 세무 조정, 세무조사 대응, 절세 컨설팅 서비스를 "
    "월 단위 기장 계약 형태로 제공하며 업종별 맞춤 자문을 함께 수행한다 [S2].\n\n"
    "## 고객\n소상공인, 개인사업자, 중소기업이 주요 고객층이며 창업 초기 사업자의 세무 대행 "
    "수요가 특히 크고 업종 전환·확장 시점에도 자문 수요가 발생한다 [S3].\n\n"
    "## 규제\n세무사 자격은 국가공인 자격이며 세무사법과 관련 법령, 직업윤리 규정 및 "
    "수임 제한 규정을 철저히 준수해야 하고 위반 시 징계 대상이 된다 [S3].\n"
    "## 출처\n[S1] 국세청 — https://www.nts.go.kr/\n[S2] 한국세무사회 — https://www.kacta.or.kr/\n"
    "[S3] 기획재정부 — https://www.moef.go.kr/\n"
)  # 5섹션


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
    # P1(0.86, 7섹션·확장) > 기존 저장점수(0.80), 축소 아님 → 채택
    st = _setup(P1_MD)
    out = reinforce("pending/x.md", llm=SeqLLM(GOOD), index=FakeIndex("v0", confidence=0.80))
    check(f"P1 우세+확장 → committed (stage={out['stage']})", out["adopted"] and out["stage"] == "committed")
    check("새 버전 저장됨", st["saved_md"] is not None and out["version_id"] == "v-new")
    check("저장 md frontmatter 재조립", "domain: tax_accounting" in st["saved_md"])
    check("CAS: check_version=True·expected=v0", st["save_kw"]["check_version"] and st["save_kw"]["expected_version"] == "v0")
    check("pending 삭제됨", st["deleted"] == "pending/x.md")


def test_low_score_kept():
    # P1(0.77) < 80%*기존저장(0.98)=0.784 → 점수 80% 미달 → 기존 유지
    st = _setup(P1_MD)
    out = reinforce("pending/x.md", llm=SeqLLM(MID), index=FakeIndex("v0", confidence=0.98))
    check(f"P1 점수 80% 미달 → kept_existing (stage={out['stage']})", (not out["adopted"]) and out["stage"] == "kept_existing")
    check("저장 안 함", st["saved_md"] is None and st["save_calls"] == 0)


def test_shrink_guard():
    # ★#206 핵심: P1이 고점수(0.86)여도 기존보다 섹션 적으면(5<6) 축소로 거부
    st = _setup(SMALL_MD)
    out = reinforce("pending/x.md", llm=SeqLLM(GOOD), index=FakeIndex("v0", confidence=0.80))
    check(f"고점수여도 축소 → kept_existing_shrink (stage={out['stage']})",
          (not out["adopted"]) and out["stage"] == "kept_existing_shrink")
    check("저장 안 함 (빈약한 게 풍부한 원본 못 덮음)", st["save_calls"] == 0)
    check("사유에 shrink 표기", "shrink" in out["reason"])


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
    # 첫 저장에서 동시 충돌 → 최신 재읽기·재시도 → 두 번째 성공 (P1은 한 번만 채점)
    st = _setup(P1_MD, conflict_times=1)
    out = reinforce("pending/x.md", llm=SeqLLM(GOOD), index=FakeIndex("v0", confidence=0.80))
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
    out = reinforce("pending/x.md", llm=SeqLLM(GOOD), index=FakeIndex("v0", confidence=0.80))
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
    for fn in [test_parse, test_p1_wins, test_low_score_kept, test_shrink_guard, test_p1_gate_fail,
               test_no_existing_adopts_p1, test_cas_conflict_then_retry, test_precheck_reject,
               test_identical_reject, test_bad_domain, test_handler_extract_key]:
        print(f"\n[{fn.__name__}]")
        fn()
    print("\n전부 통과 ✅")
