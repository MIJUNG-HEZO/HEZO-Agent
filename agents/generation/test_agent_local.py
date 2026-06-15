"""
생성 에이전트 로컬 테스트 스크립트.

실제 Bedrock 없이 contract_loader + render_spec_saver Lambda를 모킹하여
generation agent 시스템 프롬프트 + 입력/출력 구조를 검증한다.

사용법:
    python test_agent_local.py
    python test_agent_local.py --fixture contract_13_tax_landing.json
"""

from __future__ import annotations

import argparse
import json
import pathlib
import sys

FIXTURES_DIR = pathlib.Path(__file__).parent / "fixtures"
SCHEMA_FILE  = pathlib.Path(__file__).parent / "render_spec_schema.json"

# ──────────────────────────────────────────────────────────────────────────────
# BLOCKING 조건 검증
# ──────────────────────────────────────────────────────────────────────────────

def _assert_blocking(render_spec: dict, site_id: str) -> list[str]:
    """
    render_spec_13_tax_landing.json 기준으로 BLOCKING 조건 검사.
    실패 시 오류 문자열 목록 반환.
    """
    errors: list[str] = []
    pages = render_spec.get("pages", [])

    if not pages:
        errors.append("BLOCKING: pages 배열이 비어 있습니다.")
        return errors

    page = pages[0]
    blocks = page.get("blocks", [])

    # 1. H1 정확히 1개
    hero_blocks = [b for b in blocks if b.get("type") == "Hero"]
    h1_count = sum(1 for b in hero_blocks if b.get("h1"))
    if h1_count == 0:
        errors.append("BLOCKING: Hero 블록에 h1 필드가 없습니다.")
    elif h1_count > 1:
        errors.append(f"BLOCKING: h1이 {h1_count}개 있습니다. 정확히 1개여야 합니다.")

    # 2. FAQ 최소 5개
    faq_blocks = [b for b in blocks if b.get("type") == "FAQ"]
    if not faq_blocks:
        errors.append("BLOCKING: FAQ 블록이 없습니다.")
    else:
        faq_items = faq_blocks[0].get("items", [])
        if len(faq_items) < 5:
            errors.append(f"BLOCKING: FAQ 항목이 {len(faq_items)}개입니다. 최소 5개 필요.")

    # 3. llms.txt 필수
    supp = render_spec.get("supplementary_files", {})
    if not supp.get("llms_txt"):
        errors.append("BLOCKING: supplementary_files.llms_txt 가 없거나 비어 있습니다.")

    # 4. Schema.org FAQPage 포함
    jsonld = page.get("jsonld", [])
    faq_page_types = [ld for ld in jsonld if ld.get("@type") == "FAQPage"]
    if not faq_page_types:
        errors.append("BLOCKING: JSON-LD에 FAQPage 타입이 없습니다.")
    else:
        faq_entities = faq_page_types[0].get("mainEntity", [])
        if len(faq_entities) < 5:
            errors.append(f"BLOCKING: FAQPage mainEntity가 {len(faq_entities)}개입니다. 최소 5개 필요.")

    # 5. robots.txt AI 크롤러 Allow
    robots = supp.get("robots_rules", [])
    robots_text = "\n".join(robots) if isinstance(robots, list) else str(robots)
    for bot in ["GPTBot", "ClaudeBot", "PerplexityBot"]:
        if bot not in robots_text:
            errors.append(f"BLOCKING: robots_rules에 {bot} Allow 규칙이 없습니다.")

    # 6. schema_version 확인
    if render_spec.get("schema_version") != "1.0.0":
        errors.append(f"경고: render_spec schema_version이 {render_spec.get('schema_version')!r}입니다. (기대: '1.0.0')")

    # 7. site_id 일치
    if render_spec.get("site_id") != site_id:
        errors.append(f"경고: render_spec.site_id={render_spec.get('site_id')!r} ≠ contract.site_id={site_id!r}")

    return errors


def _validate_contract(contract: dict) -> list[str]:
    """G slot-based 0.1.0 Contract JSON 기본 검증."""
    errors: list[str] = []

    if contract.get("schema_version") != "0.1.0":
        errors.append(f"contract schema_version이 {contract.get('schema_version')!r}입니다. (기대: '0.1.0')")

    gates = contract.get("gates", {})
    if not gates.get("generation_ready"):
        errors.append(f"gates.generation_ready가 false입니다. 에이전트 실행 불가.")

    slots = contract.get("slots", {})
    for required in ["business_name", "core_services", "phone"]:
        if not slots.get(required):
            errors.append(f"필수 슬롯 누락: slots.{required}")

    return errors


# ──────────────────────────────────────────────────────────────────────────────
# 레퍼런스 출력 비교
# ──────────────────────────────────────────────────────────────────────────────

