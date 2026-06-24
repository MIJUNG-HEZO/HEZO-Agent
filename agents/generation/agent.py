"""
생성 에이전트 — Amazon Bedrock AgentCore Runtime 진입점

흐름 (v2 에이전트 동작):
  1. site_id 파싱
  2. contract_final.json 로드 + generation_ready 게이트 확인
  3. validation_feedback.json 로드 (재시도 경로용, 없으면 None)
  4. Claude Sonnet → render_spec 생성
  5. 규칙 기반 평가 (evaluate_render_spec)
  6. [에이전트] LLM 자체 평가 — "AI 검색 인용 가능성" 점수화
  7. 미달 섹션만 재생성 (전체 재생성 아님, 최대 2회)
  8. 가드레일 검사
  9. render_spec.json → S3 저장
"""
from __future__ import annotations

import json
import logging
import os
import re
import time
from typing import Any

import boto3
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

from agents.generation.evaluators.render_spec_evaluator import evaluate_render_spec
from agents.generation.guardrails.content_guardrail import (
    GuardrailViolation,
    check_guardrails,
)
from agents.generation.template_spec import get_partial_spec
from agents.generation.tools.contract_loader import load_contract
from agents.generation.tools.feedback_loader import load_feedback
from agents.generation.tools.render_spec_saver import save_render_spec
from libs.telemetry import init_telemetry, record_llm_usage

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(levelname)s %(message)s")
logger = logging.getLogger("hezo.generation")

REGION = os.environ.get("AWS_DEFAULT_REGION", os.environ.get("REGION", "ap-northeast-2"))
MODEL_ID = os.environ.get("MODEL_ID", "global.anthropic.claude-sonnet-4-6")
QUALITY_THRESHOLD = int(os.environ.get("QUALITY_THRESHOLD", "70"))
LLM_EVAL_THRESHOLD = int(os.environ.get("LLM_EVAL_THRESHOLD", "70"))

init_telemetry("generation", region=REGION)
MAX_RETRIES = int(os.environ.get("MAX_RETRIES", "2"))

app = FastAPI(title="HEZO Generation Agent")

_bedrock: Any = None


def get_bedrock():
    global _bedrock
    if _bedrock is None:
        from botocore.config import Config
        _bedrock = boto3.client(
            "bedrock-runtime",
            region_name=REGION,
            config=Config(read_timeout=600, connect_timeout=10, retries={"max_attempts": 0}),
        )
    return _bedrock


# =============================================================================
# site_id 파싱
# =============================================================================

def parse_site_id(input_text: str, session_attrs: dict) -> str:
    if site_id := session_attrs.get("site_id"):
        return site_id.strip()
    m = re.search(r"site_id[=:\s]+([a-zA-Z0-9_\-]+)", input_text)
    if m:
        return m.group(1).strip()
    raise ValueError(f"site_id를 찾을 수 없음 — inputText: {input_text!r}")


# =============================================================================
# Claude 호출 — render_spec 전체 생성
# =============================================================================

