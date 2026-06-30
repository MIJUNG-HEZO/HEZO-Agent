"""
P3 HTML 렌더러

render_spec.json → 완성된 정적 HTML

렌더링 우선순위:
  1. SEO head (title, meta, OG) — 필수
  2. JSON-LD (LocalBusiness + FAQPage) — GEO 핵심
  3. H1 교체 — Validation blocking 조건
  4. QuickAnswer div 삽입 — Validation 체크 대상
  5. 브랜드명·전화번호 — best-effort
  6. CSS 경로 패치 (../static/ → ./static/) — 일부 템플릿 필수
"""
from __future__ import annotations

import json
import logging
import re

from bs4 import BeautifulSoup, Tag

logger = logging.getLogger(__name__)

# H1 위치 후보 (우선순위 순)
_H1_SELECTORS = [
    '[data-hezo="hero-headline"]',
    ".copy h1",
    ".hero-text h1",
    ".hero h1",
    ".hero__title",
    "section h1",
    "main h1",
]

# 브랜드명 위치 후보
_BRAND_SELECTORS = [
    '[data-hezo="brand-name"]',
    ".brand",
    ".logo-text",
    "header .logo",
    "nav .brand",
    ".nav .brand",
]

# 전화번호 위치 후보
_PHONE_SELECTORS = [
    '[data-hezo="phone"]',
    ".glass-btn",
    ".floating",
    ".contact-phone",
    ".phone",
]


# =============================================================================
# 헬퍼
# =============================================================================

def _find(soup: BeautifulSoup, *selectors: str) -> Tag | None:
    for sel in selectors:
        el = soup.select_one(sel)
        if el:
            return el
    return None


def _upsert_meta(soup: BeautifulSoup, name: str, content: str) -> None:
    tag = soup.find("meta", attrs={"name": name})
    if tag:
        tag["content"] = content
    else:
        head = soup.find("head")
        if head:
            new = soup.new_tag("meta", attrs={"name": name, "content": content})
            head.append(new)


def _upsert_meta_property(soup: BeautifulSoup, prop: str, content: str) -> None:
    tag = soup.find("meta", attrs={"property": prop})
    if tag:
        tag["content"] = content
    else:
        head = soup.find("head")
        if head:
            new = soup.new_tag("meta", attrs={"property": prop, "content": content})
            head.append(new)


# =============================================================================
# 개별 인젝션 함수
# =============================================================================

def _inject_seo_head(soup: BeautifulSoup, seo: dict) -> None:
    # <title>
    title_tag = soup.find("title")
    if title_tag:
        title_tag.string = seo.get("title", "")
    else:
        head = soup.find("head")
        if head:
            t = soup.new_tag("title")
            t.string = seo.get("title", "")
            head.insert(0, t)

    # meta description
    if desc := seo.get("description"):
        _upsert_meta(soup, "description", desc)

    # canonical
    if canonical := seo.get("canonical"):
        tag = soup.find("link", attrs={"rel": "canonical"})
        if tag:
            tag["href"] = canonical
        else:
            head = soup.find("head")
            if head:
                head.append(soup.new_tag("link", attrs={"rel": "canonical", "href": canonical}))

    # OG
    og = seo.get("og", {})
    if og.get("title"):
        _upsert_meta_property(soup, "og:title", og["title"])
    if og.get("description"):
        _upsert_meta_property(soup, "og:description", og["description"])
    if og.get("image"):
        _upsert_meta_property(soup, "og:image", og["image"])
    if og.get("url"):
        _upsert_meta_property(soup, "og:url", og["url"])
    if og.get("type"):
        _upsert_meta_property(soup, "og:type", og["type"])

    # Twitter
    tw = seo.get("twitter", {})
    if tw.get("card"):
        _upsert_meta(soup, "twitter:card", tw["card"])
    if tw.get("title"):
        _upsert_meta(soup, "twitter:title", tw["title"])
    if tw.get("description"):
        _upsert_meta(soup, "twitter:description", tw["description"])


def _inject_jsonld(soup: BeautifulSoup, jsonld_list: list[dict]) -> None:
    # 기존 JSON-LD 전부 제거
    for script in soup.find_all("script", type="application/ld+json"):
        script.decompose()

    head = soup.find("head")
    if not head:
        return

    for jld in jsonld_list:
        script = soup.new_tag("script", type="application/ld+json")
        script.string = json.dumps(jld, ensure_ascii=False, indent=2)
        head.append(script)


def _inject_h1(soup: BeautifulSoup, h1_text: str) -> None:
    # 기존 H1이 여러 개면 첫 번째만 유지하고 나머지 제거
    all_h1 = soup.find_all("h1")

    if all_h1:
        all_h1[0].string = h1_text
        for extra in all_h1[1:]:
            extra.decompose()
        return

    # H1 없으면 후보 셀렉터로 찾아서 태그 교체
    el = _find(soup, *_H1_SELECTORS)
    if el and el.name != "h1":
        el.name = "h1"
        el.string = h1_text
    elif el:
        el.string = h1_text
    else:
        # 마지막 수단: <body> 상단에 숨겨진 H1 삽입
        body = soup.find("body")
        if body:
            h1 = soup.new_tag("h1", style="position:absolute;left:-9999px;width:1px;height:1px;overflow:hidden")
            h1.string = h1_text
            body.insert(0, h1)
            logger.warning("H1 위치를 찾지 못해 숨겨진 H1 삽입: %s", h1_text[:40])


