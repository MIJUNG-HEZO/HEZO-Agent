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

# 검수 미달 재시도 백오프: attempts별 다음 시도까지 지연(일). 4회+부터 30일 고정.
# 무인 운영 — 포기(failed) 대신 status=pending 유지 + 간격만 벌려 비용을 통제한다.
# (일시적 실패는 곧 풀리고, 구조적 실패도 한 달에 한 번 정도만 헛돌아 비용 미미)
_BACKOFF_DAYS: dict[int, int] = {1: 1, 2: 3, 3: 7}
_BACKOFF_DEFAULT_DAYS = 30


def _backoff_days(attempts: int) -> int:
    return _BACKOFF_DAYS.get(attempts, _BACKOFF_DEFAULT_DAYS)


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

    def reject(self, domain: str, *, now: int | None = None) -> int:
        """검수 미달: 무인 백오프 재시도. 새 attempts 반환.

        status를 pending으로 되돌리고(다음 PickDomains 쿼리에 다시 잡히게) next_refresh_at을
        attempts별 백오프(1·3·7·30일)만큼 미래로 민다. 사람 개입 없이 자동 재시도하되,
        실패가 쌓일수록 간격이 벌어져 비용이 통제된다(포기/failed 전이 없음).

        재큐잉 메커니즘 = SQS가 아니라 DDB 속성 변경 — pending + (지난) next_refresh_at이면
        due-index 쿼리에 다시 포함된다. 백오프 동안은 next_refresh_at이 미래라 안 잡힌다.
        """
        now = _now_epoch() if now is None else now
        item = self._table.get_item(Key={"domain": domain}).get("Item") or {}
        attempts = int(_to_native(item.get("attempts", 0))) + 1
        self._table.update_item(
            Key={"domain": domain},
            UpdateExpression=(
                "SET #st = :pending, attempts = :a, next_refresh_at = :nra, last_updated = :lu"
            ),
            ExpressionAttributeNames={"#st": "status"},
            ExpressionAttributeValues={
                ":pending": STATUS_PENDING,
                ":a": attempts,
                ":nra": now + _backoff_days(attempts) * _DAY,
                ":lu": _now_iso(),
            },
        )
        return attempts