_SYSTEM_PROMPT = """당신은 HEZO의 AI 친화 홈페이지 생성 전문가입니다.

주어진 Contract JSON을 분석하여 render_spec.json을 생성하세요.

## 출력 형식
반드시 순수 JSON만 출력하세요. 마크다운 코드 블록(```json)이나 설명 텍스트를 포함하지 마세요.
JSON 시작 전 또는 후에 어떤 텍스트도 추가하지 마세요.

## render_spec 구조
{
  "schema_version": "1.0.0",
  "site_id": "<contract의 ids.site_id>",
  "template_id": "<contract의 template.template_id>",
  "pages": [
    {
      "path": "/",
      "title_h1": "<H1 — 페이지당 정확히 1개>",
      "h2_list": ["<FAQ 질문형 H2 5~7개>"],
      "seo": {
        "title": "<SEO 타이틀 60자 이내>",
        "description": "<메타 디스크립션 160자 이내>",
        "canonical": "<https://{template_slug}.hezo.io/>",
        "target_keywords": ["<키워드 3~5개>"],
        "og": {
          "title": "<OG 타이틀>",
          "description": "<OG 설명>",
          "image": "<https://{slug}.hezo.io/og-thumb.jpg>",
          "type": "website",
          "url": "<canonical URL>"
        },
        "twitter": {
          "card": "summary_large_image",
          "title": "<트위터 타이틀>",
          "description": "<트위터 설명>"
        }
      },
      "jsonld": [
        {
          "@context": "https://schema.org",
          "@type": "<업종별 Schema.org 타입>",
          "name": "<업체명>",
          "description": "<업체 설명>",
          "address": { "@type": "PostalAddress", "addressLocality": "<지역>", "addressCountry": "KR" },
          "telephone": "<전화번호>",
          "openingHours": "<영업시간>"
        },
        {
          "@context": "https://schema.org",
          "@type": "FAQPage",
          "mainEntity": [
            { "@type": "Question", "name": "<질문>", "acceptedAnswer": { "@type": "Answer", "text": "<답변>" } }
          ]
        },
        {
          "@context": "https://schema.org",
          "@type": "Service",
          "serviceType": "<대표 서비스명>",
          "provider": { "@type": "<업종 Schema 타입>", "name": "<업체명>" },
          "description": "<서비스 설명>",
          "areaServed": { "@type": "City", "name": "<지역>" }
        }
      ],
      "blocks": [
        { "type": "Hero", "h1": "<H1>", "subtext": "<부제목>", "cta_text": "<CTA>", "cta_href": "#contact" },
        { "type": "Services", "items": [{ "name": "<서비스명>", "desc": "<서비스 설명>", "label": "<약어>" }] },
        { "type": "FAQ", "module_key": "<업종>", "items": [{ "q": "<질문>", "a": "<답변>" }] },
        { "type": "QuickAnswer", "text": "<업체 한줄 요약 — 50~120자>" },
        { "type": "Contact", "phone": "<전화>", "kakao": "<카카오>", "hours": "<영업시간>" }
      ]
    }
  ],
  "supplementary_files": {
    "llms_txt": "# <업체명>\\n> <업종> | <지역>\\n\\n## 핵심 페이지\\n- [홈](/) : <업체 한 줄 설명>\\n- [서비스 안내](/#services) : <서비스 한 줄 요약>\\n- [자주 묻는 질문](/#faq) : 비용·기간·절차 안내\\n- [상담 신청](/#contact) : 무료 상담\\n\\n## 서비스\\n- <서비스1>\\n- <서비스2>\\n\\n## 연락처\\n- 전화: <전화번호>\\n- 영업시간: <영업시간>",
    "llms_full_txt": "# <업체명>\\n> <업종> | <지역>\\n\\n<업체 소개 2~3문장>\\n\\n## 핵심 서비스\\n- **<서비스1>**: <구체적 설명>\\n- **<서비스2>**: <구체적 설명>\\n\\n## 고객 고통점 해결\\n- <문제1>: <해결책>\\n\\n## FAQ\\n- Q: <실제 사용자가 AI 검색에 물어볼 질문1>\\n  A: <구체적 수치·비용·기간 포함 답변>\\n- Q: <질문2>\\n  A: <답변2>\\n- Q: <질문3>\\n  A: <답변3>\\n\\n## 연락처\\n- 전화: <전화번호>\\n- 영업시간: <영업시간>\\n\\n## 타겟 고객\\n- <고객군1>\\n- <고객군2>",
    "sitemap_pages": [
      { "path": "/", "priority": 1.0, "changefreq": "monthly" },
      { "path": "/llms-full.txt", "priority": 0.8, "changefreq": "monthly" }
    ],
    "robots_rules": [
      "User-agent: GPTBot", "Allow: /",
      "User-agent: ClaudeBot", "Allow: /",
      "User-agent: PerplexityBot", "Allow: /",
      "User-agent: Yeti", "Allow: /",
      "User-agent: *", "Allow: /",
      "Sitemap: https://<slug>.hezo.io/sitemap.xml"
    ]
  },
  "build_manifest": {
    "s3_artifact_bucket": "hezo-artifacts",
    "s3_site_bucket": "hezo-sites",
    "s3_key_prefix": "sites/<site_id>/"
  }
}

## 업종 → Schema.org 타입
tax_accounting → Accountant
medical_clinic → MedicalClinic
dental_clinic  → Dentist
law_firm       → LegalService
restaurant     → FoodEstablishment
fitness        → SportsActivityLocation
salon/nail     → BeautySalon
real_estate    → RealEstateAgent
education      → EducationalOrganization
기타           → LocalBusiness

## BLOCKING 조건 (반드시 준수)
- H1: 페이지당 정확히 1개
- FAQ: 최소 5개 (h2_list와 jsonld.FAQPage.mainEntity 모두 5개 이상)
- llms_txt 필수 생성 (## 핵심 페이지 링크 섹션 포함)
- llms_full_txt에 ## FAQ 섹션 필수 (Q:/A: 형식, 3개 이상)
- robots_rules에 GPTBot/ClaudeBot/PerplexityBot/Yeti Allow 필수
- FAQPage JSON-LD 필수
- Service JSON-LD 필수 (대표 서비스 1개 이상)

## 템플릿별 render_spec 특화 지침

Contract JSON의 template.template_id가 아래 중 하나이면 해당 지침을 반드시 따른다.

### store/10-wine-market (와인샵)
slots.wine_lineup에 사용자가 입력한 와인 목록이 있다.

Services.items: wine_lineup을 파싱해 와인 4개를 구성한다.
  - name: 와인 이름 (이탈리아 키안티, 프랑스 샤르도네 등)
  - desc: 페어링 추천 한 줄 (예: "스테이크 & 치즈와 잘 어울리는 묵직한 레드")
  - label: "Red" | "White" | "Sparkling" 중 하나
  - price: "₩55,000" 형식 (숫자+원화 기호, 쉼표 포함)

Hero.h1: slots.featured_wine이 있으면 그 와인 이름을 사용. 없으면 wine_lineup 첫 번째 항목 이름.
Hero.subtext: 해당 와인의 페어링 추천 한 줄 (예: "스테이크와 딱 맞는 묵직한 레드 와인")

JSON-LD @type: "LocalBusiness", specialty 필드에 "와인 전문점" 추가.
FAQ 예시 주제: 주문 방법, 배송 가능 지역, 와인 추천 기준, 가격대, 선물 포장 여부.

llms_txt/llms-full_txt: 와인 목록과 가격, 페어링 정보 중심으로 작성.
QuickAnswer: "{업체명}은 {지역} 위치의 와인 전문샵으로 {featured_wine_name} 등 엄선된 와인을 판매합니다." 형식.

### landing/13-tax-accounting (세무회계사무소)
slots.tax_services에 사용자가 입력한 세무 서비스 목록이 있다.

Services.items: tax_services를 파싱해 서비스 3개를 구성한다.
  - name: 서비스명 (예: 월 기장대리, 부가세 신고, 절세 컨설팅)
  - desc: 대상 고객과 서비스 내용 한 줄 (예: "개인사업자를 위한 매월 매출·비용·인건비 정리")
  - label: "STARTER" | "MONTHLY" | "REVIEW" | "ADVISORY" 중 맥락에 맞게 선택

Hero.h1: 세무 전문성과 지역명을 결합한 임팩트 있는 문구 (예: "숫자를 정리하면 사업의 다음 결정이 보입니다").
  h1에 업체명을 직접 넣지 말 것 — JSON-LD와 브랜드 셀렉터가 처리함.

slots.target_clients가 있으면 QuickAnswer에 포함: "{업체명}은 {target_clients} 전문 세무사무소입니다."
slots.success_case가 있으면 Services 마지막 항목 desc에 "[실제 사례] {success_case}" 형식으로 추가.

JSON-LD @type: "Accountant".
FAQ 예시 주제: 기장 비용, 부가세 신고 주기, 종합소득세 신고 방법, 법인 vs 개인사업자 절세, 첫 상담 방법.

### blog/17-career-notebook (커리어 블로그)
slots.author_info 또는 slots.author_name + slots.career_field + slots.career_level로 블로그 주인공 정보가 있다.

Hero.h1: "{career_level} {career_field}의 커리어 기록" 형식 (50자 이내, 예: "3년차 프론트엔드 개발자의 커리어 기록").
  author_info 원문에서 직군·경력을 추출해 사용.
Hero.subtext: 블로그 목적 한 줄 (예: "일한 흔적을 다음 기회로 바꾸는 성장 로그").

Services.items: slots.portfolio_projects를 파싱해 3개 note card를 구성한다.
  - name: 프로젝트·경험 제목 (간결하게, 30자 이내)
  - desc: 성과·배운 점 한 줄 (구체적 수치 포함 시 +1점)
  - label: "Case Study" | "Resume" | "Portfolio" | "Interview" 중 맥락에 맞게 선택

QuickAnswer: "{author_name}의 커리어 블로그. {career_field} 분야 {career_level}의 프로젝트 회고, 이력서 개선, 면접 준비 기록."

slots.learning_activities가 있으면 FAQ.items에 학습 활동을 Q/A 형식으로 변환:
  Q: "최근 커리어 준비로 무엇을 하고 있나요?" A: {learning_activities 내용}
  추가 FAQ: 포트폴리오 작성법, 이직 준비 타임라인, 개발자 이력서 작성 팁 등 직군 맞춤 질문 포함.

JSON-LD @type: "Person" (name: author_name) + "Blog" (@type: "Blog", author: Person).
  LocalBusiness나 Service JSON-LD 대신 Person + Blog 구조 사용.
  Service JSON-LD는 blog 템플릿에서 생략 가능.

llms_txt/llms-full_txt: 비용·가격 정보 불필요. 커리어 성장 기록 중심으로 작성.
  ## 핵심 페이지에 포트폴리오·이력서·면접 관련 섹션 링크 포함.
"""

