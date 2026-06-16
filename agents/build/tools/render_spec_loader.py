"""render_spec.json + contract_final.json을 S3에서 로드"""
from __future__ import annotations

import logging

from agents.shared.s3_utils import ARTIFACTS_BUCKET, read_json, validate_site_id

logger = logging.getLogger(__name__)


def load_render_spec(site_id: str) -> dict:
    """
    hezo-artifacts/sites/{site_id}/render_spec.json 로드.
    반환: { "render_spec": dict, "template_category": str }
    """
    site_id = validate_site_id(site_id)

    render_spec = read_json(ARTIFACTS_BUCKET, f"sites/{site_id}/render_spec.json")
    logger.info("render_spec 로드 완료 — site_id=%s, template_id=%s",
                site_id, render_spec.get("template_id", "unknown"))

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