def _inject_brand_name(soup: BeautifulSoup, brand_name: str) -> None:
    el = _find(soup, *_BRAND_SELECTORS)
    if el:
        # 자식 span 유지하면서 텍스트만 교체 (e.g. "Balance<span>Tax</span>")
        # 자식 태그가 없으면 그냥 string 교체
        if el.find(True):
            # 자식 있음 → 텍스트 노드만 교체
            for child in list(el.children):
                if hasattr(child, "name") and child.name:
                    continue  # span 등 태그는 그대로
                if str(child).strip():
                    child.replace_with(brand_name)
                    break
        else:
            el.string = brand_name


def _inject_quick_answer(soup: BeautifulSoup, text: str) -> None:
    # 이미 있으면 업데이트
    existing = soup.find(id="quick-answer") or soup.find(attrs={"data-hezo": "quick-answer"})
    if existing:
        existing.string = text
        return

    # 없으면 </body> 직전에 숨겨진 div 삽입
    body = soup.find("body")
    if body:
        qa_div = soup.new_tag("div")
        qa_div["id"] = "quick-answer"
        qa_div["data-hezo"] = "quick-answer"
        qa_div["aria-hidden"] = "true"
        qa_div["style"] = "position:absolute;left:-9999px;width:1px;height:1px;overflow:hidden"
        qa_div.string = text
        body.append(qa_div)


def _inject_phone(soup: BeautifulSoup, phone: str) -> None:
    for sel in _PHONE_SELECTORS:
        for el in soup.select(sel):
            # 전화번호 패턴이 있는 요소만 교체
            txt = el.get_text()
            if re.search(r"\d{2,4}[-.\s]\d{3,4}[-.\s]\d{4}", txt) or "전화" in txt or "상담" in txt:
                el.string = f"전화 상담 {phone}"
                break


def _inject_services(soup: BeautifulSoup, items: list[dict]) -> None:
    # data-hezo 기반 카드 (wine-market, career-notebook 등)
    hezo_cards = soup.select("[data-hezo-idx]")
    if hezo_cards:
        for i, card in enumerate(hezo_cards):
            if i >= len(items):
                # items가 부족하면 초과 카드는 display:none으로 숨김
                card["style"] = "display:none"
                continue
            svc = items[i]
            # CSS 셀렉터 대신 find(attrs=...) 사용 — 다중 p 요소(eyebrow+desc) 환경에서 확실히 동작
            name_el = card.find(attrs={"data-hezo": "service-name"}) or card.find("h3")
            desc_el = card.find(attrs={"data-hezo": "service-desc"}) or next(
                (p for p in card.find_all("p") if "eyebrow" not in (p.get("class") or [])),
                None,
            )
            if name_el:
                name_el.string = svc.get("name") or ""
            if desc_el:
                desc_el.string = svc.get("desc") or ""
            # 가격 필드가 있으면 .buy strong 에 주입 (wine-market 등 store 템플릿용)
            if price := svc.get("price"):
                price_el = card.select_one(".buy strong") or card.select_one(".price")
                if price_el:
                    price_el.string = str(price)
        return

    # info-card 기반
    cards = soup.select(".info-card")
    if cards:
        for i, card in enumerate(cards):
            if i >= len(items):
                card["style"] = "display:none"
                continue
            svc = items[i]
            name_el = card.select_one("h3") or card.select_one("strong")
            desc_el = card.select_one("p")
            if name_el:
                name_el.string = svc.get("name") or ""
            if desc_el:
                desc_el.string = svc.get("desc") or ""
        return

    # ledger-row 기반 (세무 템플릿 등)
    rows = soup.select(".ledger-row")
    for i, row in enumerate(rows):
        if i >= len(items):
            row["style"] = "display:none"
            continue
        svc = items[i]
        h3 = row.select_one("h3")
        p = row.select_one("p")
        span = row.select_one("span")
        if h3:
            h3.string = svc.get("name") or ""
        if p:
            p.string = svc.get("desc") or ""
        if span and svc.get("label"):
            span.string = svc["label"]