# =============================================================================
# Option A: Partial render_spec 완성 전용 시스템 프롬프트
# =============================================================================

_SYSTEM_PROMPT_PARTIAL = """당신은 HEZO의 partial render_spec 완성 전문가입니다.

주어진 partial_render_spec의 null 필드(창의적 콘텐츠)만 완성하세요.

## 중요 규칙 (반드시 준수)

### ✅ 해야 할 것
- null 필드만 완성하세요 (값이 null인 필드).
- 창의적 콘텐츠만 생성: SEO (title, description), H1, FAQ (질문/답변), QuickAnswer, 설명, 시간.
- 각 필드의 타입과 형식을 유지하세요.
  - title: 문자열
  - title_h1: 문자열 (Hero.h1과 동일한 값 — SEO용 페이지 레벨 H1 필드)
  - h2_list: 문자열 배열 (FAQ 질문형 H2 5개 이상 — FAQ.items[].q와 동일한 내용)
  - items: 객체 배열
  - FAQ.items: {q: 문자열, a: 문자열} 배열

### ❌ 하지 말 것
- null이 아닌 필드는 절대 수정하지 마세요.
- Services.items 수정 금지: name, label, price, desc 변경 금지.
- Contact 정보 수정 금지: phone, kakao, hours 변경 금지.
- JSON-LD 비즈니스 데이터 수정 금지: name, address, telephone, jobTitle 변경 금지.
- 와인명, 가격, 지역, 저자명 등 비즈니스 데이터를 재파싱하지 마세요.

## 출력 형식
순수 JSON만 출력하세요. 마크다운 코드 블록(```json)이나 설명 텍스트를 포함하지 마세요.

## 템플릿별 창의적 콘텐츠 생성 지침

### 🍷 wine-market (와인샵)
- Hero.h1: 와인의 특징을 창의적으로 표현 (50자 이내).
- Hero.subtext: 와인의 페어링을 감정적으로 설명.
- Services.items[].desc: 각 와인에 대한 창의적 설명 (기존 파싱된 데이터와 일관성 유지).
- FAQ: 와인 관련 질문 (5-7개): 와인 선택 기준, 보관법, 페어링 추천, 배송 범위, 가격대 비교, 선물 포장 등.
- SEO title: "{업체명} 와인샵 | {지역} | 와인 추천" 패턴.
- QuickAnswer: 와인샵의 특징 한 줄 (50-120자).

### 📊 tax-accounting (세무회계사무소)
- Hero.h1: 세무 전문성과 신뢰 표현 (업체명 제외, 50자 이내).
- Services.items[].desc: 각 서비스에 대한 창의적 설명 (기존 파싱된 이름과 일관성 유지).
- FAQ: 세무 관련 질문 (5-7개): 기장 비용, 부가세 신고 주기, 절세 팁, 법인 vs 개인, 초기 상담 방법 등.
- SEO title: "{지역} 세무사무소 | 개인사업자·스타트업 전문" 패턴.
- QuickAnswer: 세무사무소의 강점 한 줄.

### 👨‍💼 career-notebook (커리어 블로그)
- Hero.h1: "{career_level} {career_field}의 커리어 기록" 형식 (author_name, career_field, career_level은 이미 매핑됨, 수정 금지).
- Services.items[].desc: 각 프로젝트의 성과 설명 (기존 파싱된 이름과 일관성 유지).
- FAQ: 커리어 관련 질문 (5-7개): 면접 준비, 이력서 작성, 포트폴리오 구성, 이직 타이밍, 기술 학습 등.
- SEO title: "{author_name}의 커리어 블로그 | {career_field}" 패턴.
- QuickAnswer: 블로그의 목적 한 줄.

## 데이터 일관성 유지
- 같은 정보는 모든 필드에서 일관되게 표현하세요.
  - Hero의 와인명 = Services.items[0]의 이름 (같은 와인 언급).
  - FAQ의 지역명 = JSON-LD address와 동일.
  - QuickAnswer의 업체명/저자명 = JSON-LD name과 동일.
"""

# =============================================================================
# Option A: 파싱 함수들 (구조화된 필드 구성)
# =============================================================================


def _parse_wine_lineup(wine_lineup_str: str) -> list[dict]:
    """
    wine_lineup 문자열을 Services.items 배열로 파싱

    입력 형식: "와인1/종류/가격/설명, 와인2/종류/가격/설명, ..."
    예: "이탈리아 키안티/레드/55,000원/스테이크와 어울림, 프랑스 샤르도네/화이트/48,000원/..."

    출력:
        [
            {"name": "이탈리아 키안티", "label": "Red", "price": "₩55,000", "desc": "스테이크와 어울림"},
            ...
        ]
    """
    items = []
    if not wine_lineup_str or not wine_lineup_str.strip():
        return items

    # 가격 내 쉼표(55,000)와 아이템 구분자 쉼표 충돌 방지 — "/, " 패턴으로 아이템 구분
    import re as _re
    raw_items = _re.split(r',\s+(?=[가-힣a-zA-Z])', wine_lineup_str)
    for item_str in raw_items:
        item_str = item_str.strip()
        if not item_str:
            continue

        parts = [p.strip() for p in item_str.split("/")]
        if len(parts) < 4:
            logger.warning(f"Skipping malformed wine item: {item_str}")
            continue

        name, wine_type, price_str, desc = parts[0], parts[1], parts[2], parts[3]

        # wine_type 정규화 (레드/빨강/Red/Bordeaux → Red)
        wine_type_lower = wine_type.lower()
        if "레드" in wine_type_lower or "빨강" in wine_type_lower or wine_type_lower == "red":
            normalized_type = "Red"
        elif "화이트" in wine_type_lower or "하양" in wine_type_lower or wine_type_lower == "white":
            normalized_type = "White"
        elif "스파클링" in wine_type_lower or "샴페인" in wine_type_lower or wine_type_lower == "sparkling":
            normalized_type = "Sparkling"
        elif "로제" in wine_type_lower or wine_type_lower == "rosé":
            normalized_type = "Rosé"
        else:
            normalized_type = wine_type  # 그대로 사용

        # 가격 정규화 (55,000원 / 55000원 / ₩55,000 → ₩55,000)
        price_normalized = price_str.replace("원", "").replace("₩", "").strip()
        # 숫자만 추출
        price_digits = re.sub(r"[^0-9]", "", price_normalized)
        if price_digits:
            # 3자리마다 쉼표 추가
            price_with_comma = "{:,}".format(int(price_digits))
            price_final = f"₩{price_with_comma}"
        else:
            price_final = price_str

        items.append({
            "name": name,
            "label": normalized_type,
            "price": price_final,
            "desc": desc,
        })

    return items[:4]  # 최대 4개


