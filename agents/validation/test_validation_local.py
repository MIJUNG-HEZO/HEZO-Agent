"""
P4 검증 에이전트 로컬 테스트
- P2 세무 도메인 지식 MD → parse_wiki_md()로 wiki_snapshot 변환 (hezo-wiki 대역)
- S3 없이 픽스처 직접 주입 → 3층 검증 실행
- Layer 1: hezo-wiki 토픽 커버리지 (Bedrock 호출, 실패 시 자동 skip)
- Layer 2: 결정론적 Python 규칙
- Layer 3: BeautifulSoup HTML 파싱
"""
from __future__ import annotations

import json
import logging
import pathlib
from datetime import datetime, timezone

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger("hezo.validation.test")

# P2 세무 도메인 MD 경로 (로컬)
_P2_MD_PATH = pathlib.Path(__file__).parents[3] / (
    "3. 에이전트 개발/에이전트 개발 3- P1~5 분업 PRD"
    "/P2 - HEZO LLM Wiki 자동 보강 시스템"
    "/크롤링 데이터 + 정제 md/세무_도메인지식_크롤샘플.md"
)


# ─────────────────────────────────────────────────────────────────────────────
# 픽스처 정의
# ─────────────────────────────────────────────────────────────────────────────

CONTRACT = {
    "ids": {"site_id": "test-tax-001"},
    "template": {
        "template_id": "tax_landing_v1",
        "template_category": "landing",
    },
    "slots": {
        "business_name": "서울 세무사 사무소",
        "business_type": "tax_accounting",
        "business_region": "서울 강남구",
        "phone": "02-1234-5678",
        "kakao": "seoulcpa",
        "address": "서울 강남구 테헤란로 123, 5층",
        "hours": "평일 09:00~18:00 (토요일 10:00~14:00)",
        "services": ["법인세 신고", "부가가치세 신고", "종합소득세 신고", "세무조정", "기장대리"],
        "required_sections": ["hero", "services", "faq", "contact"],
    },
    "gates": {"generation_ready": True, "preview_ready": True},
}

RENDER_SPEC = {
    "schema_version": "1.0.0",
    "site_id": "test-tax-001",
    "template_id": "tax_landing_v1",
    "pages": [
        {
            "path": "/",
            "blocks": [
                {"type": "Hero", "h1": "강남 세무사 사무소"},
                {"type": "Services", "items": []},
                {"type": "FAQ", "items": []},
                {"type": "Contact", "phone": "02-1234-5678"},
            ],
        }
    ],
}

HTML_CONTENT = """<!DOCTYPE html>
<html lang="ko">
<head>
  <meta charset="UTF-8">
  <title>강남 세무사 사무소 — 법인세·부가세·종소세 전문 | HEZO</title>
  <meta name="description" content="강남구 소재 세무사 사무소. 법인세·부가가치세·종합소득세 신고대리, 세무조정, 기장대리 서비스.">
  <link rel="canonical" href="https://tax-landing-v1.hezo.io/">
  <script type="application/ld+json">
  {"@context":"https://schema.org","@type":"Accountant","name":"서울 세무사 사무소","telephone":"02-1234-5678"}
  </script>
  <script type="application/ld+json">
  {
    "@context": "https://schema.org",
    "@type": "FAQPage",
    "mainEntity": [
      {"@type":"Question","name":"법인세 신고 준비","acceptedAnswer":{"@type":"Answer","text":"사업연도 종료일로부터 3개월 이내 신고·납부. 세무조정계산서 필요."}},
      {"@type":"Question","name":"부가가치세 세율","acceptedAnswer":{"@type":"Answer","text":"일반과세자 10%, 간이과세자 1.5~4% (업종별 부가가치율 적용)."}},
      {"@type":"Question","name":"종합소득세 신고기한","acceptedAnswer":{"@type":"Answer","text":"매년 5월 1일~31일. 성실신고확인서 대상자 6월 30일."}},
      {"@type":"Question","name":"세무조정 의무 법인","acceptedAnswer":{"@type":"Answer","text":"수입금액 70억원 이상 또는 외부감사 대상 법인."}},
      {"@type":"Question","name":"기장대리 혜택","acceptedAnswer":{"@type":"Answer","text":"장부 미기장 시 산출세액 20% 가산세 방지."}}
    ]
  }
  </script>
</head>
<body>
  <h1>강남 세무사 사무소 — 법인세·부가세·종소세 전문</h1>
  <div data-hezo="quick-answer">서울 강남구 세무사 사무소. 법인세·부가세·종소세 전문.</div>
  <section id="services">
    <h2>주요 서비스</h2>
    <p>법인세 신고대리 (사업연도 종료 3개월 이내)</p>
    <p>부가가치세 신고 — 일반과세자 10%, 간이과세자 1.5~4%</p>
    <p>종합소득세 신고 — 5월 31일까지</p>
    <p>세무조정·기장대리 — 복식부기 의무자 전담</p>
  </section>
  <section id="contact">
    <h2>상담 신청</h2>
    <p>전화: 02-1234-5678</p>
  </section>
</body>
</html>"""

FILE_LIST = ["index.html", "style.css", "llms.txt", "llms-full.txt", "sitemap.xml", "robots.txt"]


# ─────────────────────────────────────────────────────────────────────────────
# wiki_snapshot 로드 (P2 MD 파싱)
# ─────────────────────────────────────────────────────────────────────────────