def _inject_faq(soup: BeautifulSoup, items: list[dict]) -> None:
    # FAQ 섹션 탐색
    faq_section = (
        soup.find(id=re.compile(r"faq", re.I))
        or soup.find(class_=re.compile(r"faq", re.I))
        or soup.find(attrs={"data-section": re.compile(r"faq", re.I)})
    )

    if faq_section:
        # 기존 FAQ 항목을 새 내용으로 교체
        q_els = faq_section.select(".faq-q, .question, dt, summary")
        a_els = faq_section.select(".faq-a, .answer, dd, .faq-content")
        for i, item in enumerate(items):
            if i < len(q_els):
                q_els[i].string = item.get("q") or ""
            if i < len(a_els):
                a_els[i].string = item.get("a") or ""
        return

    # FAQ 섹션 없으면 숨겨진 FAQ 블록 추가 (AI 크롤러용, JSON-LD와 일치)
    body = soup.find("body")
    if not body:
        return

    faq_div = soup.new_tag(
        "section",
        id="faq-section",
        attrs={"aria-label": "자주 묻는 질문"},
        style="position:absolute;left:-9999px;width:1px;height:1px;overflow:hidden",
    )
    for item in items:
        dl = soup.new_tag("dl")
        dt = soup.new_tag("dt")
        dt.string = item.get("q") or ""
        dd = soup.new_tag("dd")
        dd.string = item.get("a") or ""
        dl.append(dt)
        dl.append(dd)
        faq_div.append(dl)
    body.append(faq_div)


def _fix_css_paths(soup: BeautifulSoup) -> None:
    for link in soup.find_all("link", rel="stylesheet"):
        href = link.get("href", "")
        if "../static/" in href:
            link["href"] = href.replace("../static/", "./static/")


# =============================================================================
# 메인 렌더 함수
# =============================================================================

def _inject_hreflang(soup: BeautifulSoup, canonical: str) -> None:
    head = soup.find("head")
    if not head or not canonical:
        return
    if not soup.find("link", attrs={"rel": "alternate", "hreflang": "ko"}):
        head.append(soup.new_tag("link", attrs={"rel": "alternate", "hreflang": "ko", "href": canonical}))


def _inject_noindex(soup: BeautifulSoup) -> None:
    head = soup.find("head")
    if not head:
        return
    existing = soup.find("meta", attrs={"name": "robots"})
    if existing:
        existing["content"] = "noindex,nofollow"
    else:
        tag = soup.new_tag("meta", attrs={"name": "robots", "content": "noindex,nofollow"})
        head.insert(0, tag)


def render(template_html: str, render_spec: dict, is_preview: bool = False) -> str:
    """
    render_spec.json + 템플릿 HTML → 데이터가 baked-in된 정적 HTML.
    is_preview=True 시 <meta name="robots" content="noindex,nofollow"> 삽입.
    """
    soup = BeautifulSoup(template_html, "html.parser")
    page = render_spec["pages"][0]
    blocks = {b["type"]: b for b in page.get("blocks", [])}

    hero = blocks.get("Hero", {})
    services_block = blocks.get("Services", {})
    faq_block = blocks.get("FAQ", {})
    quick_answer_block = blocks.get("QuickAnswer", {})
    contact_block = blocks.get("Contact", {})

    # 브랜드명: JSON-LD 첫 번째 항목에서 추출
    brand_name = next(
        (jld["name"] for jld in page.get("jsonld", []) if jld.get("name")), ""
    )

    # 1. SEO head
    _inject_seo_head(soup, page.get("seo", {}))

    # 2. JSON-LD (GEO 핵심)
    if jsonld := page.get("jsonld"):
        _inject_jsonld(soup, jsonld)

    # 3. H1
    h1_text = hero.get("h1") or page.get("title_h1", "")
    if h1_text:
        _inject_h1(soup, h1_text)

    # 3b. Hero subheadline + featured price (wine-market 등 hero 카드용)
    if subheadline := hero.get("subheadline"):
        sub_el = soup.find(attrs={"data-hezo": "hero-subheadline"})
        if sub_el:
            sub_el.string = subheadline
    if featured_price := hero.get("featured_price"):
        hero_sec = soup.find("section", class_=lambda c: c and "hero" in c)
        if hero_sec:
            price_el = hero_sec.select_one("strong.price") or hero_sec.select_one(".price")
            if price_el:
                price_el.string = featured_price

    # 4. 브랜드명
    if brand_name:
        _inject_brand_name(soup, brand_name)

    # 5. QuickAnswer (Validation 체크 대상)
    if qa_text := quick_answer_block.get("text"):
        _inject_quick_answer(soup, qa_text)

    # 6. 전화번호
    if phone := contact_block.get("phone"):
        _inject_phone(soup, phone)

    # 7. 서비스
    if items := services_block.get("items"):
        _inject_services(soup, items)

    # 8. FAQ (JSON-LD와 일치하는 텍스트 → AI 크롤러 가시성)
    if faq_items := faq_block.get("items"):
        _inject_faq(soup, faq_items)

    # 9. CSS 경로 패치
    _fix_css_paths(soup)

    # 10. hreflang (퍼블리시 전용 — 프리뷰에서는 불필요)
    if not is_preview:
        canonical = page.get("seo", {}).get("canonical", "")
        _inject_hreflang(soup, canonical)

    # 11. 프리뷰 noindex (검색 색인 차단)
    if is_preview:
        _inject_noindex(soup)

    return str(soup)
