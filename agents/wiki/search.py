"""HEZO Wiki (P2) 출처 검색 — Serper.dev (Google 검색결과) (순수 로직, 런타임 무관).

도메인 → 신뢰 출처 URL 목록. 람다/ECS 어디서든 호출 가능한 순수 함수.
키는 환경변수(SERPER_API_KEY)로만 — 코드/깃에 두지 않는다.

검색 제공자는 search.py에만 갇혀 있다(로직 분리). 제공자를 바꾸려면 _call_serper만
다른 함수(Tavily/네이버/CSE 화이트리스트 등)로 갈아끼우면 되고, fetch/collect는 그대로다.
(CSE 전체 웹 검색은 2026-03 구글 정책으로 신규 폐지 → Serper.dev로 구글 결과를 받는다)

출처 등급(source_grade): 권위 기준(언어 아님). 정부·공공(.go.kr/.or.kr)=high,
권위 화이트리스트(_HIGH_HOSTS: 전문매체·글로벌 기관, 영어 포함)=high, 신뢰 위키=mid, 그 외=low.
규제 도메인은 정부(go.kr)가 이미 high라 커버되고, 커머스·라이프(예: 와인)는 비정부 권위가
화이트리스트로 high가 된다. 검색이 도메인별이라 호스트가 무관 도메인에 새지 않는다
(예: wine21은 와인 검색에만 등장) → 플랫 화이트리스트로 충분. 도메인 늘며 확장.
"""
from __future__ import annotations

import os
from urllib.parse import urlparse

from agents.wiki.catalog import get_entry

SERPER_ENDPOINT = "https://google.serper.dev/search"

# 권위 화이트리스트 — 비정부 전문매체·협회·글로벌 기관(언어 무관). 호스트로 high 부여.
# 60개 도메인별로 웹 검색으로 실존·권위 확인한 출처(2026-06-22). 정부·공공
# (.go.kr/.or.kr/.ac.kr/.re.kr)은 아래 규칙에서 자동 high라 여기 미포함.
# 검색이 도메인별이라 호스트가 무관 도메인에 새지 않는다(플랫 목록으로 충분).
_HIGH_HOSTS = (
    # landing
    "byline.network", "outstanding.kr", "g2.com", "saastr.com", "motorgraph.com",
    "hanok.org", "lawtimes.co.kr", "nscakorea.com", "wedding21.co.kr", "britishcouncil.kr",
    "interiorskorea.com", "joseilbo.com", "kopia.asia", "industrynews.co.kr",
    "developer.android.com", "developer.apple.com", "support.google.com",
    "dailyvet.co.kr", "month.foodbank.co.kr",
    # blog
    "guide.michelin.com", "developer.mozilla.org", "docs.python.org", "hbr.org",
    "hankyung.com", "10000recipe.com", "lonelyplanet.com", "mayoclinic.org",
    "health.harvard.edu", "designboom.com", "dezeen.com", "moma.org", "healthychildren.org",
    "stratechery.com", "allure.com", "data.kbland.kr", "pitchfork.com", "rollingstone.com",
    "acsm.org", "ch.yes24.com", "changbi.com", "magnumphotos.com", "bluer.co.kr",
    # store
    "sca.coffee", "kca-coffee.org", "fashionbiz.co.kr", "koreafashion.org",
    "thecreatoreconomy.com", "phocuswire.com", "hospitalitytech.com", "nailholic.co.kr",
    "cmn.co.kr", "nongmin.com", "agrinet.co.kr", "sneakernews.com", "hypebeast.com",
    "soleretriever.com", "winereview.co.kr", "wine21.com", "winespectator.com",
    "kofda.net", "akppe.org", "thinkfood.co.kr", "zdnet.co.kr", "theverge.com",
    "engadget.com", "publishersweekly.com", "kirkusreviews.com", "toyassociation.org",
    "outdoornews.co.kr", "outdoorgearlab.com", "backpacker.com", "gia.edu",
    "tea.co.uk", "worldteanews.com",
)
_MID_HOSTS = ("wikipedia.org", "namu.wiki")


def _host_matches(host: str, hosts: tuple[str, ...]) -> bool:
    """호스트 정확 매칭 — host가 목록의 항목과 같거나 그 서브도메인일 때만 True.
    (substring 매칭은 'g2.com'이 'img2.com'에 걸리는 등 오매칭 위험이 있어 사용 안 함)."""
    return any(host == h or host.endswith("." + h) for h in hosts)


def build_query(domain: str) -> str:
    """기본 검색어 = 도메인 라벨. (접미사는 책·강의 페이지를 끌어와 권위 출처를 밀어내므로 미사용)"""
    return get_entry(domain)["label"]


def search_queries(domain: str) -> list[str]:
    """도메인 검색어 목록. catalog 'query' override(문자열/리스트), 없으면 라벨.

    여러 개면 search_sources가 각각 검색해 결과를 합친다(중복 제거). 쇼핑몰 SEO가 센
    커머스 등에서 라벨 검색이 권위 출처를 못 잡을 때 '지식 의도' 검색어로 정교화한다.
    """
    q = get_entry(domain).get("query")
    if not q:
        return [build_query(domain)]
    return [q] if isinstance(q, str) else list(q)


def source_grade(url: str) -> str:
    """URL 호스트로 출처 등급 태깅 (high/mid/low)."""
    host = urlparse(url).netloc.lower()
    if host.endswith((".go.kr", ".or.kr", ".ac.kr", ".re.kr")):  # 정부·공공·학술·연구
        return "high"
    if _host_matches(host, _HIGH_HOSTS):
        return "high"
    if _host_matches(host, _MID_HOSTS):
        return "mid"
    return "low"


def _call_serper(query: str, num: int) -> dict:
    """Serper.dev 호출(구글 검색결과). 키 없으면 명확히 실패. (테스트는 이 함수를 monkeypatch)"""
    key = os.environ.get("SERPER_API_KEY")
    if not key:
        raise RuntimeError("serper_credentials_missing: SERPER_API_KEY")
    import httpx  # 람다 import 비용 회피 위해 지연 import

    resp = httpx.post(
        SERPER_ENDPOINT,
        headers={"X-API-KEY": key, "Content-Type": "application/json"},
        json={"q": query, "num": min(max(num, 1), 20), "gl": "kr", "hl": "ko"},
        timeout=15.0,
    )
    resp.raise_for_status()
    return resp.json()


def search_sources(domain: str, *, num: int = 20) -> list[dict]:
    """도메인 출처 URL 검색 → [{url, title, snippet, source_grade}] (dedupe).

    검색어가 여럿(멀티쿼리)이면 각각 검색해 합치고 URL 중복을 제거한다 — 겹치는 URL은
    한 번만 크롤되므로 결과 수는 합집합(2배가 아님). num은 검색어별 상한.
    """
    out: list[dict] = []
    seen: set[str] = set()
    for query in search_queries(domain):
        data = _call_serper(query, num)
        for item in data.get("organic", []):
            url = item.get("link")
            if not url or url in seen:
                continue
            seen.add(url)
            out.append(
                {
                    "url": url,
                    "title": item.get("title", ""),
                    "snippet": item.get("snippet", ""),
                    "source_grade": source_grade(url),
                }
            )
    return out
