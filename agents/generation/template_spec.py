"""
P4 생성 에이전트의 partial_render_spec 정의 (Option A: Pre-fill)

각 템플릿별 구조화된 필드(structured_companions + wine_lineup 파싱)를 미리 구성하고,
LLM은 None 필드(창의적 콘텐츠)만 완성하도록 제약함.

목표: 할루시네이션 방지 + 데이터 일관성 보장
"""
from __future__ import annotations

# ──────────────────────────────────────────────────────────────────────────────
# wine-market partial_spec
# ──────────────────────────────────────────────────────────────────────────────

_WINE_MARKET_PARTIAL_SPEC = {
    "schema_version": "1.0.0",
    "pages": [
        {
            "path": "/",
            "blocks": [
                {
                    "type": "Hero",
                    "h1": None,          # _build_partial_render_spec에서 첫 번째 와인명으로 설정
                    "subheadline": None, # _build_partial_render_spec에서 첫 번째 와인 설명으로 설정
                    "featured_price": None,  # _build_partial_render_spec에서 첫 번째 와인 가격으로 설정
                    "subtext": None,     # LLM이 부제목으로 생성
                    "cta_text": "와인 추천 받기",  # 고정 (변경 금지)
                    "cta_href": "#contact",  # 고정
                },
                {
                    "type": "Services",
                    "title": "추천 와인",
                    "items": None,  # wine_lineup 파싱으로 구성 (P4에서)
                    # 예상 구조: [
                    #   {"name": "와인명", "label": "Red/White/Sparkling", "price": "₩XX,XXX", "desc": "설명"},
                    #   ...
                    # ]
                },
                {
                    "type": "FAQ",
                    "title": "자주 묻는 질문",
                    "items": None,  # LLM이 생성 (와인 관련 Q&A)
                },
                {
                    "type": "Contact",
                    "phone": None,  # slots.phone으로 대입 (P4에서)
                    "kakao": None,  # structured_companions.kakao_channel (P4에서)
                    "hours": None,  # LLM이 기본값 생성
                },
            ],
        }
    ],
    "seo": {
        "title": None,  # LLM이 생성: "{업체명} 와인샵 | {지역}" 패턴
        "description": None,  # LLM이 생성
        "og_title": None,
        "og_description": None,
    },
    "quick_answer": None,  # LLM이 생성
    "json_ld": {
        "@context": "https://schema.org",
        "@type": "LocalBusiness",
        "name": None,  # slots.business_name (P4에서)
        "address": {
            "addressLocality": None,  # structured_companions.business_region (P4에서)
            "addressCountry": "KR",
        },
        "telephone": None,  # slots.phone (P4에서)
        "specialty": "와인 전문점",  # 고정
    },
}

# ──────────────────────────────────────────────────────────────────────────────
# tax-accounting partial_spec
# ──────────────────────────────────────────────────────────────────────────────

_TAX_ACCOUNTING_PARTIAL_SPEC = {
    "schema_version": "1.0.0",
    "pages": [
        {
            "path": "/",
            "blocks": [
                {
                    "type": "Hero",
                    "h1": None,  # LLM이 세무 전문성 표현 (업체명 제외)
                    "subtext": None,  # LLM이 신뢰/경험 표현
                    "cta_text": "상담 신청",  # 고정
                    "cta_href": "#contact",
                },
                {
                    "type": "Services",
                    "title": "주요 서비스",
                    "items": None,  # tax_services 파싱으로 구성 + success_case 추가 (P4에서)
                    # 예상 구조: [
                    #   {"name": "서비스명", "desc": "설명", "label": "STARTER|MONTHLY|ADVISORY"},
                    #   ...,
                    #   {"name": "...", "desc": "[실제 사례] {success_case}", "label": "..."}  # 마지막에 success_case
                    # ]
                },
                {
                    "type": "FAQ",
                    "title": "자주 묻는 질문",
                    "items": None,  # LLM이 생성 (세무 관련 Q&A)
                },
                {
                    "type": "Contact",
                    "phone": None,  # slots.phone
                    "kakao": None,  # structured_companions.kakao_channel
                    "hours": None,
                },
            ],
        }
    ],
    "seo": {
        "title": None,  # LLM이 생성: "{지역} 세무사무소" 패턴
        "description": None,
        "og_title": None,
        "og_description": None,
    },
    "quick_answer": None,  # LLM이 생성 (target_clients 포함 가능)
    "json_ld": {
        "@context": "https://schema.org",
        "@type": "Accountant",
        "name": None,  # slots.business_name
        "address": {
            "addressLocality": None,  # structured_companions.business_region
            "addressCountry": "KR",
        },
        "telephone": None,  # slots.phone
        "areaServed": None,  # structured_companions.target_clients 참고
    },
}

