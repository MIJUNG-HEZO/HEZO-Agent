"""traffic_analyzer 단위 테스트 — S3 없이 내부 파싱 로직만 검증"""
import gzip
import io
from unittest.mock import MagicMock, patch

from agents.report.tools.traffic_analyzer import (
    _parse_referrer_visits,
    analyze_traffic,
    AI_REFERRERS,
)


def _make_gz_log(lines: list[str]) -> bytes:
    """CloudFront 로그 형식 gz 바이트 생성"""
    header = "#Fields: date time x-edge-location sc-bytes c-ip cs-method cs(Host) cs-uri-stem sc-status cs(Referer) cs(User-Agent) cs-uri-query\n"
    content = header + "\n".join(lines) + "\n"
    buf = io.BytesIO()
    with gzip.GzipFile(fileobj=buf, mode="wb") as f:
        f.write(content.encode("utf-8"))
    return buf.getvalue()


SAMPLE_LINES = [
    # Perplexity 레퍼러 — 실제 사용자
    "2026-06-26\t10:00:00\tICN57-P4\t1000\t1.2.3.4\tGET\texample.com\t/\t200\thttps://perplexity.ai/search?q=test\tMozilla/5.0\t-",
    # ChatGPT 레퍼러 — 실제 사용자
    "2026-06-26\t10:00:01\tICN57-P4\t1000\t1.2.3.5\tGET\texample.com\t/\t200\thttps://chatgpt.com/c/abc123\tMozilla/5.0\t-",
    # 봇 UA → 제외해야 함
    "2026-06-26\t10:00:02\tICN57-P4\t1000\t1.2.3.6\tGET\texample.com\t/\t200\thttps://perplexity.ai/\tPerplexityBot/1.0\t-",
    # 레퍼러 없음 (-) — 집계 안 됨
    "2026-06-26\t10:00:03\tICN57-P4\t1000\t1.2.3.7\tGET\texample.com\t/\t200\t-\tMozilla/5.0\t-",
]


def test_parse_referrer_visits_excludes_bots():
    """봇 UA가 있는 요청은 제외한다"""
    gz_bytes = _make_gz_log(SAMPLE_LINES)
    mock_s3 = MagicMock()
    mock_s3.get_object.return_value = {"Body": MagicMock(read=lambda: gz_bytes)}

    with patch("agents.report.tools.traffic_analyzer._get_s3", return_value=mock_s3):
        pairs = _parse_referrer_visits("DIST/log.gz")

    referrers = [r for r, _ in pairs]
    assert "https://perplexity.ai/search?q=test" in referrers   # real user included
    assert "https://chatgpt.com/c/abc123" in referrers           # real user included
    perplexity_refs = [r for r in referrers if "perplexity.ai" in r]
    assert len(perplexity_refs) == 1   # PerplexityBot UA excluded, only 1 real user


def test_analyze_traffic_no_distribution_id():
    """cf_distribution_id 없으면 configured=False 반환"""
    result = analyze_traffic("")
    assert result["configured"] is False
    assert result["total_ai_traffic"] == 0
    assert set(result["visits"].keys()) == set(AI_REFERRERS.keys())


def test_analyze_traffic_no_logs():
    """S3에 로그 없으면 configured=False"""
    mock_s3 = MagicMock()
    mock_s3.list_objects_v2.return_value = {"Contents": []}

    with patch("agents.report.tools.traffic_analyzer._get_s3", return_value=mock_s3):
        result = analyze_traffic("E38E4K9DA2XEDN")

    assert result["configured"] is False
    assert result["total_ai_traffic"] == 0


def test_analyze_traffic_counts_correctly():
    """Perplexity 1회, ChatGPT 1회 → total=2, bot 제외"""
    from datetime import datetime, timezone
    gz_bytes = _make_gz_log(SAMPLE_LINES)
    mock_s3 = MagicMock()
    mock_s3.list_objects_v2.return_value = {
        "Contents": [{"Key": "DIST/log.gz", "LastModified": datetime(2026, 6, 26, tzinfo=timezone.utc)}]
    }
    mock_s3.get_object.return_value = {"Body": MagicMock(read=lambda: gz_bytes)}

    with patch("agents.report.tools.traffic_analyzer._get_s3", return_value=mock_s3):
        result = analyze_traffic("DIST")

    assert result["configured"] is True
    assert result["visits"]["Perplexity"] == 1   # 봇 1개 제외, 사용자 1개
    assert result["visits"]["ChatGPT"] == 1
    assert result["visits"]["Claude"] == 0
    assert result["total_ai_traffic"] == 2
