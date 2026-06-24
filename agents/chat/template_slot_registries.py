"""3개 타겟 템플릿 전용 슬롯 레지스트리 + 동반 추출 맵.

지원 템플릿:
  store/10-wine-market      → 와인샵 (wine_lineup 중심)
  landing/13-tax-accounting → 세무회계사무소 (tax_services 중심)
  blog/17-career-notebook   → 커리어 블로그 (author_info + portfolio_projects + learning_activities)

지원하지 않는 템플릿은 DEFAULT 레지스트리(기존 generic 3-슬롯)를 사용한다.
"""
from __future__ import annotations

# ── wine-market ────────────────────────────────────────────────────────────────
_WINE_MARKET_REGISTRY: dict[str, dict] = {
    "business_name": {
        "label": "와인샵 이름",
        "required": True,
        "question_hint": (
            "와인샵 이름과 위치를 함께 알려주세요. "
            "(예: '서울 마포구에서 케이브보틀을 운영합니다')"
        ),
    },
    "wine_lineup": {
        "label": "판매 와인 목록",
        "required": True,
        "question_hint": (
            "판매하는 와인 4가지를 알려주세요. "
            "이름·종류(레드/화이트/스파클링)·가격·특징을 적어주세요. "
            "(예: '이탈리아 키안티/레드/55,000원/스테이크와 잘 어울림, "
            "프랑스 샤르도네/화이트/48,000원/크리미한 버터 향')"
        ),
    },
    "phone": {
        "label": "연락처",
        "required": True,
        "question_hint": "주문 문의 전화번호를 알려주세요. 카카오채널이 있으면 함께 알려주세요.",
    },
}

_WINE_MARKET_COMPANION_MAP: dict[str, dict[str, str]] = {
    "business_name": {
        "business_region": "지역 (시·구·동 단위, 예: 서울 마포구)",
    },
    "wine_lineup": {
        "featured_wine": (
            "오늘의 추천 와인 1개 — 이름·₩가격·페어링 음식을 콤팩트하게 "
            "(예: '이탈리아 키안티 ₩55,000 스테이크 페어링', "
            "추천을 특정할 수 없으면 wine_lineup 첫 번째 항목 사용)"
        ),
        "wine_items": (
            "wine_lineup을 파싱해서 JSON 배열로 변환해야 한다. 매우 중요! "
            "각 와인은 슬래시(/)로 구분된 4개 필드: name/label/price/desc "
            "예시: '이탈리아 키안티/레드/55,000원/스테이크 페어링' → "
            "{\"name\": \"이탈리아 키안티\", \"label\": \"Red\", \"price\": \"₩55,000\", \"desc\": \"스테이크 페어링\"} "
            "label은 반드시 Red|White|Sparkling|Rosé 중 하나. "
            "price는 ₩로 시작하고 쉼표 포함 (예: ₩55,000). "
            "wine_lineup이 여러 와인을 쉼표로 구분하면 모두 배열로 포함. "
            "반환 형식: JSON 배열. 예: [{...}, {...}, {...}, {...}] "
            "형식이 완전히 틀리면 null."
        ),
    },
    "phone": {
        "kakao_channel": "카카오 채널 ID (@로 시작, 없으면 null)",
    },
}


# ── tax-accounting ─────────────────────────────────────────────────────────────
_TAX_ACCOUNTING_REGISTRY: dict[str, dict] = {
    "business_name": {
        "label": "세무사무소 이름",
        "required": True,
        "question_hint": (
            "세무사무소 이름과 위치를 함께 알려주세요. "
            "(예: '서울 강남구 한빛세무회계를 운영합니다')"
        ),
    },
    "tax_services": {
        "label": "주요 세무 서비스",
        "required": True,
        "question_hint": (
            "제공하는 세무 서비스 3가지와 주요 고객층을 알려주세요. "
            "(예: '개인사업자 월 기장대리, 부가세·종소세 신고, 절세 컨설팅 "
            "— 소상공인과 스타트업 법인 전문')"
        ),
    },
    "phone": {
        "label": "연락처",
        "required": True,
        "question_hint": "상담 전화번호를 알려주세요. 카카오채널이 있으면 함께 알려주세요.",
    },
}

_TAX_ACCOUNTING_COMPANION_MAP: dict[str, dict[str, str]] = {
    "business_name": {
        "business_region": "지역 (시·구·동 단위, 예: 서울 강남구)",
    },
    "tax_services": {
        "target_clients": (
            "주요 고객층 (예: 개인사업자, 스타트업 법인, 프리랜서, null이면 생략)"
        ),
        "success_case": (
            "대표 절세 성공 사례 키워드 1개 "
            "(예: '누락 비용 발견 연 150만원 절세', null이면 생략)"
        ),
    },
    "phone": {
        "kakao_channel": "카카오 채널 ID (@로 시작, 없으면 null)",
    },
}