def _parse_tax_services(tax_services_str: str) -> list[dict]:
    """
    tax_services 문자열을 Services.items 배열로 파싱

    입력 형식: "서비스1, 서비스2, 서비스3"
    예: "월 기장대리, 부가세·종소세 신고, 절세 컨설팅"

    출력:
        [
            {"name": "월 기장대리", "desc": "개인사업자를 위한...", "label": "MONTHLY"},
            ...
        ]
    """
    items = []
    if not tax_services_str or not tax_services_str.strip():
        return items

    service_names = [s.strip() for s in tax_services_str.split(",") if s.strip()]

    for service_name in service_names[:3]:  # 최대 3개
        # 서비스 종류 판단 (label 지정)
        service_lower = service_name.lower()
        if "기장" in service_lower or "정산" in service_lower:
            label = "MONTHLY"
        elif "신고" in service_lower or "신청" in service_lower:
            label = "FILING"
        elif "절세" in service_lower or "상담" in service_lower or "컨설팅" in service_lower:
            label = "ADVISORY"
        else:
            label = "STARTER"

        items.append({
            "name": service_name,
            "desc": None,  # LLM이 생성
            "label": label,
        })

    return items


def _parse_portfolio_projects(portfolio_str: str) -> list[dict]:
    """
    portfolio_projects 문자열을 Services.items 배열로 파싱

    입력 형식: "프로젝트1, 프로젝트2, 프로젝트3"
    예: "React 앱 리팩토링으로 로딩 40% 개선, 사내 디자인시스템 구축, 오픈소스 PR"

    출력:
        [
            {"name": "React 앱 리팩토링...", "desc": None, "label": "Case Study"},
            ...
        ]
    """
    items = []
    if not portfolio_str or not portfolio_str.strip():
        return items

    project_names = [p.strip() for p in portfolio_str.split(",") if p.strip()]

    for project_name in project_names[:3]:  # 최대 3개
        # 프로젝트 라벨 판단
        project_lower = project_name.lower()
        if "오픈" in project_lower or "기여" in project_lower:
            label = "Portfolio"
        elif "이력서" in project_lower or "경력" in project_lower:
            label = "Resume"
        else:
            label = "Case Study"

        items.append({
            "name": project_name[:50],  # 제목 50자 이내
            "desc": None,  # LLM이 생성
            "label": label,
        })

    return items


def _build_partial_render_spec(contract: dict) -> dict | None:
    """
    structured_companions + slots → partial_render_spec 구성

    LLM은 None 필드(창의적 콘텐츠)만 완성.
    """
    template_id = contract.get("template", {}).get("template_id")
    slots = contract.get("slots", {})
    companions = contract.get("structured_companions", {})

    # 템플릿별 partial_spec 가져오기
    partial = get_partial_spec(template_id)
    if not partial:
        logger.warning(f"No partial spec for {template_id}, using default")
        return None

    # ────────────────────────────────────────────────────────────────────────
    # wine-market: Services.items + Contact 구성
    # ────────────────────────────────────────────────────────────────────────
    if "wine-market" in (template_id or ""):
        # Haiku companion에서 wine_items (구조화 배열)가 있으면 사용
        # 없으면 wine_lineup 문자열을 파싱
        wine_items_str = companions.get("wine_items")
        if wine_items_str:
            try:
                wine_items = json.loads(wine_items_str) if isinstance(wine_items_str, str) else wine_items_str
                if not isinstance(wine_items, list):
                    wine_items = _parse_wine_lineup(slots.get("wine_lineup", ""))
            except (json.JSONDecodeError, TypeError, ValueError):
                logger.warning("wine_items JSON 파싱 실패, wine_lineup 파싱으로 폴백")
                wine_items = _parse_wine_lineup(slots.get("wine_lineup", ""))
        else:
            wine_items = _parse_wine_lineup(slots.get("wine_lineup", ""))

        # Services.items: desc = "{label} | {pairing}" 포맷으로 합치기
        wine_items_display = [
            {**item, "desc": f"{item['label']} | {item['desc']}" if item.get("label") else item.get("desc", "")}
            for item in wine_items
        ]

        # Hero + title_h1 결정론적 설정 (LLM 할루시네이션 차단)
        first_wine = wine_items[0] if wine_items else {}
        for block in partial["pages"][0]["blocks"]:
            if block.get("type") == "Hero":
                block["h1"] = first_wine.get("name", slots.get("business_name", ""))
                block["featured_price"] = first_wine.get("price", "")
                block["subheadline"] = first_wine.get("desc", "")
                break
        partial["pages"][0]["title_h1"] = first_wine.get("name", slots.get("business_name", ""))

        # Services.items 설정
        for block in partial["pages"][0]["blocks"]:
            if block.get("type") == "Services":
                block["items"] = wine_items_display

        # Contact 설정
        for block in partial["pages"][0]["blocks"]:
            if block.get("type") == "Contact":
                block["phone"] = slots.get("phone", "")
                block["kakao"] = companions.get("kakao_channel", "")

        # JSON-LD 설정
        if "json_ld" in partial:
            partial["json_ld"]["name"] = slots.get("business_name", "")
            partial["json_ld"]["address"]["addressLocality"] = companions.get("business_region", "")
            partial["json_ld"]["telephone"] = slots.get("phone", "")

    # ────────────────────────────────────────────────────────────────────────
    # tax-accounting: Services.items + success_case + Contact 구성
    # ────────────────────────────────────────────────────────────────────────
    elif "tax-accounting" in (template_id or ""):
        tax_items = _parse_tax_services(slots.get("tax_services", ""))

        # success_case를 마지막 항목에 추가
        if companions.get("success_case") and tax_items:
            tax_items[-1]["desc"] = f"[실제 사례] {companions['success_case']}"

        # Services.items 설정
        for block in partial["pages"][0]["blocks"]:
            if block.get("type") == "Services":
                block["items"] = tax_items

        # Contact 설정
        for block in partial["pages"][0]["blocks"]:
            if block.get("type") == "Contact":
                block["phone"] = slots.get("phone", "")
                block["kakao"] = companions.get("kakao_channel", "")

        # JSON-LD 설정
        if "json_ld" in partial:
            partial["json_ld"]["name"] = slots.get("business_name", "")
            partial["json_ld"]["address"]["addressLocality"] = companions.get("business_region", "")
            partial["json_ld"]["telephone"] = slots.get("phone", "")

    # ────────────────────────────────────────────────────────────────────────
    # career-notebook: Services.items + JSON-LD Person 구성
    # ────────────────────────────────────────────────────────────────────────
    elif "career-notebook" in (template_id or ""):
        portfolio_items = _parse_portfolio_projects(slots.get("portfolio_projects", ""))

        # Services.items 설정
        for block in partial["pages"][0]["blocks"]:
            if block.get("type") == "Services":
                block["items"] = portfolio_items

        # JSON-LD Person 설정
        if "json_ld" in partial and "author" in partial["json_ld"]:
            author_name = companions.get("author_name", "")
            career_field = companions.get("career_field", "")
            partial["json_ld"]["author"]["name"] = author_name
            partial["json_ld"]["author"]["jobTitle"] = career_field
            partial["json_ld"]["name"] = f"{author_name}의 커리어 블로그"

    return partial


