"""HEZO Wiki (P2) DynamoDB 색인 접근 계층 (hezo_wiki_index).

본문(S3) 옆에서 "최신·상태·만료" 메타를 담는 색인. **본문 없음, 메타만.**
boto3 래퍼는 팀 패턴(agents/chat/chat_state_store.py)을 따른다.

아이템 스키마 (PK=domain):
  domain · category · template_no · template_id · s3_key · latest_version ·
  status(pending|crawling|done|rejected|failed) · confidence · volatility ·
  last_updated(ISO8601) · next_refresh_at(epoch) · source_urls · attempts
GSI due-index (PK=status, SK=next_refresh_at) — pending/만료 done 쿼리.

신선도 TTL (commit 시 next_refresh_at = now + TTL): 모든 도메인 15일 균일 (#194, 무제한 폐지).

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
# 갱신 주기 = 모든 도메인 15일 균일 (2026-06-22 결정, #194). volatility별 차등·"무제한(never)"
# 폐지 — 어떤 도메인도 영영 안 갱신하면 위키가 낡으므로, 모두 최소 2주마다 최신화한다.
REFRESH_DAYS = 15

# 크롤 claim lease(초): claim 시 next_refresh_at=now+이값으로 둬 "처리 중"을 표시한다.
# 한 파이프라인 실행은 실측 최대 ~2분이라 30분이면 정상 처리분을 실수로 재선점하지 않는다.
# 실행이 commit/reject로 끝나면 next_refresh_at이 덮어써지고, 크래시·SFn死로 끝을 못 내면
# lease 만료(next_refresh_at<=now) → due("crawling")가 stale로 잡아 재선점(자가복구).
CRAWL_LEASE_SECONDS = 30 * 60

# 검수 미달 재시도 백오프: attempts별 다음 시도까지 지연(일). 4회+부터 30일 고정.
# 무인 운영 — 포기(failed) 대신 status=pending 유지 + 간격만 벌려 비용을 통제한다.
# (일시적 실패는 곧 풀리고, 구조적 실패도 한 달에 한 번 정도만 헛돌아 비용 미미)
_BACKOFF_DAYS: dict[int, int] = {1: 1, 2: 3, 3: 7}
_BACKOFF_DEFAULT_DAYS = 30


def _backoff_days(attempts: int) -> int:
    return _BACKOFF_DAYS.get(attempts, _BACKOFF_DEFAULT_DAYS)


class ConcurrencyConflict(Exception):
    """commit 시 latest_version이 기대값과 달라 조건부 쓰기 실패(동시 수정 감지)."""


def _now_epoch() -> int:
    return int(time.time())


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def next_refresh_at(volatility: str | None = None, now: int | None = None) -> int:
    """다음 갱신 시각(epoch) = now + 15일 (모든 도메인 균일, #194).

    volatility 인자는 호환 위해 받기만 하고 쓰지 않는다(차등 폐지). 백오프(reject 재시도)는
    이와 별개로 _backoff_days가 담당한다.
    """
    now = _now_epoch() if now is None else now
    return now + REFRESH_DAYS * _DAY


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

    def claim(self, domain: str, *, now: int | None = None,
              lease_seconds: int = CRAWL_LEASE_SECONDS) -> bool:
        """status=crawling으로 찜 + lease(next_refresh_at=now+lease) 기록.

        선점 가능 조건: pending·done이거나, crawling이어도 lease가 만료(next_refresh_at<=now)된
        경우(=처리하다 죽어 방치된 stale). 활성 crawling(lease 미만)이면 False(중복/동시 선점 차단).
        lease 덕에 크래시·SFn死로 갇힌 도메인도 lease 만료 후 재선점된다.
        """
        now = _now_epoch() if now is None else now
        try:
            self._table.update_item(
                Key={"domain": domain},
                UpdateExpression="SET #st = :crawling, next_refresh_at = :lease",
                ConditionExpression=(
                    "attribute_exists(#dom) AND (#st <> :crawling OR next_refresh_at <= :now)"
                ),
                ExpressionAttributeNames={"#st": "status", "#dom": "domain"},
                ExpressionAttributeValues={
                    ":crawling": STATUS_CRAWLING,
                    ":lease": now + lease_seconds,
                    ":now": now,
                },
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
        check_version: bool = False,
        expected_version: str | None = None,
    ) -> dict:
        """저장 성공 후 done 전이 + 최신버전·신뢰도·출처·만료 갱신.

        check_version=True면 낙관적 동시성(CAS): 현재 latest_version이 expected_version과
        같을 때만 갱신(첫 저장은 expected_version=None → latest_version 없을 때만). 그새
        다른 쓰기가 끼면 ConcurrencyConflict를 올린다(호출부가 재읽기·재시도).
        """
        e = catalog.get_entry(domain)
        nra = next_refresh_at(e["volatility"], now)
        values = {
            ":done": STATUS_DONE,
            ":lv": latest_version,
            ":conf": Decimal(str(confidence)),
            ":urls": source_urls,
            ":lu": _now_iso(),
            ":nra": nra,
        }
        kwargs: dict[str, Any] = {
            "Key": {"domain": domain},
            "UpdateExpression": (
                "SET #st = :done, latest_version = :lv, confidence = :conf, "
                "source_urls = :urls, last_updated = :lu, next_refresh_at = :nra"
            ),
            "ExpressionAttributeNames": {"#st": "status"},
            "ExpressionAttributeValues": values,
            "ReturnValues": "ALL_NEW",
        }
        if check_version:
            if expected_version is None:
                kwargs["ConditionExpression"] = "attribute_not_exists(latest_version)"
            else:
                kwargs["ConditionExpression"] = "latest_version = :expv"
                values[":expv"] = expected_version
        try:
            resp = self._table.update_item(**kwargs)
        except Exception as error:  # ConditionalCheckFailedException 포함
            if type(error).__name__ == "ConditionalCheckFailedException":
                raise ConcurrencyConflict(domain) from error
            raise
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
