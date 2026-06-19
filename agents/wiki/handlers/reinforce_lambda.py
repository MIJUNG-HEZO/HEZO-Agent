"""보강 A 람다 핸들러 (얇은 어댑터) — 트리거에서 pending_key만 뽑아 reinforce() 호출.

S3 이벤트 / SQS / S3-이벤트-in-SQS 어느 형태든 pending 경로(키)만 추출해 순수 로직
reinforce()로 넘긴다. 비교·검수·저장 로직은 0줄. 트리거가 바뀌어도 코어는 안 바뀐다.
"""
from __future__ import annotations

import json
from typing import Any
from urllib.parse import unquote_plus

from agents.wiki.reinforce import reinforce


def _extract_key(event: dict[str, Any]) -> str:
    """S3 이벤트 / SQS / S3-in-SQS 에서 pending 경로 추출."""
    rec = event["Records"][0]
    if "s3" in rec:  # S3 이벤트 직접
        return unquote_plus(rec["s3"]["object"]["key"])
    body = json.loads(rec["body"])  # SQS
    if isinstance(body, dict) and "Records" in body:  # S3 이벤트가 SQS에 래핑됨
        return unquote_plus(body["Records"][0]["s3"]["object"]["key"])
    return body["key"]  # 평범한 SQS 메시지 {"key": ...}


def handler(event: dict[str, Any], context: Any = None) -> dict:
    return reinforce(_extract_key(event))