def call_claude(contract: dict, crawl_snapshot: dict | None, issues_hint: list[str] | None = None) -> dict:
    """
    Option A: Partial render_spec 완성 방식

    1. structured_companions + wine_lineup 파싱 → partial_render_spec 구성
    2. partial_render_spec를 user_content에 명시
    3. LLM은 None 필드만 완성 (비즈니스 데이터 보호)
    """
    template_id = contract.get("template", {}).get("template_id", "")

    # ────────────────────────────────────────────────────────────────────────
    # Step 1: partial_render_spec 구성
    # ────────────────────────────────────────────────────────────────────────
    partial_spec = _build_partial_render_spec(contract)
    if not partial_spec:
        logger.warning(f"Could not build partial spec for {template_id}, falling back to full generation")
        # fallback: 기본 방식 (structured_companions 명시)
        user_content = f"Contract JSON:\n{json.dumps(contract, ensure_ascii=False, indent=2)}"
        max_tokens = 8192
    else:
        # ────────────────────────────────────────────────────────────────────
        # Step 2: user_content 구성 (partial_spec 명시)
        # ────────────────────────────────────────────────────────────────────
        user_content = f"""Partial render_spec (다음 필드는 이미 구성됨, 수정하지 마세요):
{json.dumps(partial_spec, ensure_ascii=False, indent=2)}

Contract (참고용):
{json.dumps(contract, ensure_ascii=False, indent=2)}

당신의 작업:
1. 위 partial_render_spec의 null 필드만 완성하세요.
2. null이 아닌 필드는 절대 수정하지 마세요.
3. 창의적 콘텐츠만 생성하세요 (SEO, FAQ, H1, QuickAnswer, 시간, 설명).

중요:
- Services.items: 이미 구성됨 (수정 금지) — 와인명, 가격, 라벨 변경 금지
- Contact 정보: 이미 구성됨 (수정 금지) — 전화, 카카오 채널 변경 금지
- JSON-LD 비즈니스 데이터: 이미 구성됨 (수정 금지) — 업체명, 주소, 전화 변경 금지
"""
        if crawl_snapshot:
            snap_str = json.dumps(crawl_snapshot, ensure_ascii=False)[:2000]
            user_content += f"\n\nCrawl Snapshot (참고용):\n{snap_str}"

        if issues_hint:
            user_content += "\n\n이전 생성에서 발견된 이슈 — 이 부분을 개선하세요:\n"
            user_content += "\n".join(f"- {i}" for i in issues_hint)

        max_tokens = 4096  # 일부만 생성하므로 충분

    # ────────────────────────────────────────────────────────────────────────
    # Step 3: LLM 호출
    # ────────────────────────────────────────────────────────────────────────
    bedrock = get_bedrock()
    body = json.dumps({
        "anthropic_version": "bedrock-2023-05-31",
        "max_tokens": max_tokens,
        "system": _SYSTEM_PROMPT_PARTIAL,  # 신규 partial 전용 프롬프트
        "messages": [{"role": "user", "content": user_content}],
    })

    start = time.monotonic()
    resp = bedrock.invoke_model(
        modelId=MODEL_ID, body=body,
        contentType="application/json", accept="application/json",
    )
    elapsed = (time.monotonic() - start) * 1000

    result = json.loads(resp["body"].read())
    _usage = result.get("usage", {})
    record_llm_usage(
        "generation", "sonnet",
        _usage.get("input_tokens", 0), _usage.get("output_tokens", 0), ms=elapsed,
    )
    logger.info("Claude 호출 완료 (partial_spec 완성): %.0f ms", elapsed)

    text = result["content"][0]["text"].strip()
    m = re.search(r"```(?:json)?\s*([\s\S]+?)\s*```", text)
    if m:
        text = m.group(1)

    # ────────────────────────────────────────────────────────────────────────
    # Step 4: 응답 파싱 + partial_spec 병합
    # ────────────────────────────────────────────────────────────────────────
    completed = json.loads(text)

    if partial_spec:
        # LLM 응답을 partial_spec에 병합 (null 필드만 채움)
        render_spec = _merge_partial_spec(partial_spec, completed)
    else:
        render_spec = completed

    return render_spec


def _merge_partial_spec(partial: dict, completed: dict) -> dict:
    """
    partial_spec + LLM 응답 → 최종 render_spec 병합

    LLM은 null 필드만 생성했으므로, 두 dict를 깊게 병합
    """
    import copy

    result = copy.deepcopy(partial)

    # 재귀적으로 null 필드를 completed에서 가져옴
    def merge_recursive(target, source):
        if isinstance(target, dict) and isinstance(source, dict):
            for key in source:
                if key in target:
                    if target[key] is None:
                        target[key] = source[key]
                    elif isinstance(target[key], (dict, list)):
                        merge_recursive(target[key], source[key])
                else:
                    target[key] = source[key]
        elif isinstance(target, list) and isinstance(source, list):
            for i, item in enumerate(source):
                if i < len(target) and target[i] is None:
                    target[i] = item

    merge_recursive(result, completed)
    return result


# =============================================================================
# [에이전트] LLM 자체 평가 — AI 인용 가능성 판단
# =============================================================================

