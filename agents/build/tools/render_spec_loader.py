"""render_spec.json + contract_final.json을 S3에서 로드"""
from __future__ import annotations

import logging

from agents.shared.s3_utils import ARTIFACTS_BUCKET, read_json, validate_site_id

logger = logging.getLogger(__name__)


def load_render_spec(site_id: str, is_preview: bool = False) -> dict:
    """
    render_spec.json 로드.

    Args:
        site_id: 대상 사이트 ID
        is_preview: True면 preview/render_spec.json, False면 render_spec.json

    반환: { "render_spec": dict, "template_category": str }
    """
    site_id = validate_site_id(site_id)

    # ✅ Preview와 배포용 경로 분리
    spec_key = f"sites/{site_id}/preview/render_spec.json" if is_preview else f"sites/{site_id}/render_spec.json"
    render_spec = read_json(ARTIFACTS_BUCKET, spec_key)
    mode_label = "preview" if is_preview else "publish"
    logger.info("render_spec 로드 완료 — site_id=%s, mode=%s, template_id=%s, path=%s",
                site_id, mode_label, render_spec.get("template_id", "unknown"), spec_key)

    # template_category: render_spec에 있으면 바로 사용, 없으면 contract에서 조회
    template_category = render_spec.get("template_category", "")
    if not template_category:
        try:
            contract = read_json(ARTIFACTS_BUCKET, f"sites/{site_id}/contract_final.json")
            tmpl = contract.get("template", {})
            template_category = (
                tmpl.get("site_type")
                or tmpl.get("category")
                or tmpl.get("template_category")
                or "landing"
            )
            logger.info("contract에서 template_category 조회: %s", template_category)
        except Exception as exc:
            logger.warning("contract 로드 실패, 기본값 'landing' 사용: %s", exc)
            template_category = "landing"

    return {
        "render_spec": render_spec,
        "template_category": template_category,
    }