# ── career-notebook ────────────────────────────────────────────────────────────
_CAREER_NOTEBOOK_REGISTRY: dict[str, dict] = {
    "author_info": {
        "label": "블로거 소개",
        "required": True,
        "question_hint": (
            "블로그 운영자 이름(닉네임)과 직군·경력을 알려주세요. "
            "(예: '김동균, 프론트엔드 개발자 3년차 이직 준비 중')"
        ),
    },
    "portfolio_projects": {
        "label": "대표 프로젝트·경험",
        "required": True,
        "question_hint": (
            "블로그에 담을 대표 프로젝트나 성장 경험 2~3가지를 알려주세요. "
            "(예: 'React 앱 리팩토링으로 로딩 40% 개선, 사내 디자인시스템 구축, 오픈소스 PR 기여')"
        ),
    },
    "learning_activities": {
        "label": "최근 학습·활동",
        "required": True,
        "question_hint": (
            "최근 커리어를 위해 하고 있는 활동들을 알려주세요. "
            "(예: '이력서 문장 12개 개선, 알고리즘 스터디, 포트폴리오 3개 정리 중')"
        ),
    },
}

_CAREER_NOTEBOOK_COMPANION_MAP: dict[str, dict[str, str]] = {
    "author_info": {
        "author_name": "이름 또는 닉네임만 (성·이름 포함, 예: 김동균)",
        "career_field": "직군 (예: 프론트엔드 개발자, UX 디자이너, 데이터 분석가)",
        "career_level": "경력 수준 (예: 신입, 3년차, 이직 준비, 취업 준비)",
    },
    "portfolio_projects": {},
    "learning_activities": {},
}


# ── 기본(generic) 레지스트리 ────────────────────────────────────────────────────
_DEFAULT_REGISTRY: dict[str, dict] = {
    "business_name": {
        "label": "업체명 · 지역",
        "required": True,
        "question_hint": (
            "업체 이름과 운영 지역을 함께 알려주세요. "
            "(예: '서울 강남에서 해조세무회계를 운영합니다')"
        ),
    },
    "core_services": {
        "label": "핵심 서비스",
        "required": True,
        "question_hint": (
            "주력 서비스나 상품을 알려주세요. "
            "주요 고객층도 함께 말씀해 주시면 맞춤 구성이 가능해요."
        ),
    },
    "phone": {
        "label": "연락처",
        "required": True,
        "question_hint": (
            "전화번호와 카카오 채널 ID를 알려주세요. "
            "카카오채널이 없으시면 '없음'이라고 해주세요."
        ),
    },
}

_DEFAULT_COMPANION_MAP: dict[str, dict[str, str]] = {
    "business_name": {
        "business_region": "지역 (시·구·동 단위, 예: 서울 강남)",
    },
    "core_services": {
        "target_audience": "주요 고객층 (예: 30-40대 직장인, 소상공인, null이면 생략)",
    },
    "phone": {
        "kakao_channel": "카카오 채널 ID (@로 시작, 없으면 null)",
    },
}


# ── 룩업 테이블 ────────────────────────────────────────────────────────────────
_TEMPLATE_MAP: dict[str, tuple[dict, dict]] = {
    "10-wine-market":         (_WINE_MARKET_REGISTRY,    _WINE_MARKET_COMPANION_MAP),
    "store/10-wine-market":   (_WINE_MARKET_REGISTRY,    _WINE_MARKET_COMPANION_MAP),
    "13-tax-accounting":      (_TAX_ACCOUNTING_REGISTRY, _TAX_ACCOUNTING_COMPANION_MAP),
    "landing/13-tax-accounting": (_TAX_ACCOUNTING_REGISTRY, _TAX_ACCOUNTING_COMPANION_MAP),
    "17-career-notebook":     (_CAREER_NOTEBOOK_REGISTRY, _CAREER_NOTEBOOK_COMPANION_MAP),
    "blog/17-career-notebook": (_CAREER_NOTEBOOK_REGISTRY, _CAREER_NOTEBOOK_COMPANION_MAP),
}


def _lookup(selected_template: str) -> tuple[dict, dict] | None:
    key = selected_template.strip()
    if key in _TEMPLATE_MAP:
        return _TEMPLATE_MAP[key]
    # category prefix 제거 후 재시도 (예: "landing/13-tax-accounting" → "13-tax-accounting")
    short = key.rsplit("/", 1)[-1]
    return _TEMPLATE_MAP.get(short)


def get_slot_registry(selected_template: str) -> dict[str, dict]:
    """템플릿 ID로 슬롯 레지스트리 반환. 미지원 템플릿은 기본 레지스트리."""
    result = _lookup(selected_template)
    return result[0] if result else _DEFAULT_REGISTRY


def get_companion_map(selected_template: str) -> dict[str, dict[str, str]]:
    """템플릿 ID로 동반 슬롯 추출 맵 반환. 미지원 템플릿은 기본 맵."""
    result = _lookup(selected_template)
    return result[1] if result else _DEFAULT_COMPANION_MAP


# 모든 가능한 동반 슬롯 레이블 (chat_http_handler._build_system_prompt용)
ALL_COMPANION_LABELS: dict[str, str] = {
    # 공통
    "business_region": "지역",
    "kakao_channel": "카카오채널",
    # generic
    "target_audience": "주요 고객",
    # wine-market
    "featured_wine": "오늘의 추천 와인",
    # tax-accounting
    "target_clients": "주요 고객층",
    "success_case": "절세 사례",
    # career-notebook
    "author_name": "작성자 이름",
    "career_field": "직군",
    "career_level": "경력 수준",
}
