"""빌드 결과물을 S3에 업로드 — 프리뷰(hezo-artifacts) / 최종 빌드(hezo-sites) 분리"""
from __future__ import annotations

import logging
import os

from agents.shared.s3_utils import ARTIFACTS_BUCKET, SITE_BUCKET, validate_site_id, write_text

logger = logging.getLogger(__name__)

PREVIEW_URL_BASE = os.environ.get("PREVIEW_URL_BASE", "https://preview.hezo.app")

# 빌드 에이전트 내부 templates/ 경로
_TEMPLATES_ROOT = os.path.join(os.path.dirname(__file__), "..", "templates")


def _read_local_css(filename: str) -> str | None:
    path = os.path.join(_TEMPLATES_ROOT, "static", filename)
    if os.path.exists(path):
        with open(path, encoding="utf-8") as f:
            return f.read()
    return None


def _upload_html_and_css(bucket: str, prefix: str, html: str, css_needed: list[str]) -> list[str]:
    """HTML + CSS를 지정 버킷/경로에 업로드. 업로드된 키 목록 반환."""
    uploaded: list[str] = []

    html_key = f"{prefix}/index.html"
    write_text(bucket, html_key, html, content_type="text/html; charset=utf-8")
    uploaded.append(html_key)
    logger.info("index.html 업로드: s3://%s/%s (%d bytes)", bucket, html_key, len(html.encode()))

    for css_name in css_needed:
        css_content = _read_local_css(css_name)
        if css_content is None:
            logger.warning("CSS 파일 없음: %s", css_name)
            continue
        css_key = f"{prefix}/static/{css_name}"
        write_text(bucket, css_key, css_content, content_type="text/css; charset=utf-8")
        uploaded.append(css_key)
        logger.info("CSS 업로드: s3://%s/%s", bucket, css_key)

    return uploaded


def upload_preview(site_id: str, html: str, css_needed: list[str]) -> dict:
    """
    프리뷰 HTML + CSS → hezo-artifacts/sites/{id}/preview/ 업로드.
    반환: { preview_url, uploaded_files, preview_prefix, artifacts_bucket }
    """
    site_id = validate_site_id(site_id)
    prefix = f"sites/{site_id}/preview"
    uploaded = _upload_html_and_css(ARTIFACTS_BUCKET, prefix, html, css_needed)
    preview_url = f"{PREVIEW_URL_BASE}/{site_id}"
    logger.info("프리뷰 업로드 완료: %s", preview_url)
    return {
        "preview_url": preview_url,
        "uploaded_files": uploaded,
        "preview_prefix": prefix,
        "artifacts_bucket": ARTIFACTS_BUCKET,
    }


def upload_site(site_id: str, html: str, css_needed: list[str]) -> dict:
    """
    최종 빌드 HTML + CSS → hezo-sites/sites/{id}/dist/ 업로드.
    반환: { uploaded_files, dist_prefix, site_bucket }
    """
    site_id = validate_site_id(site_id)
    prefix = f"sites/{site_id}/dist"
    uploaded = _upload_html_and_css(SITE_BUCKET, prefix, html, css_needed)
    return {
        "uploaded_files": uploaded,
        "dist_prefix": prefix,
        "site_bucket": SITE_BUCKET,
    }
