"""render_spec.json 및 GEO 파일 4종을 S3에 저장하는 내부 도구"""
from __future__ import annotations

import logging
import time
from datetime import datetime, timezone
from typing import Any
from urllib.parse import urlparse

from agents.shared.s3_utils import (
    ARTIFACTS_BUCKET,
    SITE_BUCKET,
    write_json,
    write_text,
    validate_site_id,
)

logger = logging.getLogger(__name__)


def _build_sitemap_xml(sitemap_pages: list[dict], base_url: str) -> str:
    base = base_url.rstrip("/")
    lines = [
        '<?xml version="1.0" encoding="UTF-8"?>',
        '<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">',
    ]
    for page in sitemap_pages:
        path = page.get("path", "/")
        loc = base + path
        priority = page.get("priority", 0.5)
        changefreq = page.get("changefreq", "monthly")
        lines += [
            "  <url>",
            f"    <loc>{loc}</loc>",
            f"    <priority>{priority}</priority>",
            f"    <changefreq>{changefreq}</changefreq>",
            "  </url>",
        ]
    lines.append("</urlset>")
    return "\n".join(lines)


def _save_geo_files(site_id: str, supp: dict) -> list[str]:
    """supplementary_files → hezo-sites/{site_id}/ GEO 4종 저장. 저장된 키 목록 반환."""
    if not supp:
        logger.warning("supplementary_files 없음 — GEO 파일 저장 건너뜀")
        return []

    prefix = f"sites/{site_id}"
    saved: list[str] = []

    if llms_txt := supp.get("llms_txt", ""):
        write_text(SITE_BUCKET, f"{prefix}/llms.txt", llms_txt)
        saved.append(f"{prefix}/llms.txt")

    if llms_full := supp.get("llms_full_txt", ""):
        write_text(SITE_BUCKET, f"{prefix}/llms-full.txt", llms_full)
        saved.append(f"{prefix}/llms-full.txt")

    if sitemap_pages := supp.get("sitemap_pages", []):
        try:
            canonical = supp.get("base_url") or ""
            base_url = canonical or f"https://{site_id}.hezo.io"
        except Exception:
            base_url = f"https://{site_id}.hezo.io"
        xml = _build_sitemap_xml(sitemap_pages, base_url)
        write_text(SITE_BUCKET, f"{prefix}/sitemap.xml", xml, "application/xml")
        saved.append(f"{prefix}/sitemap.xml")

    if robots_rules := supp.get("robots_rules", []):
        write_text(SITE_BUCKET, f"{prefix}/robots.txt", "\n".join(robots_rules))
        saved.append(f"{prefix}/robots.txt")

    logger.info("GEO 파일 %d종 저장 완료: %s", len(saved), saved)
    return saved


def save_render_spec(site_id: str, render_spec: dict[str, Any]) -> dict[str, Any]:
    """
    render_spec.json → hezo-artifacts/sites/{site_id}/render_spec.json
    GEO 파일 4종    → hezo-sites/sites/{site_id}/
    반환: {s3_key, size_bytes, geo_files, saved_at}
    """
    site_id = validate_site_id(site_id)

    # site_id 일관성 보정
    if render_spec.get("site_id") and render_spec["site_id"] != site_id:
        logger.warning("site_id 불일치: render_spec=%s vs param=%s, 파라미터 값 사용",
                       render_spec["site_id"], site_id)
        render_spec["site_id"] = site_id

    render_spec["_saved_at"] = datetime.now(timezone.utc).isoformat()

    start = time.monotonic()
    key = f"sites/{site_id}/render_spec.json"
    size = write_json(ARTIFACTS_BUCKET, key, render_spec,
                      metadata={"site-id": site_id, "saved-by": "hezo-generation-agent"})
    logger.info("render_spec.json 저장: %.1f ms", (time.monotonic() - start) * 1000)

    supp = render_spec.get("supplementary_files", {})
    geo_files = _save_geo_files(site_id, supp)

    return {
        "s3_key": key,
        "s3_bucket": ARTIFACTS_BUCKET,
        "size_bytes": size,
        "page_count": len(render_spec.get("pages", [])),
        "geo_files": geo_files,
        "geo_bucket": SITE_BUCKET,
        "saved_at": render_spec["_saved_at"],
        "status": "saved",
    }