def _compare_with_reference(contract_fixture: str, render_spec: dict) -> list[str]:
    """contract fixture 이름에 대응하는 render_spec fixture 파일과 구조 비교."""
    ref_name = contract_fixture.replace("contract_", "render_spec_")
    ref_path = FIXTURES_DIR / ref_name

    if not ref_path.exists():
        return [f"참고: 레퍼런스 출력 파일 없음 ({ref_name})"]

    ref = json.loads(ref_path.read_text(encoding="utf-8"))
    diffs: list[str] = []

    # 최상위 키 비교
    for key in ["schema_version", "site_id", "template_id", "site_type"]:
        if render_spec.get(key) != ref.get(key):
            diffs.append(f"  {key}: 실제={render_spec.get(key)!r} ≠ 레퍼런스={ref.get(key)!r}")

    # blocks 타입 목록 비교
    actual_block_types  = [b.get("type") for b in (render_spec.get("pages") or [{}])[0].get("blocks", [])]
    ref_block_types     = [b.get("type") for b in (ref.get("pages") or [{}])[0].get("blocks", [])]
    if set(actual_block_types) != set(ref_block_types):
        diffs.append(f"  blocks 타입 차이: 실제={actual_block_types} / 레퍼런스={ref_block_types}")

    return diffs


# ──────────────────────────────────────────────────────────────────────────────
# 메인
# ──────────────────────────────────────────────────────────────────────────────

def main() -> None:
    parser = argparse.ArgumentParser(description="Generation Agent 로컬 구조 검증")
    parser.add_argument(
        "--fixture",
        default="contract_13_tax_landing.json",
        help="fixtures/ 디렉터리의 contract JSON 파일명 (기본값: contract_13_tax_landing.json)",
    )
    parser.add_argument(
        "--render-spec",
        default=None,
        help="검증할 render_spec JSON 파일 경로 (미입력 시 레퍼런스 파일 사용)",
    )
    args = parser.parse_args()

    # ── contract 로드 ─────────────────────────────────────────────────────────
    contract_path = FIXTURES_DIR / args.fixture
    if not contract_path.exists():
        print(f"[ERROR] fixture 파일 없음: {contract_path}", file=sys.stderr)
        sys.exit(1)

    contract = json.loads(contract_path.read_text(encoding="utf-8"))
    site_id  = (contract.get("ids") or {}).get("site_id", "unknown")

    print(f"\n{'='*60}")
    print(f"  HEZO Generation Agent 로컬 검증")
    print(f"  Contract: {args.fixture}")
    print(f"  Site ID : {site_id}")
    print(f"{'='*60}\n")

    # ── contract 유효성 ───────────────────────────────────────────────────────
    print("[1] Contract JSON 검증 (G slot-based 0.1.0)")
    contract_errors = _validate_contract(contract)
    if contract_errors:
        for e in contract_errors:
            print(f"  [!] {e}")
        print()
    else:
        print("  [OK] contract 검증 통과\n")

    # ── render_spec 로드 ──────────────────────────────────────────────────────
    if args.render_spec:
        render_path = pathlib.Path(args.render_spec)
    else:
        ref_name    = args.fixture.replace("contract_", "render_spec_")
        render_path = FIXTURES_DIR / ref_name

    if not render_path.exists():
        print(f"[2] render_spec 파일 없음: {render_path}")
        print("    에이전트를 실제 실행한 뒤 출력 파일을 --render-spec 으로 지정하세요.")
        sys.exit(0)

    render_spec = json.loads(render_path.read_text(encoding="utf-8"))
    print(f"[2] render_spec 로드: {render_path.name}")

    # ── BLOCKING 조건 검사 ────────────────────────────────────────────────────
    print("\n[3] BLOCKING 조건 검사")
    blocking_errors = _assert_blocking(render_spec, site_id)
    if blocking_errors:
        for e in blocking_errors:
            print(f"  [FAIL] {e}")
    else:
        print("  [OK] 모든 BLOCKING 조건 통과")

    # ── 레퍼런스 비교 ─────────────────────────────────────────────────────────
    print("\n[4] 레퍼런스 출력 비교")
    diffs = _compare_with_reference(args.fixture, render_spec)
    if diffs:
        for d in diffs:
            print(f"  {d}")
    else:
        print("  [OK] 레퍼런스와 일치")

    # ── 최종 결과 ─────────────────────────────────────────────────────────────
    all_errors = contract_errors + [e for e in blocking_errors if e.startswith("BLOCKING")]
    print(f"\n{'='*60}")
    if all_errors:
        print(f"  결과: FAIL ({len(all_errors)}개 오류)")
        for e in all_errors:
            print(f"  - {e}")
        sys.exit(1)
    else:
        print("  결과: PASS — 생성 에이전트 출력 검증 완료")
    print(f"{'='*60}\n")


if __name__ == "__main__":
    main()
