"""⑤b 생성·검수·저장 단위 테스트 (가짜 LLM/스토어, boto3·AWS 불필요).

run: PYTHONUTF8=1 py -m pytest agents/wiki/test_generate_review.py -q
(pytest 없으면 이 파일을 직접 실행: py agents/wiki/test_generate_review.py)
"""
from __future__ import annotations

import json
import re

from agents.wiki.llm import LLMResult
from agents.wiki import pipeline as pipe_mod
from agents.wiki.generate import assemble_markdown, generate, select_sources
from agents.wiki.review import compute, review
from agents.wiki.precheck import precheck


# ─── 가짜 LLM: system에 "검수자"가 있으면 검수, 아니면 생성 ──────────────────
class FakeLLM:
    def __init__(self, body: str, review_scores: dict, notes: str = "ok", stop_reason: str = "end_turn"):
        self._body = body
        self._review = json.dumps({"scores": review_scores, "notes": notes})
        self._stop = stop_reason
        self.calls: list[str] = []

    def complete(self, system, user, **kw):
        is_review = "검수자" in system
        self.calls.append("review" if is_review else "generate")
        text = self._review if is_review else self._body
        # 검수 콜은 항상 정상 종료, 생성 콜만 stop_reason 적용(잘림 테스트용)
        stop = "end_turn" if is_review else self._stop
        return LLMResult(text=text, input_tokens=10, output_tokens=20, latency_ms=5, ok=True, stop_reason=stop)


GOOD_SCORES = {
    "factual_accuracy": 5, "groundedness": 4, "coverage": 4, "domain_relevance": 5,
    "specificity": 4, "structure": 4, "neutrality": 5, "freshness": 3,
}
GATE_FAIL_SCORES = {**GOOD_SCORES, "factual_accuracy": 2}   # 게이트 미달
LOW_SCORES = {k: 2 for k in GOOD_SCORES}                    # 컷 미달

BODY = (
    "## 개요\n세무/회계는 기업과 개인의 세금 신고, 장부 작성, 재무 보고를 다루는 전문 분야이며, "
    "세법과 회계기준에 따라 정확한 신고와 절세 전략 수립을 지원한다 [S1].\n\n"
    "## 핵심 개념·용어\n부가가치세, 원천징수, 종합소득세, 법인세, 세무조정 등이 핵심 용어이며 "
    "각각 과세 대상과 신고 시기가 다르다 [S1][S2].\n\n"
    "## 비즈니스 형태·서비스\n세무사 사무소는 기장 대행, 세무 조정, 세무조사 대응, 절세 컨설팅 "
    "서비스를 제공하며 월 단위 기장 계약이 일반적이다 [S2].\n\n"
    "## 타깃 고객\n소상공인, 개인사업자, 중소기업이 주요 고객층이며 창업 초기 사업자의 수요가 크다 [S1].\n\n"
    "## 차별화·경쟁 포인트\n업종 전문성과 절세 컨설팅 역량, 신속한 응대가 핵심 차별점으로 작용한다 [S2].\n\n"
    "## 규제·인증·유의사항\n세무사 자격은 국가공인이며 세무사법과 관련 법령을 준수해야 한다 [S1].\n\n"
    "## 트렌드·시장 동향\n전자세금계산서, 간편장부, 홈택스 기반 비대면 신고 도입이 빠르게 확산되고 있다 [S2].\n"
)

DOCS = [
    {"url": "https://www.nts.go.kr/a", "title": "국세청", "text": "세무 본문 " * 30, "ok": True, "source_grade": "high"},
    {"url": "https://blog.naver.com/x", "title": "블로그", "text": "후기 " * 30, "ok": True, "source_grade": "low"},
    {"url": "https://bad", "title": "실패", "text": "", "ok": False, "source_grade": "low"},
]


def check(name, cond):
    print(f"  {'OK ' if cond else 'FAIL'} {name}")
    assert cond, name


def test_select_sources():
    sel = select_sources(DOCS)
    check("select: ok·본문 있는 것만(2개)", len(sel) == 2)
    check("select: high가 먼저", sel[0]["source_grade"] == "high")