def load_wiki_snapshot() -> dict | None:
    """P2 MD를 parse_wiki_md()로 파싱해 wiki_snapshot 반환 (hezo-wiki S3 대역)."""
    from agents.validation.tools.wiki_parser import parse_wiki_md

    if not _P2_MD_PATH.exists():
        logger.warning("P2 MD 파일 없음: %s", _P2_MD_PATH)
        return None

    md_content = _P2_MD_PATH.read_text(encoding="utf-8")
    snapshot = parse_wiki_md(md_content)
    logger.info(
        "wiki_snapshot 파싱 완료: domain=%s topics=%d",
        snapshot["domain"], len(snapshot["topics"]),
    )
    return snapshot


# ─────────────────────────────────────────────────────────────────────────────
# 테스트 실행
# ─────────────────────────────────────────────────────────────────────────────

def run_validation_test():
    from agents.validation.rule_engine.layer1_info_preservation import check_layer1
    from agents.validation.rule_engine.layer2_requirements import check_layer2
    from agents.validation.rule_engine.layer3_ai_friendly import check_layer3
    from agents.validation.evaluators.ai_visibility_scorer import calculate_ai_score

    wiki_snapshot = load_wiki_snapshot()

    print("\n" + "=" * 70)
    print("  P4 검증 에이전트 로컬 테스트")
    print(f"  site_id   : {CONTRACT['ids']['site_id']}")
    print(f"  업종      : {CONTRACT['slots']['business_type']} ({CONTRACT['slots']['business_name']})")
    if wiki_snapshot:
        print(f"  wiki MD   : {wiki_snapshot['domain']} — {len(wiki_snapshot['topics'])}개 토픽 파싱")
        for t in wiki_snapshot["topics"]:
            print(f"    · {t['title']}  {t['key_terms'][:3]}")
    else:
        print("  wiki MD   : 없음 (Layer 1 skip)")
    print("=" * 70)

    all_issues: list[dict] = []

    # ── Layer 1: 도메인 토픽 커버리지 (Bedrock LLM) ─────────────────────────
    print("\n[Layer 1] 도메인 토픽 커버리지 검증 (hezo-wiki vs 생성 HTML) — Bedrock...")
    layer1: list[dict] = []
    if wiki_snapshot:
        layer1 = check_layer1(CONTRACT, wiki_snapshot, HTML_CONTENT)
    else:
        print("  → wiki_snapshot 없음 — skip")
    all_issues.extend(layer1)
    _print_layer_result("Layer 1 도메인 커버리지", layer1)

    # ── Layer 2: 요구사항 정합성 (결정론적) ──────────────────────────────────
    print("\n[Layer 2] 요구사항 정합성 검증 (결정론적)...")
    layer2 = check_layer2(CONTRACT, RENDER_SPEC)
    all_issues.extend(layer2)
    _print_layer_result("Layer 2 요구사항 정합성", layer2)

    # ── Layer 3: AI 친화 구조 (BeautifulSoup) ────────────────────────────────
    print("\n[Layer 3] AI 친화 구조 검증 (HTML 파싱)...")
    layer3 = check_layer3(HTML_CONTENT, FILE_LIST)
    all_issues.extend(layer3)
    _print_layer_result("Layer 3 AI 친화 구조", layer3)

    # ── 최종 판정 ─────────────────────────────────────────────────────────────
    ai_score = calculate_ai_score(all_issues)
    blocking = [i for i in all_issues if i.get("level") == "blocking"]
    warnings = [i for i in all_issues if i.get("level") == "warning"]

    status = "FAIL_BLOCKING" if blocking else ("PASS_WITH_WARNINGS" if warnings else "PASS")

    print("\n" + "=" * 70)
    print("  최종 판정 결과")
    print("=" * 70)
    print(f"  판정 상태       : {status}")
    print(f"  AI 가시성 점수  : {ai_score} / 100")
    print(f"  Blocking 이슈   : {len(blocking)}건")
    print(f"  Warning 이슈    : {len(warnings)}건")
    print(f"  배포 가능 여부  : {'YES' if not blocking else 'NO'}")

    print("\n  전체 이슈 목록:")
    if not all_issues:
        print("  → 이슈 없음 (완벽)")
    for issue in all_issues:
        icon = "[BLOCK]" if issue["level"] == "blocking" else "[WARN] "
        print(f"  {icon} {issue['code']}: {issue['detail']}")

    report = {
        "site_id": CONTRACT["ids"]["site_id"],
        "validation_status": status,
        "publish_ready": len(blocking) == 0,
        "ai_visibility_score": ai_score,
        "blocking_issues": blocking,
        "warnings": warnings,
        "checked_at": datetime.now(timezone.utc).isoformat() + "Z",
    }

    print("\n  validation_report.json:")
    print(json.dumps(report, ensure_ascii=False, indent=2))
    print("=" * 70)
    return report


def _print_layer_result(layer_name: str, issues: list[dict]) -> None:
    if not issues:
        print(f"  OK {layer_name}: 이슈 없음")
    else:
        print(f"  !! {layer_name}: {len(issues)}건")
        for i in issues:
            tag = "[BLOCK]" if i["level"] == "blocking" else "[WARN] "
            print(f"     {tag} {i['code']}: {i['detail']}")


if __name__ == "__main__":
    run_validation_test()
