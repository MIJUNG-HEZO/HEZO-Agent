"""빌드 결과물을 hezo-sites S3에 업로드"""
from __future__ import annotations

import logging
import os

from agents.shared.s3_utils import SITE_BUCKET, validate_site_id, write_text

logger = logging.getLogger(__name__)

# CSS 파일명 → 필요한 템플릿 판별용 (html_renderer가 이미 경로 패치함)
_CSS_FILES = ["template-expansion.css", "blog-premium.css", "templates.css", "templates-v2.css"]

# 빌드 에이전트 내부 templates/ 경로
_TEMPLATES_ROOT = os.path.join(os.path.dirname(__file__), "..", "templates")


def _read_local_css(filename: str) -> str | None:
    path = os.path.join(_TEMPLATES_ROOT, "static", filename)
    if os.path.exists(path):
        with open(path, encoding="utf-8") as f:
            return f.read()
    return None


def upload_site(site_id: str, html: str, css_needed: list[str]) -> dict:
    """
    dist/index.html + 필요한 static CSS 파일을 hezo-sites에 업로드.
    css_needed: 업로드할 CSS 파일명 목록 (e.g. ["template-expansion.css"])
    반환: { uploaded_files, dist_prefix }
    """
    site_id = validate_site_id(site_id)
    prefix = f"sites/{site_id}/dist"
    uploaded: list[str] = []

    # index.html
    html_key = f"{prefix}/index.html"
    write_text(SITE_BUCKET, html_key, html, content_type="text/html; charset=utf-8")
    uploaded.append(html_key)
    logger.info("index.html 업로드: %s (%d bytes)", html_key, len(html.encode()))

    # CSS 파일
    for css_name in css_needed:
        css_content = _read_local_css(css_name)
        if css_content is None:
            logger.warning("CSS 파일 없음: %s", css_name)
            continue
        css_key = f"{prefix}/static/{css_name}"
        write_text(SITE_BUCKET, css_key, css_content, content_type="text/css; charset=utf-8")
        uploaded.append(css_key)
        logger.info("CSS 업로드: %s", css_key)

    return {
        "uploaded_files": uploaded,
        "dist_prefix": prefix,
        "site_bucket": SITE_BUCKET,
    }