def test_compute():
    s1, p1, g1 = compute(GOOD_SCORES)
    check(f"compute: good 합격 (score={s1})", p1 and not g1 and s1 >= 0.70)
    s2, p2, g2 = compute(GATE_FAIL_SCORES)
    check("compute: 게이트 미달이면 불합격", (not p2) and "factual_accuracy" in g2)
    s3, p3, _ = compute(LOW_SCORES)
    check(f"compute: 컷 미달 불합격 (score={s3})", (not p3) and s3 < 0.70)


def test_assemble_parseable():
    md = assemble_markdown("tax_accounting", "landing", "세무/회계", BODY, select_sources(DOCS), confidence=0.82)
    check("assemble: frontmatter domain", "domain: tax_accounting" in md)
    check("assemble: frontmatter category", "category: landing" in md)
    check("assemble: confidence(정규식 매칭)", re.search(r"confidence\s*[:=]\s*0\.82", md) is not None)
    check("assemble: ## 출처 섹션", "## 출처" in md)
    check("assemble: [S1] 근거 라인", re.search(r"\[S1\].*nts\.go\.kr", md) is not None)
    check("assemble: H2 지식 섹션 존재", "## 개요" in md)

    # 모델이 본문에 직접 쓴 '## 출처'는 strip되고 정본만 남아야 (중복 방지)
    dup = assemble_markdown("tax_accounting", "landing", "세무",
                            BODY + "\n## 출처\n[S1] 모델이 직접 쓴 출처\n",
                            select_sources(DOCS), confidence=0.8)
    check("assemble: 모델 작성 '## 출처' strip → 1개만", dup.count("## 출처") == 1)
    check("assemble: 모델 출처 내용 제거됨", "모델이 직접 쓴 출처" not in dup)


def test_generate():
    llm = FakeLLM(BODY, GOOD_SCORES)
    g = generate("landing", "tax_accounting", DOCS, llm=llm)
    check("generate: ok", g.ok and g.body.startswith("## 개요"))
    check("generate: selected 2개", len(g.selected) == 2)
    g2 = generate("landing", "tax_accounting", [{"ok": False}], llm=llm)
    check("generate: 사용가능 출처 없으면 실패", (not g2.ok) and g2.reason == "no_usable_sources")
    # 토큰 한도로 잘리면(stopReason=max_tokens) 실패 처리
    gtrunc = generate("landing", "tax_accounting", DOCS, llm=FakeLLM(BODY, GOOD_SCORES, stop_reason="max_tokens"))
    check("generate: 잘림(max_tokens) → generation_truncated", (not gtrunc.ok) and gtrunc.reason == "generation_truncated")


def test_review():
    r = review("landing", "tax_accounting", BODY, select_sources(DOCS), llm=FakeLLM(BODY, GOOD_SCORES))
    check(f"review: 합격 (score={r.score})", r.ok and r.passed)
    rg = review("landing", "tax_accounting", BODY, select_sources(DOCS), llm=FakeLLM(BODY, GATE_FAIL_SCORES))
    check("review: 게이트 미달 불합격", rg.ok and not rg.passed and rg.gate_failed)


def test_precheck():
    sel = select_sources(DOCS)  # 2개 출처
    ok = precheck(BODY, sel)
    check(f"precheck: 정상 본문 통과 (stats={ok.stats})", ok.passed)

    short = precheck("## 개요\n짧음 [S1]\n## 핵심\n또 [S1]\n", sel)
    check("precheck: 섹션/길이 부족 위반", (not short.passed) and short.violations)

    oor = precheck(BODY + "\n## 추가\n범위 밖 인용 [S9]\n", sel)
    check("precheck: 출처 범위 밖 인용([S9]) 차단", (not oor.passed)
          and any("citation_out_of_range" in v for v in oor.violations))

    ad = precheck(BODY + "\n지금 전화 주세요!\n", sel)
    check("precheck: 광고 금지어 차단", (not ad.passed)
          and any("banned_phrases" in v for v in ad.violations))

    # '할인마트'(소매 업태=정상 지식)는 '할인' 때문에 차단되면 안 됨 (#오탐 방지)
    fp = precheck(BODY + "\n편의점·대형할인마트도 유통 채널이다 [S1]\n", sel)
    check("precheck: '할인마트' 오탐 안 함(통과)", fp.passed)
    # 판촉 문구는 계속 차단
    promo = precheck(BODY + "\n지금 할인 이벤트 중! [S1]\n", sel)
    check("precheck: 판촉 문구(할인 이벤트) 차단", (not promo.passed)
          and any("banned_phrases" in v for v in promo.violations))


