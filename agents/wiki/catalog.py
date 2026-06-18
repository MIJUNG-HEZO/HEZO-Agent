"""HEZO Wiki (P2) 카탈로그 — 60 도메인 정적 매핑 (source of truth).

60 템플릿(landing/blog/store 각 20)이 고정이므로 도메인 목록은 크롤이 발견하는 게
아니라 이 카탈로그가 안다. 각 도메인 → category·template_no·template_id·label·
volatility·seed.

- template_id = 정규 파일명 슬러그 (build/renderer/template_map.py 산출물명과 1:1).
  P2는 Contract의 category+domain으로만 라우팅하므로 P1 시맨틱 id는 쓰지 않는다.
- volatility = 신선도 TTL 결정 (high 7일 / mid 30일 / low 무제한). 기본 mid.
- seed = 초기 적재 대상 (카테고리당 1개, status=done). 나머지 57개는 pending.

상세 설계: 바탕화면 `HEZO_P2_위키저장소_PRD_v2_확정.md` 부록 A.
"""
from __future__ import annotations

from agents.wiki.constants import CATEGORIES

# domain → {category, template_no, template_id, label, volatility, seed}
WIKI_CATALOG: dict[str, dict] = {
    # ─── landing (20) ───────────────────────────────────────────────────────
    "dental_clinic":        {"category": "landing", "template_no": 1,  "template_id": "01-clinic-landing",       "label": "치과/병원",          "volatility": "mid",  "seed": False},
    "dev_bootcamp":         {"category": "landing", "template_no": 2,  "template_id": "02-course-landing",       "label": "개발 부트캠프",      "volatility": "mid",  "seed": False},
    "saas_product":         {"category": "landing", "template_no": 3,  "template_id": "03-saas-product",         "label": "SaaS 제품",          "volatility": "mid",  "seed": False},
    "car_rental":           {"category": "landing", "template_no": 4,  "template_id": "04-long-term-rental",     "label": "장기 렌터카",        "volatility": "mid",  "seed": False},
    "lifting_clinic":       {"category": "landing", "template_no": 5,  "template_id": "05-lifting-clinic",       "label": "리프팅/피부과",      "volatility": "mid",  "seed": False},
    "hanok_stay":           {"category": "landing", "template_no": 6,  "template_id": "06-hanok-stay",           "label": "한옥 스테이",        "volatility": "low",  "seed": False},
    "legal_consulting":     {"category": "landing", "template_no": 7,  "template_id": "07-legal-consulting",     "label": "법률 상담",          "volatility": "high", "seed": False},
    "creator_agency":       {"category": "landing", "template_no": 8,  "template_id": "08-creator-agency",       "label": "크리에이터 에이전시", "volatility": "mid",  "seed": False},
    "fitness_studio":       {"category": "landing", "template_no": 9,  "template_id": "09-fitness-studio",       "label": "피트니스 스튜디오",   "volatility": "mid",  "seed": False},
    "wedding_studio":       {"category": "landing", "template_no": 10, "template_id": "10-wedding-studio",       "label": "웨딩 스튜디오",      "volatility": "low",  "seed": False},
    "language_academy":     {"category": "landing", "template_no": 11, "template_id": "11-language-academy",     "label": "어학원",             "volatility": "mid",  "seed": False},
    "interior_remodel":     {"category": "landing", "template_no": 12, "template_id": "12-interior-remodel",     "label": "인테리어 리모델링",   "volatility": "mid",  "seed": False},
    "tax_accounting":       {"category": "landing", "template_no": 13, "template_id": "13-tax-accounting",       "label": "세무/회계",          "volatility": "high", "seed": True},
    "mind_counseling":      {"category": "landing", "template_no": 14, "template_id": "14-mind-counseling",      "label": "심리 상담",          "volatility": "mid",  "seed": False},
    "live_event":           {"category": "landing", "template_no": 15, "template_id": "15-live-event",           "label": "라이브 이벤트",      "volatility": "mid",  "seed": False},
    "franchise_startup":    {"category": "landing", "template_no": 16, "template_id": "16-franchise-startup",    "label": "프랜차이즈 창업",    "volatility": "mid",  "seed": False},
    "solar_energy":         {"category": "landing", "template_no": 17, "template_id": "17-solar-energy",         "label": "태양광 에너지",      "volatility": "high", "seed": False},
    "app_launch":           {"category": "landing", "template_no": 18, "template_id": "18-mobile-app-launch",    "label": "앱 출시",            "volatility": "mid",  "seed": False},
    "pet_hospital":         {"category": "landing", "template_no": 19, "template_id": "19-pet-hospital",         "label": "동물병원",           "volatility": "mid",  "seed": False},
    "restaurant_franchise": {"category": "landing", "template_no": 20, "template_id": "20-restaurant-franchise", "label": "외식 프랜차이즈",    "volatility": "mid",  "seed": False},

    # ─── blog (20) ──────────────────────────────────────────────────────────
    "food_travel":          {"category": "blog", "template_no": 1,  "template_id": "01-food-travel-blog",    "label": "푸드/여행",      "volatility": "mid",  "seed": False},
    "daily_life":           {"category": "blog", "template_no": 2,  "template_id": "02-daily-life-blog",     "label": "일상",           "volatility": "low",  "seed": False},
    "developer_docs":       {"category": "blog", "template_no": 3,  "template_id": "03-developer-docs",      "label": "개발 문서",      "volatility": "high", "seed": False},
    "magazine":             {"category": "blog", "template_no": 4,  "template_id": "04-magazine-grid",       "label": "매거진",         "volatility": "mid",  "seed": False},
    "expert_column":        {"category": "blog", "template_no": 5,  "template_id": "05-expert-column",       "label": "전문가 칼럼",    "volatility": "mid",  "seed": False},
    "recipe":               {"category": "blog", "template_no": 6,  "template_id": "06-recipe-kitchen",      "label": "레시피",         "volatility": "low",  "seed": False},
    "travel_atlas":         {"category": "blog", "template_no": 7,  "template_id": "07-travel-atlas",        "label": "여행 아틀라스",  "volatility": "mid",  "seed": False},
    "wellness":             {"category": "blog", "template_no": 8,  "template_id": "08-wellness-journal",    "label": "웰니스",         "volatility": "mid",  "seed": False},
    "finance_memo":         {"category": "blog", "template_no": 9,  "template_id": "09-finance-memo",        "label": "금융 메모",      "volatility": "high", "seed": False},
    "art_design":           {"category": "blog", "template_no": 10, "template_id": "10-art-design-log",      "label": "아트/디자인",    "volatility": "low",  "seed": False},
    "parenting":            {"category": "blog", "template_no": 11, "template_id": "11-parenting-community", "label": "육아",           "volatility": "mid",  "seed": False},
    "newsletter":           {"category": "blog", "template_no": 12, "template_id": "12-newsletter-digest",   "label": "뉴스레터",       "volatility": "high", "seed": False},
    "beauty_review":        {"category": "blog", "template_no": 13, "template_id": "13-beauty-review",       "label": "뷰티 리뷰",      "volatility": "mid",  "seed": False},
    "real_estate":          {"category": "blog", "template_no": 14, "template_id": "14-real-estate-journal", "label": "부동산",         "volatility": "high", "seed": False},
    "music_review":         {"category": "blog", "template_no": 15, "template_id": "15-music-review",        "label": "음악 리뷰",      "volatility": "mid",  "seed": False},
    "fitness_log":          {"category": "blog", "template_no": 16, "template_id": "16-fitness-diet-log",    "label": "피트니스 로그",  "volatility": "mid",  "seed": False},
    "career":               {"category": "blog", "template_no": 17, "template_id": "17-career-notebook",    "label": "커리어",         "volatility": "high", "seed": True},
    "book_essay":           {"category": "blog", "template_no": 18, "template_id": "18-book-essay",          "label": "책 에세이",      "volatility": "low",  "seed": False},
    "photo_diary":          {"category": "blog", "template_no": 19, "template_id": "19-photo-diary",         "label": "포토 다이어리",  "volatility": "low",  "seed": False},
    "local_food":           {"category": "blog", "template_no": 20, "template_id": "20-local-food-guide",    "label": "동네 맛집",      "volatility": "mid",  "seed": False},

    # ─── store (20) ─────────────────────────────────────────────────────────
    "cafe_menu":            {"category": "store", "template_no": 1,  "template_id": "01-cafe-menu",        "label": "카페 메뉴",      "volatility": "low",  "seed": False},
    "fashion_select":       {"category": "store", "template_no": 2,  "template_id": "02-fashion-select",   "label": "패션 셀렉트",    "volatility": "mid",  "seed": False},
    "handmade_studio":      {"category": "store", "template_no": 3,  "template_id": "03-handmade-studio",  "label": "핸드메이드",     "volatility": "low",  "seed": False},
    "digital_goods":        {"category": "store", "template_no": 4,  "template_id": "04-digital-goods",    "label": "디지털 굿즈",    "volatility": "mid",  "seed": False},
    "booking_service":      {"category": "store", "template_no": 5,  "template_id": "05-booking-service",  "label": "예약 서비스",    "volatility": "mid",  "seed": False},
    "nail_beauty":          {"category": "store", "template_no": 6,  "template_id": "06-oops-nail",        "label": "네일/뷰티",      "volatility": "mid",  "seed": False},
    "fruits":               {"category": "store", "template_no": 7,  "template_id": "07-fruits-basket",    "label": "청과",           "volatility": "mid",  "seed": False},
    "sneaker_drop":         {"category": "store", "template_no": 8,  "template_id": "08-sneaker-drop",     "label": "스니커즈 드롭",  "volatility": "high", "seed": False},
    "plant_shop":           {"category": "store", "template_no": 9,  "template_id": "09-plant-shop",       "label": "식물 샵",        "volatility": "low",  "seed": False},
    "wine_market":          {"category": "store", "template_no": 10, "template_id": "10-wine-market",      "label": "와인 마켓",      "volatility": "mid",  "seed": True},
    "furniture_studio":     {"category": "store", "template_no": 11, "template_id": "11-furniture-studio", "label": "가구 스튜디오",  "volatility": "low",  "seed": False},
    "pet_supplies":         {"category": "store", "template_no": 12, "template_id": "12-pet-supplies",     "label": "반려동물 용품",  "volatility": "mid",  "seed": False},
    "skincare_lab":         {"category": "store", "template_no": 13, "template_id": "13-skincare-lab",     "label": "스킨케어 랩",    "volatility": "mid",  "seed": False},
    "grocery_market":       {"category": "store", "template_no": 14, "template_id": "14-grocery-market",   "label": "식료품 마켓",    "volatility": "mid",  "seed": False},
    "tech_gadget":          {"category": "store", "template_no": 15, "template_id": "15-tech-gadget",      "label": "테크 가젯",      "volatility": "high", "seed": False},
    "book_curation":        {"category": "store", "template_no": 16, "template_id": "16-book-curation",    "label": "도서 큐레이션",  "volatility": "low",  "seed": False},
    "kids_toy":             {"category": "store", "template_no": 17, "template_id": "17-kids-toy",         "label": "키즈 완구",      "volatility": "mid",  "seed": False},
    "outdoor_gear":         {"category": "store", "template_no": 18, "template_id": "18-outdoor-gear",     "label": "아웃도어 기어",  "volatility": "mid",  "seed": False},
    "jewelry":              {"category": "store", "template_no": 19, "template_id": "19-jewelry-atelier",  "label": "주얼리",         "volatility": "low",  "seed": False},
    "tea_collection":       {"category": "store", "template_no": 20, "template_id": "20-tea-collection",   "label": "티 컬렉션",      "volatility": "low",  "seed": False},
}

