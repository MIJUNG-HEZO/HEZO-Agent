"""source_grade 권위 등급 + collect 권위>언어 오버라이드 단위 테스트 (AWS 불필요).

- source_grade: 권위 화이트리스트(전문·글로벌)·정부=high, 위키=mid, 그 외=low.
- collect: high 등급은 길이만 충족하면 한국어 미달이어도 ok(영어 권위 허용). 쓰레기는 길이로 거름.

run: PYTHONUTF8=1 py -m agents.wiki.test_collect
"""
from __future__ import annotations

import agents.wiki.collect as C
import agents.wiki.search as S
from agents.wiki.fetch import MIN_LEN
from agents.wiki.search import search_queries, search_sources, source_grade


def check(name, cond):
    print(f"  {'OK ' if cond else 'FAIL'} {name}")
    assert cond, name


def test_source_grade():
    check("정부 go.kr → high", source_grade("https://www.nts.go.kr/x") == "high")
    check("공공 or.kr → high", source_grade("https://www.at.or.kr/x") == "high")
    check("학술 ac.kr → high", source_grade("https://www.snu.ac.kr/x") == "high")
    check("연구 re.kr → high", source_grade("https://eiec.kdi.re.kr/x") == "high")
    check("전문매체(wine21) → high", source_grade("https://www.wine21.com/x") == "high")
    check("글로벌 권위(winespectator) → high", source_grade("https://www.winespectator.com/x") == "high")
    check("서브도메인(developer.apple.com) → high", source_grade("https://developer.apple.com/x") == "high")
    check("위키 → mid", source_grade("https://ko.wikipedia.org/x") == "mid")
    check("개인 블로그 → low", source_grade("https://someone.tistory.com/x") == "low")
    check("오매칭 방지: img2.com ≠ g2.com → low", source_grade("https://img2.com/x") == "low")


def test_collect_authority_over_language():
    sources = [
        {"url": "https://decanter.com/a", "title": "", "snippet": "", "source_grade": "high"},
        {"url": "https://shopblog.com/b", "title": "", "snippet": "", "source_grade": "low"},
        {"url": "https://oiv.int/c", "title": "", "snippet": "", "source_grade": "high"},
    ]
    # 셋 다 한국어 비율 미달(영어). high 2개 중 1개는 길이 충족, 1개는 너무 짧음.
    docs = [
        {"url": "https://decanter.com/a", "ok": False, "needs_headless": True, "chars": MIN_LEN + 50, "korean_ratio": 0.0},
        {"url": "https://shopblog.com/b", "ok": False, "needs_headless": True, "chars": MIN_LEN + 50, "korean_ratio": 0.0},
        {"url": "https://oiv.int/c", "ok": False, "needs_headless": True, "chars": MIN_LEN - 10, "korean_ratio": 0.0},
    ]
    captured: dict = {}
    C.search_sources = lambda domain, num=20: sources
    C.fetch_many = lambda urls: [dict(d) for d in docs]
    C.save_raw = lambda category, domain, docs, date=None: (captured.update(docs=docs) or "raw/x.json")

    out = C.collect("store", "wine_market")
    saved = {d["url"]: d for d in captured["docs"]}

    check("high+길이충족 → 권위 오버라이드로 ok", saved["https://decanter.com/a"]["ok"] is True)
    check("  needs_headless 해제됨", saved["https://decanter.com/a"]["needs_headless"] is False)
    check("low → 한국어 미달 그대로 not ok", saved["https://shopblog.com/b"]["ok"] is False)
    check("high지만 길이 미달 → 오버라이드 안 됨(쓰레기 가드)", saved["https://oiv.int/c"]["ok"] is False)
    check("kept=1 (decanter만)", out["kept"] == 1)


def test_search_queries():
    check("override 리스트(와인 멀티쿼리)",
          search_queries("wine_market") == ["와인 시장 동향", "와인 품종별 특징"])
    check("override 리스트(커리어 멀티쿼리)", search_queries("career") == ["커리어 성장", "직무 정보"])
    check("override 없으면 라벨(세무)", search_queries("tax_accounting") == ["세무"])


def test_search_sources_multiquery_dedupe():
    calls: list[str] = []

    def fake(q, num):
        calls.append(q)
        # 검색어별 고유 1개 + 공통 1개(중복) 반환
        return {"organic": [
            {"link": f"https://uniq.com/{len(calls)}", "title": "", "snippet": ""},
            {"link": "https://dup.com/a", "title": "", "snippet": ""},
        ]}

    S._call_serper = fake
    out = search_sources("wine_market", num=5)  # 멀티쿼리 2개
    check("검색어 2번 호출됨", len(calls) == 2)
    check("중복 URL 1개로 합쳐짐", sum(1 for d in out if d["url"] == "https://dup.com/a") == 1)
    check("유니크 = 2고유 + 1공통 = 3", len(out) == 3)


if __name__ == "__main__":
    for fn in [test_source_grade, test_collect_authority_over_language,
               test_search_queries, test_search_sources_multiquery_dedupe]:
        print(f"\n[{fn.__name__}]")
        fn()
    print("\n전부 통과 ✅")
