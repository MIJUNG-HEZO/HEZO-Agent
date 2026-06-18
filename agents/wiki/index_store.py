"""HEZO Wiki (P2) DynamoDB 색인 접근 계층 (hezo_wiki_index).

본문(S3) 옆에서 "최신·상태·만료" 메타를 담는 색인. **본문 없음, 메타만.**
boto3 래퍼는 팀 패턴(agents/chat/chat_state_store.py)을 따른다.

아이템 스키마 (PK=domain):
  domain · category · template_no · template_id · s3_key · latest_version ·
  status(pending|crawling|done|rejected|failed) · confidence · volatility ·
  last_updated(ISO8601) · next_refresh_at(epoch) · source_urls · attempts
GSI due-index (PK=status, SK=next_refresh_at) — pending/만료 done 쿼리.

신선도 TTL (commit 시 next_refresh_at = now + TTL): high 7일 / mid 30일 / low 만료없음.

credential은 코드/깃에 두지 않는다. 프로필/리전은 constants(환경변수)로만 참조.
"""
from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal
import time
from typing import Any

from agents.wiki.constants import AWS_PROFILE, AWS_REGION, WIKI_INDEX_TABLE, industry_key
from agents.wiki import catalog

# ─── 상태 / TTL ─────────────────────────────────────────────────────────────
STATUS_PENDING = "pending"
STATUS_CRAWLING = "crawling"
STATUS_DONE = "done"
STATUS_REJECTED = "rejected"
STATUS_FAILED = "failed"

_DAY = 86_400
TTL_DAYS: dict[str, int | None] = {"high": 7, "mid": 30, "low": None}
# low(만료 없음): due-index에는 남되 절대 만료로 안 잡히도록 먼 미래 epoch 사용.
NEVER_REFRESH = 9_999_999_999


def _now_epoch() -> int:
    return int(time.time())


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def next_refresh_at(volatility: str, now: int | None = None) -> int:
    """volatility별 다음 갱신 시각(epoch). low는 NEVER_REFRESH."""
    now = _now_epoch() if now is None else now
    days = TTL_DAYS.get(volatility, 30)  # 알 수 없는 값은 mid로
    return NEVER_REFRESH if days is None else now + days * _DAY


def _to_native(value: Any) -> Any:
    """DynamoDB resource가 돌려주는 Decimal을 int/float로 변환."""
    if isinstance(value, Decimal):
        return int(value) if value % 1 == 0 else float(value)
    if isinstance(value, list):
        return [_to_native(v) for v in value]
    if isinstance(value, dict):
        return {k: _to_native(v) for k, v in value.items()}
    return value


class WikiIndexStore:
    """hezo_wiki_index DynamoDB 접근 계층 (boto3 resource)."""

    def __init__(
        self,
        table: Any | None = None,
        table_name: str | None = None,
        region_name: str | None = None,
    ) -> None:
        self._table_name = table_name or WIKI_INDEX_TABLE
        if table is not None:
            self._table = table
            return
        try:
            import boto3  # type: ignore[import-not-found]
        except ImportError as error:
            raise RuntimeError("boto3_required_for_wiki_index_store") from error

        session_kwargs: dict[str, str] = {}
        if AWS_PROFILE:
            session_kwargs["profile_name"] = AWS_PROFILE
        session = boto3.Session(**session_kwargs)
        dynamodb = session.resource("dynamodb", region_name=region_name or AWS_REGION)
        self._table = dynamodb.Table(self._table_name)

    # ─── 조회 ───────────────────────────────────────────────────────────────
    def get(self, domain: str) -> dict | None:
        """도메인 메타 조회. 없으면 None."""
        resp = self._table.get_item(Key={"domain": domain})
        item = resp.get("Item")
        return _to_native(item) if item else None

    def due(self, status: str, limit: int, now: int | None = None) -> list[dict]:
        """due-index로 처리 대상 조회.

        pending(next_refresh_at=0) 또는 done 중 만료(next_refresh_at<=now)를
        next_refresh_at 오름차순으로 limit개. status별로 호출한다.
        """
        now = _now_epoch() if now is None else now
        resp = self._table.query(
            IndexName="due-index",
            KeyConditionExpression="#st = :s AND next_refresh_at <= :now",
            ExpressionAttributeNames={"#st": "status"},
            ExpressionAttributeValues={":s": status, ":now": now},
            ScanIndexForward=True,  # 오래된(작은 next_refresh_at) 먼저
            Limit=limit,
        )
        return [_to_native(it) for it in resp.get("Items", [])]

    # ─── 등록 / 전이 ──────────────────────────────────────────────────────────
    def register(self, domain: str, status: str = STATUS_PENDING) -> dict:
        """카탈로그 기준 초기 등록(작업④ 초기화에서 사용). pending=next_refresh_at 0."""
        e = catalog.get_entry(domain)
        item = {
            "domain": domain,
            "category": e["category"],
            "template_no": e["template_no"],
            "template_id": e["template_id"],
            "volatility": e["volatility"],
            "s3_key": industry_key(e["category"], domain),
            "status": status,
            "confidence": Decimal("0"),
            "attempts": 0,
            "next_refresh_at": 0,
            "last_updated": _now_iso(),
        }
        self._table.put_item(Item=item)
        return _to_native(item)

    def claim(self, domain: str) -> bool:
        """status=crawling으로 찜. 이미 crawling이면(중복) False."""
        try:
            self._table.update_item(
                Key={"domain": domain},
                UpdateExpression="SET #st = :crawling",
                ConditionExpression="attribute_exists(#dom) AND #st <> :crawling",
                ExpressionAttributeNames={"#st": "status", "#dom": "domain"},
                ExpressionAttributeValues={":crawling": STATUS_CRAWLING},
            )
            return True
        except Exception as error:  # ConditionalCheckFailedException 포함
            if type(error).__name__ == "ConditionalCheckFailedException":
                return False
            raise

    def commit(
        self,
        domain: str,
        *,
        latest_version: str,
        confidence: float,
        source_urls: list[str],
        now: int | None = None,
    ) -> dict:
        """저장 성공 후 done 전이 + 최신버전·신뢰도·출처·만료 갱신."""
        e = catalog.get_entry(domain)
        nra = next_refresh_at(e["volatility"], now)
        resp = self._table.update_item(
            Key={"domain": domain},
            UpdateExpression=(
                "SET #st = :done, latest_version = :lv, confidence = :conf, "
                "source_urls = :urls, last_updated = :lu, next_refresh_at = :nra"
            ),
            ExpressionAttributeNames={"#st": "status"},
            ExpressionAttributeValues={
                ":done": STATUS_DONE,
                ":lv": latest_version,
                ":conf": Decimal(str(confidence)),
                ":urls": source_urls,
                ":lu": _now_iso(),
                ":nra": nra,
            },
            ReturnValues="ALL_NEW",
        )
        return _to_native(resp.get("Attributes", {}))

    def reject(self, domain: str) -> int:
        """검수 미달: attempts 증가(status 유지). 새 attempts 반환."""
        resp = self._table.update_item(
            Key={"domain": domain},
            UpdateExpression="SET last_updated = :lu ADD attempts :one",
            ExpressionAttributeValues={":one": 1, ":lu": _now_iso()},
            ReturnValues="ALL_NEW",
        )
        return int(_to_native(resp.get("Attributes", {})).get("attempts", 0))