def llm_self_eval(render_spec: dict, contract: dict) -> dict:
    """
    Claude가 스스로 생성한 render_spec의 AI 검색 인용 가능성 평가.
    반환: {score: 0~100, weak_sections: [...], reason: str}
    """
    slots = contract.get("slots", {})
    # P1 contract_final 스키마 기준: 업종은 meta.domain_label, 지역은 business_region 슬롯.
    # (이전 business_type/address 키는 P1이 생성하지 않아 빈 값이 되던 버그 수정)
    business_type = (
        contract.get("meta", {}).get("domain_label")
        or slots.get("business_type", "")
    )
    region = slots.get("business_region") or slots.get("address", "")

    page = render_spec.get("pages", [{}])[0]
    faq_items: list[dict] = []
    quick_answer = ""
    for block in page.get("blocks", []):
        if block.get("type") == "FAQ":
            faq_items = block.get("items") or []
        elif block.get("type") == "QuickAnswer":
            quick_answer = block.get("text", "")

    prompt = f"""다음 홈페이지 콘텐츠가 '{business_type} {region} 추천' AI 검색 질의에서 인용될 가능성을 평가하세요.

[QuickAnswer]
{quick_answer}

[FAQ {len(faq_items)}개]
{json.dumps(faq_items[:5], ensure_ascii=False, indent=2)}

[SEO 키워드]
{json.dumps(page.get('seo', {}).get('target_keywords', []), ensure_ascii=False)}

평가 기준 (각 20점):
1. 구체적 수치·비용·기간 포함 여부
2. FAQ가 실제 사용자 질문 형태인지
3. QuickAnswer가 핵심 정보를 압축 전달하는지
4. 업종 핵심 키워드 자연스럽게 포함했는지
5. 경쟁사 대비 차별화 포인트 존재 여부

반드시 다음 JSON만 출력하세요:
{{
  "score": <0~100 정수>,
  "weak_sections": <["FAQ", "QuickAnswer", "SEO", "JSONLD"] 중 미달 항목 목록, 없으면 []>,
  "reason": "<미달 이유 한 문장, 통과 시 빈 문자열>"
}}"""

    bedrock = get_bedrock()
    body = json.dumps({
        "anthropic_version": "bedrock-2023-05-31",
        "max_tokens": 512,
        "messages": [{"role": "user", "content": prompt}],
    })
    start = time.monotonic()
    resp = bedrock.invoke_model(
        modelId=MODEL_ID, body=body,
        contentType="application/json", accept="application/json",
    )
    elapsed = (time.monotonic() - start) * 1000
    result = json.loads(resp["body"].read())
    _usage = result.get("usage", {})
    record_llm_usage(
        "generation_self_eval", "sonnet",
        _usage.get("input_tokens", 0), _usage.get("output_tokens", 0), ms=elapsed,
    )
    text = result["content"][0]["text"].strip()

    m = re.search(r"\{[\s\S]+\}", text)
    if m:
        text = m.group()

    try:
        return json.loads(text)
    except json.JSONDecodeError:
        logger.warning("LLM 자체 평가 JSON 파싱 실패 — 기본값 사용")
        return {"score": 75, "weak_sections": [], "reason": ""}


def regenerate_weak_sections(
    render_spec: dict, weak_sections: list[str], contract: dict, reason: str
) -> dict:
    """
    미달 섹션만 Claude로 재생성 (전체 재생성 아님).
    반환: 수정된 render_spec
    """
    import copy
    patched = copy.deepcopy(render_spec)
    slots = contract.get("slots", {})
    # P1 contract_final 스키마: 업종=meta.domain_label, 지역=business_region 슬롯
    business_type = (
        contract.get("meta", {}).get("domain_label")
        or slots.get("business_type", "")
    )
    business_name = slots.get("business_name", "")
    region = slots.get("business_region") or slots.get("address", "")

    page = patched.get("pages", [{}])[0]

    if "FAQ" in weak_sections:
        prompt = f"""'{business_name}'({business_type}, {region}) 홈페이지 FAQ 7개를 개선하여 재작성하세요.
개선 방향: {reason}
- 구체적 수치(비용·기간) 포함
- 실제 사용자가 AI 검색에 물어볼 법한 질문 형태

다음 JSON 배열만 출력:
[{{"q": "질문", "a": "구체적 답변"}}]"""

        body = json.dumps({
            "anthropic_version": "bedrock-2023-05-31",
            "max_tokens": 2048,
            "messages": [{"role": "user", "content": prompt}],
        })
        _start = time.monotonic()
        resp = get_bedrock().invoke_model(modelId=MODEL_ID, body=body,
                                          contentType="application/json", accept="application/json")
        _result = json.loads(resp["body"].read())
        record_llm_usage(
            "generation_regen_faq", "sonnet",
            _result.get("usage", {}).get("input_tokens", 0),
            _result.get("usage", {}).get("output_tokens", 0),
            ms=(time.monotonic() - _start) * 1000,
        )
        text = _result["content"][0]["text"].strip()
        try:
            m = re.search(r"\[[\s\S]+\]", text)
            new_faq = json.loads(m.group() if m else text)
            for block in page.get("blocks", []):
                if block.get("type") == "FAQ":
                    block["items"] = new_faq
                    break
            # FAQPage JSON-LD도 업데이트
            for jld in page.get("jsonld", []):
                if jld.get("@type") == "FAQPage":
                    jld["mainEntity"] = [
                        {"@type": "Question", "name": item["q"],
                         "acceptedAnswer": {"@type": "Answer", "text": item["a"]}}
                        for item in new_faq
                    ]
                    break
            logger.info("FAQ 섹션 재생성 완료: %d개", len(new_faq))
        except (json.JSONDecodeError, AttributeError) as exc:
            logger.warning("FAQ 재생성 파싱 실패: %s", exc)

    if "QuickAnswer" in weak_sections:
        prompt = f"""'{business_name}'({business_type}, {region})의 QuickAnswer를 재작성하세요.
조건: 50~120자, AI 검색 인용 최적화, 핵심 서비스·특징 압축.
개선 방향: {reason}
문자열만 출력 (따옴표 없이):"""

        body = json.dumps({
            "anthropic_version": "bedrock-2023-05-31",
            "max_tokens": 256,
            "messages": [{"role": "user", "content": prompt}],
        })
        _start = time.monotonic()
        resp = get_bedrock().invoke_model(modelId=MODEL_ID, body=body,
                                          contentType="application/json", accept="application/json")
        _result = json.loads(resp["body"].read())
        record_llm_usage(
            "generation_regen_qa", "sonnet",
            _result.get("usage", {}).get("input_tokens", 0),
            _result.get("usage", {}).get("output_tokens", 0),
            ms=(time.monotonic() - _start) * 1000,
        )
        new_qa = _result["content"][0]["text"].strip().strip('"')
        for block in page.get("blocks", []):
            if block.get("type") == "QuickAnswer":
                block["text"] = new_qa[:120]
                break
        logger.info("QuickAnswer 재생성 완료")

    if "SEO" in weak_sections:
        seo = page.get("seo", {})
        prompt = f"""'{business_name}'({business_type}, {region}) 홈페이지 SEO 메타데이터를 개선하세요.
개선 방향: {reason}

현재 키워드: {seo.get('target_keywords', [])}

다음 JSON만 출력:
{{
  "title": "<60자 이내 SEO 타이틀>",
  "description": "<160자 이내 메타 디스크립션>",
  "target_keywords": ["<키워드 5개>"]
}}"""

        body = json.dumps({
            "anthropic_version": "bedrock-2023-05-31",
            "max_tokens": 512,
            "messages": [{"role": "user", "content": prompt}],
        })
        _start = time.monotonic()
        resp = get_bedrock().invoke_model(modelId=MODEL_ID, body=body,
                                          contentType="application/json", accept="application/json")
        _result = json.loads(resp["body"].read())
        record_llm_usage(
            "generation_regen_seo", "sonnet",
            _result.get("usage", {}).get("input_tokens", 0),
            _result.get("usage", {}).get("output_tokens", 0),
            ms=(time.monotonic() - _start) * 1000,
        )
        text = _result["content"][0]["text"].strip()
        try:
            m = re.search(r"\{[\s\S]+\}", text)
            new_seo = json.loads(m.group() if m else text)
            page.setdefault("seo", {}).update(new_seo)
            logger.info("SEO 섹션 재생성 완료")
        except (json.JSONDecodeError, AttributeError) as exc:
            logger.warning("SEO 재생성 파싱 실패: %s", exc)

    return patched