def test_pipeline(monkeypatch_like=None):
    # read_json / save_industry_versioned 를 가짜로 치환
    saved_holder = {}

    def fake_read_json(bucket, key):
        return {"docs": DOCS}

    def fake_save(category, domain, md, *, confidence, source_urls, index=None):
        saved_holder.update({"md": md, "confidence": confidence, "source_urls": source_urls})
        return {"version_id": "v-test", "bytes": len(md.encode()), "committed": {"status": "done"}}

    class FakeIndex:
        def __init__(self): self.rejected = 0
        def reject(self, domain): self.rejected += 1; return self.rejected

    pipe_mod.read_json = fake_read_json
    pipe_mod.save_industry_versioned = fake_save

    # 통과 경로
    idx = FakeIndex()
    out = pipe_mod.generate_and_store("landing", "tax_accounting", "raw/k.json",
                                      llm=FakeLLM(BODY, GOOD_SCORES), index=idx)
    check(f"pipeline: 통과→committed (stage={out['stage']})", out["passed"] and out["stage"] == "committed")
    check("pipeline: version_id 반환", out["version_id"] == "v-test")
    check("pipeline: 저장 md에 frontmatter", "domain: tax_accounting" in saved_holder["md"])
    check("pipeline: confidence=검수점수", abs(saved_holder["confidence"] - out["score"]) < 1e-9)

    # 미달 경로
    idx2 = FakeIndex()
    out2 = pipe_mod.generate_and_store("landing", "tax_accounting", "raw/k.json",
                                       llm=FakeLLM(BODY, LOW_SCORES), index=idx2)
    check(f"pipeline: 미달→rejected (stage={out2['stage']})", (not out2["passed"]) and out2["stage"] == "rejected")
    check("pipeline: reject 호출됨", idx2.rejected == 1)

    # precheck 실패 경로 (LLM 채점 전 룰에서 거부)
    idx3 = FakeIndex()
    out3 = pipe_mod.generate_and_store("landing", "tax_accounting", "raw/k.json",
                                       llm=FakeLLM("## 개요\n너무 짧음 [S1]\n", GOOD_SCORES), index=idx3)
    check(f"pipeline: precheck 실패→거부 (stage={out3['stage']})",
          (not out3["passed"]) and out3["stage"] == "precheck_failed")
    check("pipeline: precheck 위반 목록 반환", bool(out3["violations"]) and idx3.rejected == 1)


def test_handler():
    # 람다 핸들러(얇은 어댑터)가 이벤트 필드를 추출해 generate_and_store로 위임하는지
    from agents.wiki.handlers import generate_lambda
    captured = {}

    def fake_gas(category, domain, raw_key, **kw):
        captured.update(category=category, domain=domain, raw_key=raw_key)
        return {"stage": "committed", "passed": True}

    generate_lambda.generate_and_store = fake_gas
    event = {"category": "landing", "domain": "tax_accounting", "raw_key": "raw/landing/tax_accounting/d.json"}
    out = generate_lambda.handler(event)
    check("handler: 이벤트 필드 추출·위임", captured == event)
    check("handler: 결과 그대로 반환", out["passed"] and out["stage"] == "committed")


if __name__ == "__main__":
    for fn in [test_select_sources, test_compute, test_assemble_parseable,
               test_generate, test_review, test_precheck, test_pipeline, test_handler]:
        print(f"\n[{fn.__name__}]")
        fn()
    print("\n전부 통과 ✅")
