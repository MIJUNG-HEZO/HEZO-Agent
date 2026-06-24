"""
P3 빌드 워커 — preview / publish 모드 진입점

mode="preview":
  render_spec → HTML(noindex 포함) → hezo-artifacts/.../preview/ → preview_url 반환

mode="publish":
  render_spec → HTML → hezo-sites/.../dist/ + GEO 파일 4종 → dist_prefix 반환
"""
from __future__ import annotations

import logging
import os

from bs4 import BeautifulSoup

from agents.build.renderer.html_renderer import render
from agents.build.renderer.template_map import resolve_template_filename
from agents.build.tools.geo_file_writer import write_geo_files
from agents.build.tools.render_spec_loader import load_render_spec
from agents.build.tools.site_uploader import upload_preview, upload_site

logger = logging.getLogger(__name__)

_TEMPLATES_ROOT = os.path.join(os.path.dirname(__file__), "templates")


def _load_template_html(category: str, template_id: str) -> str:
    filename = resolve_template_filename(template_id, category)
    path = os.path.join(_TEMPLATES_ROOT, category, f"{filename}.html")
    if not os.path.exists(path):
        # category 폴더 fallback (landing 기본)
        fallback_path = os.path.join(_TEMPLATES_ROOT, "landing", f"{filename}.html")
        if os.path.exists(fallback_path):
            logger.warning("템플릿 경로 fallback: %s → %s", path, fallback_path)
            path = fallback_path
        else:
            raise FileNotFoundError(f"템플릿 파일 없음: {path}")
    with open(path, encoding="utf-8") as f:
        return f.read()


def _extract_css_needed(html: str) -> list[str]:
    """렌더링된 HTML에서 ./static/*.css 참조 파일명 추출."""
    soup = BeautifulSoup(html, "html.parser")
    css_files = []
    for link in soup.find_all("link", rel="stylesheet"):
        href = link.get("href", "")
        if href.startswith("./static/"):
            css_files.append(href.split("/")[-1])
    return css_files


def run(site_id: str, mode: str = "publish") -> dict:
    """
    P3 빌드 워커 메인 함수.

    Args:
        site_id: 대상 사이트 ID
        mode: "preview" | "publish"

    Returns:
        preview → { mode, site_id, preview_url, uploaded_files }
        publish → { mode, site_id, dist_prefix, uploaded_files, geo_files }
    """
    if mode not in ("preview", "publish"):
        raise ValueError(f"유효하지 않은 mode: {mode!r} — 'preview' 또는 'publish'여야 함")

    is_preview = (mode == "preview")
    logger.info("P3 빌드 워커 시작 — site_id=%s, mode=%s", site_id, mode)

    # 1. render_spec 로드 (preview/배포 경로 분리)
    loaded = load_render_spec(site_id, is_preview=is_preview)
    render_spec = loaded["render_spec"]
    template_category = loaded["template_category"]
    template_id = render_spec.get("template_id", "")

    # 2. 템플릿 HTML 로드
    template_html = _load_template_html(template_category, template_id)
    logger.info("템플릿 로드: category=%s, template_id=%s", template_category, template_id)

    # 3. HTML 렌더링 (preview 시 noindex 삽입)
    html = render(template_html, render_spec, is_preview=is_preview)
    logger.info("HTML 렌더링 완료: %d bytes", len(html.encode()))

    # 4. CSS 목록 추출
    css_needed = _extract_css_needed(html)

    # 5. 모드별 업로드
    if is_preview:
        result = upload_preview(site_id, html, css_needed)
        logger.info("프리뷰 완료 — preview_url=%s, files=%d",
                    result["preview_url"], len(result["uploaded_files"]))
        return {
            "mode": "preview",
            "site_id": site_id,
            "preview_url": result["preview_url"],
            "uploaded_files": result["uploaded_files"],
        }
    else:
        result = upload_site(site_id, html, css_needed)
        geo_files = write_geo_files(site_id, render_spec)
        logger.info("최종 빌드 완료 — dist_prefix=%s, files=%d, geo=%d",
                    result["dist_prefix"], len(result["uploaded_files"]), len(geo_files))
        return {
            "mode": "publish",
            "site_id": site_id,
            "dist_prefix": result["dist_prefix"],
            "uploaded_files": result["uploaded_files"],
            "geo_files": geo_files,
        }
