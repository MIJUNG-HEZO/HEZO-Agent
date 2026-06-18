"""HEZO Wiki (P2) 초기화 — 카탈로그 60 DDB 등록 + 시드 본문 생성 (순수 로직).

PickDomains가 고를 대상(DDB 등록)이 없으면 파이프라인이 돌지 않으므로, 카탈로그 60개를
DDB에 등록한다. 시드 3개는 ⑤a→⑤b 파이프라인으로 본문을 생성한다(나머지 57은 pending →
일일 크롤이 채움).

실행은 **배포 후 실제 람다/스크립트에서 1회** 돌린다(운영 환경에서 전체 점검하며 생성).
이 모듈은 그 호출 대상이 되는 함수만 제공한다 — 로컬에서 강제로 돌리지 않는다.

멱등: register는 put_item이라 무조건 부르면 done 도메인을 pending으로 덮어쓴다 →
get으로 존재 확인 후 skip한다. 여러 번 돌려도 기존 상태를 깨지 않는다.
"""
from __future__ import annotations

from agents.wiki import catalog
from agents.wiki.collect import collect
from agents.wiki.index_store import WikiIndexStore
from agents.wiki.pipeline import generate_and_store


def register_catalog(index: WikiIndexStore | None = None) -> dict:
    """카탈로그 60개를 DDB에 등록(멱등 — 이미 있으면 skip).

    반환: {registered, skipped, total, registered_domains}
    """
    index = index or WikiIndexStore()
    registered: list[str] = []
    skipped: list[str] = []
    for domain in catalog.all_domains():
        if index.get(domain) is not None:
            skipped.append(domain)  # 이미 있음 → 상태 보존(덮지 않음)
            continue
        index.register(domain)
        registered.append(domain)
    return {
        "registered": len(registered),
        "skipped": len(skipped),
        "total": len(catalog.all_domains()),
        "registered_domains": registered,
    }


def bootstrap_seeds(index: WikiIndexStore | None = None, llm=None, *, num: int = 10) -> list[dict]:
    """시드 3개에 collect()(⑤a) → generate_and_store()(⑤b) 실행. 도메인별 결과 반환.

    실제 Serper(수집)·Bedrock(생성·검수)·S3·DDB I/O가 발생한다(배포 후 운영 환경에서 호출).
    반환: [{domain, category, raw, result}] (result.stage = committed/rejected/...)
    """
    index = index or WikiIndexStore()
    results: list[dict] = []
    for domain in catalog.seed_domains():
        category = catalog.get_entry(domain)["category"]
        crawl = collect(category, domain, num=num)
        out = generate_and_store(category, domain, crawl["raw_key"], llm=llm, index=index)
        results.append({"domain": domain, "category": category, "raw": crawl, "result": out})
    return results


def initialize(index: WikiIndexStore | None = None, llm=None) -> dict:
    """전체 초기화: 60 등록 → 시드 3 본문 생성. 요약 반환. (배포 후 1회 호출)"""
    index = index or WikiIndexStore()
    reg = register_catalog(index=index)
    seeds = bootstrap_seeds(index=index, llm=llm)
    return {"register": reg, "seeds": seeds}


if __name__ == "__main__":  # 배포 후 1회 실행 (env: AWS 자격증명·SERPER_API_KEY·모델 id)
    print("=== 카탈로그 등록 ===")
    reg = register_catalog()
    print(f"등록 {reg['registered']} / 스킵 {reg['skipped']} / 전체 {reg['total']}")
    print("=== 시드 본문 생성 (collect → generate_and_store) ===")
    for r in bootstrap_seeds():
        out = r["result"]
        print(f"  {r['domain']:>16} [{r['category']}]: stage={out['stage']} "
              f"score={out.get('score')} kept={r['raw'].get('kept')}")
    print("초기화 완료")
