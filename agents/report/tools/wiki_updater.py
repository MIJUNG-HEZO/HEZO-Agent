"""
리포트 에이전트 → P2 wiki 업데이트 트리거.

LLM 응답에서 wiki에 없는 최신 정보 발견 시
hezo-wiki/_internal/pending_industries.json에 추가.
"""
from __future__ import annotations

import json
import logging
import os
from datetime import datetime, timezone
from typing import Any

import boto3
from botocore.exceptions import ClientError

logger = logging.getLogger(__name__)

REGION = os.environ.get("AWS_DEFAULT_REGION", "ap-northeast-2")
WIKI_BUCKET = os.environ.get("WIKI_BUCKET", "hezo-wiki")
PENDING_KEY = "_internal/pending_industries.json"

_s3: Any = None


def _get_s3():
    global _s3
    if _s3 is None:
        _s3 = boto3.client("s3", region_name=REGION)
    return _s3


def _load_pending() -> list[dict]:
    try:
        resp = _get_s3().get_object(Bucket=WIKI_BUCKET, Key=PENDING_KEY)
        return json.loads(resp["Body"].read().decode("utf-8"))
    except ClientError as exc:
        if exc.response["Error"]["Code"] in ("NoSuchKey", "404"):
            return []
        raise


def _save_pending(items: list[dict]) -> None:
    body = json.dumps(items, ensure_ascii=False, indent=2).encode("utf-8")
    _get_s3().put_object(
        Bucket=WIKI_BUCKET, Key=PENDING_KEY,
        Body=body, ContentType="application/json; charset=utf-8",
    )


def request_recrawl(domain: str, reason: str, site_id: str) -> bool:
    """
    domain을 P2 재크롤 대기 목록에 추가.
    이미 대기 중이면 reason만 업데이트.
    반환: True = 신규 추가, False = 이미 존재
    """
    if not domain:
        return False

    pending = _load_pending()
    existing = next((p for p in pending if p.get("domain") == domain), None)

    if existing:
        existing["last_reason"] = reason
        existing["triggered_at"] = datetime.now(timezone.utc).isoformat() + "Z"
        _save_pending(pending)
        logger.info("pending_industries 업데이트: domain=%s", domain)
        return False

    pending.append({
        "domain": domain,
        "reason": reason,
        "triggered_by": f"report_agent:{site_id}",
        "triggered_at": datetime.now(timezone.utc).isoformat() + "Z",
        "priority": "normal",
    })
    _save_pending(pending)
    logger.info("pending_industries 추가: domain=%s reason=%s", domain, reason)
    return True


def detect_stale_wiki(llm_responses: dict, wiki_snapshot: dict | None, domain: str) -> str | None:
    """
    LLM 응답과 wiki 내용을 비교해 wiki 갱신이 필요한지 판단.
    반환: 갱신 사유 문자열 | None (갱신 불필요)
    """
    if not wiki_snapshot:
        return f"hezo-wiki '{domain}' 항목 없음"

    # wiki 신선도: last_updated 기준 30일 이상이면 재크롤 권장
    last_updated = wiki_snapshot.get("last_updated", "")
    if last_updated:
        try:
            from datetime import datetime, timezone, timedelta
            updated_dt = datetime.fromisoformat(last_updated.replace("Z", "+00:00"))
            age_days = (datetime.now(timezone.utc) - updated_dt).days
            if age_days > 30:
                return f"wiki 마지막 업데이트 {age_days}일 경과"
        except (ValueError, TypeError):
            pass

    # LLM 응답 중 wiki key_terms에 없는 새 키워드 감지 (간단한 휴리스틱)
    wiki_terms = set()
    for topic in wiki_snapshot.get("topics", []):
        wiki_terms.update(t.lower() for t in topic.get("key_terms", []))

    new_terms_found: list[str] = []
    for llm_name, resp_list in llm_responses.items():
        for resp in (resp_list if isinstance(resp_list, list) else []):
            if not resp:
                continue
            words = set(resp.lower().split())
            # 4글자 이상 단어 중 wiki에 없는 것들 (매우 단순한 휴리스틱)
            new_words = {w for w in words if len(w) >= 4 and w not in wiki_terms}
            new_terms_found.extend(list(new_words)[:3])

    if len(set(new_terms_found)) > 5:
        return f"LLM 응답에서 wiki 미포함 키워드 다수 감지"

    return None
