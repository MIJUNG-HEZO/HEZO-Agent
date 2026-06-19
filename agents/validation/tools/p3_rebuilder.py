"""
검증 에이전트가 P3 빌드 워커를 직접 재트리거하는 도구.
P3가 ECS 상시 서비스로 운영되므로 HTTP POST로 호출.
"""
from __future__ import annotations

import logging
import os

import httpx

logger = logging.getLogger(__name__)

P3_BUILD_ENDPOINT = os.environ.get("P3_BUILD_ENDPOINT", "")
_TIMEOUT = 600.0  # 최대 10분


def trigger_and_wait(site_id: str, mode: str = "publish") -> bool:
    """
    P3 빌드 워커 HTTP 엔드포인트를 호출하고 완료까지 대기.
    반환: True = 성공, False = 실패
    """
    if not P3_BUILD_ENDPOINT:
        logger.error("P3_BUILD_ENDPOINT 환경변수 없음 — 재빌드 건너뜀")
        return False

    url = f"{P3_BUILD_ENDPOINT}/invocations"
    payload = {
        "sessionId": f"validation-rebuild-{site_id}",
        "inputText": f"site_id={site_id} mode={mode}",
        "sessionAttributes": {"site_id": site_id, "mode": mode},
    }

    try:
        logger.info("P3 재빌드 요청 — url=%s, site_id=%s, mode=%s", url, site_id, mode)
        resp = httpx.post(url, json=payload, timeout=_TIMEOUT)
        resp.raise_for_status()

        body = resp.json()
        if "error" in body:
            logger.error("P3 재빌드 오류 응답: %s", body)
            return False

        logger.info("P3 재빌드 완료 — output=%s", body.get("output", ""))
        return True

    except httpx.HTTPStatusError as exc:
        logger.error("P3 재빌드 HTTP 오류: %s %s", exc.response.status_code, exc.response.text)
        return False
    except Exception as exc:
        logger.error("P3 재빌드 실패: %s", exc)
        return False