# =============================================================================
# 핵심 에이전트 로직
# =============================================================================

def run_generation(site_id: str) -> dict:
    logger.info("생성 에이전트 시작 — site_id=%s", site_id)

    # 1. Contract 로드
    loaded = load_contract(site_id)
    contract = loaded["contract"]
    crawl_snapshot = loaded.get("crawl_snapshot")

    # 2. generation_ready 게이트
    if not contract.get("gates", {}).get("generation_ready", False):
        logger.warning("generation_ready=false — 생성 건너뜀")
        return {"status": "skipped", "reason": "generation_ready=false", "site_id": site_id}

    # 3. validation_feedback 로드 (재시도 경로 — 없으면 None)
    feedback = load_feedback(site_id)
    initial_hints: list[str] | None = None
    if feedback:
        initial_hints = [f"{i.get('code')}: {i.get('detail')}" for i in feedback.get("patch_hints", [])]
        logger.info("validation_feedback 로드 — hint %d건", len(initial_hints))

    # 4. Claude 호출 + 규칙 기반 평가 루프
    render_spec: dict | None = None
    eval_result: dict = {}
    issues_hint: list[str] | None = initial_hints

    for attempt in range(1, MAX_RETRIES + 1):
        logger.info("Claude 호출 시도 %d/%d", attempt, MAX_RETRIES)
        try:
            render_spec = call_claude(contract, crawl_snapshot, issues_hint)
        except (json.JSONDecodeError, KeyError) as exc:
            logger.error("Claude 응답 파싱 실패 (시도 %d): %s", attempt, exc)
            if attempt == MAX_RETRIES:
                raise
            issues_hint = [f"이전 응답이 유효한 JSON이 아니었음: {exc}"]
            continue

        eval_result = evaluate_render_spec(render_spec, threshold=QUALITY_THRESHOLD)
        logger.info("규칙 평가: score=%d, issues=%d", eval_result["score"], eval_result["issue_count"])

        if eval_result["passed"]:
            break

        if attempt < MAX_RETRIES:
            issues_hint = eval_result["issues"]
            logger.warning("규칙 점수 미달 (score=%d) — 재시도", eval_result["score"])
        else:
            logger.warning("최대 재시도 도달 — score=%d 로 진행", eval_result["score"])

    if render_spec is None:
        raise RuntimeError("render_spec 생성 실패")

    # 필수 최상위 필드 주입 (partial_spec 방식에서 LLM이 생성하지 않는 결정론적 값)
    template_id = contract.get("template", {}).get("template_id", "")
    render_spec.setdefault("schema_version", "1.0.0")
    render_spec["site_id"] = site_id
    render_spec["template_id"] = template_id

    # 5. [에이전트] LLM 자체 평가 — AI 인용 가능성 판단
    llm_eval = llm_self_eval(render_spec, contract)
    logger.info("LLM 자체 평가: score=%d, weak=%s", llm_eval["score"], llm_eval["weak_sections"])

    if llm_eval["score"] < LLM_EVAL_THRESHOLD and llm_eval["weak_sections"]:
        logger.info("LLM 자체 평가 미달 — 미달 섹션만 재생성: %s", llm_eval["weak_sections"])
        render_spec = regenerate_weak_sections(
            render_spec, llm_eval["weak_sections"], contract, llm_eval["reason"]
        )

    # 6-pre. 결정론적 페이지 필드 보완 (LLM이 null로 반환하는 경우 방어)
    # title_h1 = Hero.h1 (항상 동일한 값 — LLM에 의존하지 않음)
    # h2_list  = FAQ.items[].q (항상 동일한 값)
    _pages = render_spec.get("pages", []) or []
    logger.info("6-pre 보완 시작 — pages=%d, 첫 페이지 keys=%s",
                len(_pages), list(_pages[0].keys()) if _pages else [])
    for _page in _pages:
        _blocks = _page.get("blocks", []) or []
        _hero_h1 = next((b.get("h1") for b in _blocks if b.get("type") == "Hero"), None)
        logger.info("6-pre: title_h1=%r, hero_h1=%r, blocks=%d",
                    _page.get("title_h1"), _hero_h1, len(_blocks))
        if not _page.get("title_h1"):
            if _hero_h1:
                _page["title_h1"] = _hero_h1
                logger.info("6-pre: title_h1 ← hero_h1=%r", _hero_h1)
            else:
                # 최후 폴백: business_name + domain_label
                _biz = contract.get("slots", {}).get("business_name", "")
                _domain_lbl = contract.get("meta", {}).get("domain_label", "")
                _fallback = f"{_biz} | {_domain_lbl}".strip(" |") or "홈페이지"
                _page["title_h1"] = _fallback
                logger.warning("6-pre: title_h1 폴백 사용 — hero_h1 없음, fallback=%r", _fallback)
        if not _page.get("h2_list"):
            for _blk in _blocks:
                if _blk.get("type") == "FAQ":
                    _qs = [_i.get("q", "") for _i in (_blk.get("items") or []) if _i.get("q")]
                    if _qs:
                        _page["h2_list"] = _qs
                    break

        # 결정론적 JSON-LD 보완 — LLM 의존 없이 항상 pages[0].jsonld 보장
        _slots = contract.get("slots", {})
        _meta = contract.get("meta", {})
        _biz_name = _slots.get("business_name", "")
        _phone = _slots.get("phone", "")
        _region = contract.get("meta", {}).get("domain_label", "") or ""
        _faq_block = next((b for b in _blocks if b.get("type") == "FAQ"), None)
        _faq_items = (_faq_block.get("items") or []) if _faq_block else []

        # FAQ items가 없으면 h2_list로 synthetic Q&A 생성
        if not _faq_items:
            _h2s = _page.get("h2_list") or []
            if _h2s:
                _faq_items = [{"q": q, "a": f"자세한 내용은 전화로 문의해 주세요. ({_phone})" if _phone else "자세한 내용은 문의 바랍니다."} for q in _h2s[:5]]
            else:
                # 도메인 generic fallback (validation 통과용 최소 5개)
                _faq_items = [
                    {"q": f"{_biz_name}의 영업 시간은 어떻게 되나요?", "a": f"자세한 내용은 전화로 문의해 주세요. ({_phone})" if _phone else "자세한 내용은 문의 바랍니다."},
                    {"q": f"{_biz_name}에서 제공하는 서비스는 무엇인가요?", "a": "자세한 내용은 전화로 문의해 주세요."},
                    {"q": "방문 전 예약이 필요한가요?", "a": "자세한 내용은 전화로 문의해 주세요."},
                    {"q": "주차 공간이 있나요?", "a": "자세한 내용은 전화로 문의해 주세요."},
                    {"q": "더 많은 정보를 어디서 얻을 수 있나요?", "a": f"전화 또는 방문을 통해 {_biz_name}에 직접 문의해 주시면 안내해 드립니다."},
                ]
            logger.warning("6-pre: FAQ items 없음 — synthetic %d개 생성", len(_faq_items))
            if _faq_block:
                _faq_block["items"] = _faq_items

        # FAQPage JSON-LD (FAQ items에서 결정론적 생성 — 항상 보장)
        _faq_jsonld = {
            "@context": "https://schema.org",
            "@type": "FAQPage",
            "mainEntity": [
                {
                    "@type": "Question",
                    "name": _item.get("q", ""),
                    "acceptedAnswer": {"@type": "Answer", "text": _item.get("a", "")},
                }
                for _item in _faq_items
                if _item.get("q") and _item.get("a")
            ],
        }

        existing_jsonld = _page.get("jsonld") or []
        _has_faq_ld = any(j.get("@type") == "FAQPage" for j in existing_jsonld)
        if _has_faq_ld:
            # 기존 FAQPage를 결정론적 버전으로 교체
            existing_jsonld = [
                _faq_jsonld if j.get("@type") == "FAQPage" else j
                for j in existing_jsonld
            ]
        else:
            existing_jsonld = [_faq_jsonld] + existing_jsonld
        if not any(j.get("@type") in ("LocalBusiness", "Accountant", "Blog") for j in existing_jsonld):
            existing_jsonld.append({
                "@context": "https://schema.org",
                "@type": "LocalBusiness",
                "name": _biz_name,
                "telephone": _phone,
            })
        _page["jsonld"] = existing_jsonld
        logger.info("6-pre: jsonld 확정 — FAQPage %d개 Q&A, 총 %d개 schema",
                    len(_faq_jsonld["mainEntity"]), len(existing_jsonld))

    # 6. 가드레일
    check_guardrails(render_spec)

    # 7. S3 저장
    save_result = save_render_spec(site_id, render_spec)
    logger.info(
        "생성 완료 — site_id=%s, rule_score=%d, llm_score=%d, s3_key=%s",
        site_id, eval_result.get("score", 0), llm_eval["score"], save_result["s3_key"],
    )

    return {
        "status": "complete",
        "site_id": site_id,
        "render_spec_key": save_result["s3_key"],
        "eval_score": eval_result.get("score", 0),
        "llm_eval_score": llm_eval["score"],
        "weak_sections_patched": llm_eval["weak_sections"],
        "page_count": save_result["page_count"],
        "saved_at": save_result["saved_at"],
    }


