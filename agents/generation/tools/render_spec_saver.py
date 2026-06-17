"""render_spec.json을 S3 아티팩트 버킷에 저장하는 내부 도구.

GEO 파일 4종(llms.txt, llms-full.txt, sitemap.xml, robots.txt)은 P3 렌더링 워커가 생성.
생성 에이전트는 supplementary_files 데이터를 render_spec.json에 포함시켜 P3에 전달할 뿐임.
"""
from __future__ import annotations

import logging
import time
from datetime import datetime, timezone
from typing import Any

from agents.shared.s3_utils import (
    ARTIFACTS_BUCKET,
    write_json,
    validate_site_id,
)

logger = logging.getLogger(__name__)


def save_render_spec(site_id: str, render_spec: dict[str, Any]) -> dict[str, Any]:
    """
    render_spec.json → hezo-artifacts/sites/{site_id}/render_spec.json
    반환: {s3_key, s3_bucket, size_bytes, page_count, saved_at, status}
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

    return {
        "s3_key": key,
        "s3_bucket": ARTIFACTS_BUCKET,
        "size_bytes": size,
        "page_count": len(render_spec.get("pages", [])),
        "saved_at": render_spec["_saved_at"],
        "status": "saved",
    }