# ──────────────────────────────────────────────────────────────────────────────
# career-notebook partial_spec
# ──────────────────────────────────────────────────────────────────────────────

_CAREER_NOTEBOOK_PARTIAL_SPEC = {
    "schema_version": "1.0.0",
    "pages": [
        {
            "path": "/",
            "blocks": [
                {
                    "type": "Hero",
                    "h1": None,  # LLM이 생성 또는 "{career_level} {career_field}의 커리어 기록" (P4에서 처리)
                    "subtext": None,  # LLM이 생성
                    "cta_text": "전체 경력 보기",
                    "cta_href": "#portfolio",
                },
                {
                    "type": "Services",  # 포트폴리오 = Services
                    "title": "프로젝트 회고",
                    "items": None,  # portfolio_projects 파싱으로 구성 (P4에서)
                    # 예상 구조: [
                    #   {"name": "프로젝트명", "desc": "성과", "label": "Case Study|Resume|Portfolio"},
                    #   ...
                    # ]
                },
                {
                    "type": "FAQ",
                    "title": "커리어 Q&A",
                    "items": None,  # LLM이 생성 (면접, 이력서, 포트폴리오 관련)
                },
                {
                    "type": "Contact",
                    "email": None,  # LLM이 생성 (또는 사용자 입력)
                    "linkedin": None,
                    "github": None,
                },
            ],
        }
    ],
    "seo": {
        "title": None,  # LLM이 생성: "{author_name}의 커리어 블로그" (P4에서 처리)
        "description": None,
        "og_title": None,
        "og_description": None,
    },
    "quick_answer": None,  # LLM이 생성 (author_name, career_field, career_level 포함)
    "json_ld": {
        "@context": "https://schema.org",
        "@type": "Blog",
        "name": None,  # "{author_name}의 커리어 블로그"
        "author": {
            "@type": "Person",
            "name": None,  # structured_companions.author_name (P4에서)
            "jobTitle": None,  # structured_companions.career_field
            "sameAs": [],  # LinkedIn, GitHub 등
        },
        "datePublished": None,
        "dateModified": None,
    },
}

# ──────────────────────────────────────────────────────────────────────────────
# 룩업 테이블
# ──────────────────────────────────────────────────────────────────────────────

_TEMPLATE_PARTIAL_SPECS = {
    "store/10-wine-market": _WINE_MARKET_PARTIAL_SPEC,
    "10-wine-market": _WINE_MARKET_PARTIAL_SPEC,
    "landing/13-tax-accounting": _TAX_ACCOUNTING_PARTIAL_SPEC,
    "13-tax-accounting": _TAX_ACCOUNTING_PARTIAL_SPEC,
    "blog/17-career-notebook": _CAREER_NOTEBOOK_PARTIAL_SPEC,
    "17-career-notebook": _CAREER_NOTEBOOK_PARTIAL_SPEC,
}


def get_partial_spec(template_id: str) -> dict | None:
    """
    템플릿 ID → partial_render_spec 반환

    Args:
        template_id: 템플릿 ID (예: "store/10-wine-market", "10-wine-market")

    Returns:
        partial_render_spec dict, 미지원 템플릿은 None
    """
    if not template_id:
        return None

    key = template_id.strip()
    if key in _TEMPLATE_PARTIAL_SPECS:
        # 깊은 복사 (각 호출마다 독립적인 dict 반환)
        import copy

        return copy.deepcopy(_TEMPLATE_PARTIAL_SPECS[key])

    # category prefix 제거 후 재시도
    short = key.rsplit("/", 1)[-1]
    if short in _TEMPLATE_PARTIAL_SPECS:
        import copy

        return copy.deepcopy(_TEMPLATE_PARTIAL_SPECS[short])

    return None