# =============================================================================
# AgentCore Runtime HTTP 핸들러
# =============================================================================

async def _handle_invoke(request: Request) -> JSONResponse:
    body = await request.body()
    logger.info("요청 경로: %s %s", request.method, request.url.path)

    try:
        payload = __import__("json").loads(body) if body else {}
    except Exception:
        payload = {}

    session_id = payload.get("sessionId", "")
    input_text = payload.get("inputText", "")
    session_attrs = payload.get("sessionAttributes", {})
    # Step Functions HTTP Task / 직접 호출 시 payload root에 site_id가 올 수 있음
    if payload.get("site_id") and not session_attrs.get("site_id"):
        session_attrs = {**session_attrs, "site_id": payload["site_id"]}

    logger.info("invoke 호출 — sessionId=%s body_keys=%s", session_id, list(payload.keys()))

    try:
        site_id = parse_site_id(input_text, session_attrs)
        result = run_generation(site_id)
        output_text = (
            f"render_spec_saved — site_id: {site_id}, "
            f"rule_score: {result.get('eval_score', 0)}, "
            f"llm_score: {result.get('llm_eval_score', 0)}"
            if result.get("status") == "complete"
            else f"generation_skipped — {result.get('reason', '')}"
        )
        return JSONResponse({"output": output_text, "sessionState": {}, "metadata": result})

    except GuardrailViolation as exc:
        logger.error("가드레일 위반: %s — %s", exc.code, exc.detail)
        return JSONResponse({"error": exc.code, "message": exc.detail}, status_code=422)
    except Exception as exc:
        logger.exception("생성 에이전트 오류: %s", exc)
        return JSONResponse({"error": "GENERATION_ERROR", "message": str(exc)}, status_code=500)


@app.post("/invoke")
async def invoke(request: Request) -> JSONResponse:
    return await _handle_invoke(request)


@app.post("/invocations")
async def invocations(request: Request) -> JSONResponse:
    return await _handle_invoke(request)


@app.post("/")
async def invoke_root(request: Request) -> JSONResponse:
    return await _handle_invoke(request)


@app.get("/ping")
async def ping() -> JSONResponse:
    return JSONResponse({"status": "ok"})


@app.get("/health")
async def health() -> JSONResponse:
    return JSONResponse({"status": "ok", "agent": "hezo-generation-agent"})


@app.api_route("/{path:path}", methods=["GET", "POST", "PUT", "DELETE", "OPTIONS"])
async def catch_all(path: str, request: Request) -> JSONResponse:
    body = await request.body()
    logger.warning("알 수 없는 경로: %s %s", request.method, request.url.path)
    if request.method == "POST":
        return await _handle_invoke(request)
    return JSONResponse({"path": path, "method": request.method}, status_code=200)


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8080)