VALID_VOLATILITY = ("high", "mid", "low")


# ─── 조회 헬퍼 ──────────────────────────────────────────────────────────────
def get_entry(domain: str) -> dict:
    """도메인 카탈로그 항목 반환. 없으면 KeyError."""
    if domain not in WIKI_CATALOG:
        raise KeyError(f"unknown domain: {domain!r}")
    return WIKI_CATALOG[domain]


def all_domains() -> list[str]:
    """전체 60 도메인 키."""
    return list(WIKI_CATALOG)


def seed_domains() -> list[str]:
    """시드 도메인 (초기 status=done 대상)."""
    return [d for d, e in WIKI_CATALOG.items() if e["seed"]]


def pending_domains() -> list[str]:
    """비시드 도메인 (초기 status=pending 대상)."""
    return [d for d, e in WIKI_CATALOG.items() if not e["seed"]]


def domains_by_category(category: str) -> list[str]:
    """카테고리별 도메인 키."""
    return [d for d, e in WIKI_CATALOG.items() if e["category"] == category]


def _validate_catalog() -> None:
    """import 시 카탈로그 무결성 검증 — 잘못된 데이터를 조기에 잡는다."""
    assert len(WIKI_CATALOG) == 60, f"카탈로그는 60행이어야 함 (현재 {len(WIKI_CATALOG)})"

    seen_pairs: set[tuple[str, int]] = set()
    seed_per_cat: dict[str, int] = {c: 0 for c in CATEGORIES}
    required = {"category", "template_no", "template_id", "label", "volatility", "seed"}

    for domain, e in WIKI_CATALOG.items():
        missing = required - e.keys()
        assert not missing, f"{domain}: 누락 필드 {missing}"
        assert e["category"] in CATEGORIES, f"{domain}: 잘못된 category {e['category']!r}"
        assert 1 <= e["template_no"] <= 20, f"{domain}: template_no 범위 밖 {e['template_no']}"
        assert e["volatility"] in VALID_VOLATILITY, f"{domain}: 잘못된 volatility {e['volatility']!r}"
        pair = (e["category"], e["template_no"])
        assert pair not in seen_pairs, f"{domain}: (category, template_no) 중복 {pair}"
        seen_pairs.add(pair)
        if e["seed"]:
            seed_per_cat[e["category"]] += 1

    for cat in CATEGORIES:
        n = len(domains_by_category(cat))
        assert n == 20, f"{cat}: 20개여야 함 (현재 {n})"
        assert seed_per_cat[cat] == 1, f"{cat}: 시드 1개여야 함 (현재 {seed_per_cat[cat]})"


_validate_catalog()
