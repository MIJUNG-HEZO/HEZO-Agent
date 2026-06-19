"""render_spec.supplementary_files → GEO 파일 4종 → hezo-sites 사이트 버킷.

최종 빌드(publish) 시에만 호출. 프리뷰에서는 호출하지 않음.
"""
from __future__ import annotations

import logging
from urllib.parse import urlparse

from agents.shared.s3_utils import SITE_BUCKET, validate_site_id, write_text

logger = logging.getLogger(__name__)


def _build_sitemap_xml(sitemap_pages: list[dict], base_url: str) -> str:
    base = base_url.rstrip("/")
    lines = [
        '<?xml version="1.0" encoding="UTF-8"?>',
        '<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">',
    ]
    for page in sitemap_pages:
        path = page.get("path", "/")
        priority = page.get("priority", 0.5)
        changefreq = page.get("changefreq", "monthly")
        lines += [
            "  <url>",
            f"    <loc>{base}{path}</loc>",
            f"    <priority>{priority}</priority>",
            f"    <changefreq>{changefreq}</changefreq>",
            "  </url>",
        ]
    lines.append("</urlset>")
    return "\n".join(lines)


def write_geo_files(site_id: str, render_spec: dict) -> list[str]:
    """
    render_spec.supplementary_files → hezo-sites/sites/{id}/dist/ GEO 파일 4종 저장.
    반환: 저장된 S3 키 목록
    """
    site_id = validate_site_id(site_id)
    supp = render_spec.get("supplementary_files", {})
    if not supp:
        logger.warning("supplementary_files 없음 — GEO 파일 생성 건너뜀")
        return []

    # base_url: pages[0].seo.canonical 에서 파싱, 실패 시 fallback
    try:
        canonical = render_spec["pages"][0]["seo"]["canonical"]
        parsed = urlparse(canonical)
        base_url = f"{parsed.scheme}://{parsed.netloc}"
    except (KeyError, IndexError, TypeError):
        base_url = f"https://{site_id}.doodo.cloud"
        logger.warning("canonical URL 파싱 실패 — fallback 사용: %s", base_url)

    prefix = f"sites/{site_id}/dist"
    saved: list[str] = []

    if llms_txt := supp.get("llms_txt", ""):
        key = f"{prefix}/llms.txt"
        write_text(SITE_BUCKET, key, llms_txt)
        saved.append(key)
        logger.info("llms.txt 저장: s3://%s/%s", SITE_BUCKET, key)

    if llms_full := supp.get("llms_full_txt", ""):
        key = f"{prefix}/llms-full.txt"
        write_text(SITE_BUCKET, key, llms_full)
        saved.append(key)
        logger.info("llms-full.txt 저장: s3://%s/%s", SITE_BUCKET, key)

    if sitemap_pages := supp.get("sitemap_pages", []):
        # llms-full.txt가 sitemap에 없으면 자동 추가
        if not any(p.get("path") == "/llms-full.txt" for p in sitemap_pages):
            sitemap_pages = list(sitemap_pages) + [
                {"path": "/llms-full.txt", "priority": 0.8, "changefreq": "monthly"}
            ]
        xml = _build_sitemap_xml(sitemap_pages, base_url)
        key = f"{prefix}/sitemap.xml"
        write_text(SITE_BUCKET, key, xml, "application/xml")
        saved.append(key)
        logger.info("sitemap.xml 저장: s3://%s/%s", SITE_BUCKET, key)

    if robots_rules := supp.get("robots_rules", []):
        # Yeti(Naver Cue 크롤러)가 없으면 자동 추가
        if not any("Yeti" in r for r in robots_rules):
            robots_rules = list(robots_rules) + ["", "User-agent: Yeti", "Allow: /"]
        key = f"{prefix}/robots.txt"
        write_text(SITE_BUCKET, key, "\n".join(robots_rules))
        saved.append(key)
        logger.info("robots.txt 저장: s3://%s/%s", SITE_BUCKET, key)

    logger.info("GEO 파일 %d종 저장 완료", len(saved))
    return saved
