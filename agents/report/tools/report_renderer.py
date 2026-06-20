"""
Jinja2로 주간 HTML 리포트 렌더링.
PDF는 추후 구현 — MVP는 HTML만.
"""
from __future__ import annotations

import logging
from datetime import datetime

from jinja2 import BaseLoader, Environment

logger = logging.getLogger(__name__)

_TEMPLATE = """<!DOCTYPE html>
<html lang="ko">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>HEZO AI 준비도 리포트 — {{ report_date }}</title>
<style>
  body { font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', sans-serif; max-width: 800px; margin: 0 auto; padding: 24px; color: #1a1a1a; }
  h1 { font-size: 22px; font-weight: 700; margin-bottom: 4px; }
  .subtitle { color: #666; font-size: 14px; margin-bottom: 32px; }
  .card { background: #f8f9fa; border-radius: 12px; padding: 20px; margin-bottom: 16px; }
  .card h2 { font-size: 15px; font-weight: 600; margin: 0 0 12px; }
  .score-big { font-size: 48px; font-weight: 700; color: #2563eb; }
  .score-label { font-size: 13px; color: #666; }
  .delta-up { color: #16a34a; } .delta-down { color: #dc2626; }
  .check-row { display: flex; align-items: center; gap: 8px; font-size: 14px; padding: 4px 0; }
  .ok { color: #16a34a; } .fail { color: #dc2626; } .warn { color: #d97706; }
  .bot-row { display: flex; justify-content: space-between; font-size: 14px; padding: 4px 0; border-bottom: 1px solid #e5e7eb; }
  .action-red { background: #fee2e2; border-left: 4px solid #dc2626; padding: 10px 14px; border-radius: 4px; margin-bottom: 8px; font-size: 14px; }
  .action-yellow { background: #fef3c7; border-left: 4px solid #d97706; padding: 10px 14px; border-radius: 4px; margin-bottom: 8px; font-size: 14px; }
  .action-green { background: #dcfce7; border-left: 4px solid #16a34a; padding: 10px 14px; border-radius: 4px; margin-bottom: 8px; font-size: 14px; }
  .grid-2 { display: grid; grid-template-columns: 1fr 1fr; gap: 12px; }
  .metric { background: white; border-radius: 8px; padding: 12px; }
  .metric-label { font-size: 12px; color: #666; margin-bottom: 4px; }
  .metric-value { font-size: 20px; font-weight: 600; }
  footer { margin-top: 40px; font-size: 12px; color: #999; text-align: center; }
</style>
</head>
<body>

<h1>HEZO AI 준비도 리포트</h1>
<div class="subtitle">{{ business_name }} · {{ report_date }} · <a href="{{ domain_url }}">{{ domain_url }}</a></div>

<div class="card">
  <h2>한눈에 보기</h2>
  <div class="score-big">{{ overall_score }}</div>
  <div class="score-label">/ 100점
    {% if delta > 0 %}<span class="delta-up"> ▲{{ delta }}점 상승</span>
    {% elif delta < 0 %}<span class="delta-down"> ▼{{ delta | abs }}점 하락</span>
    {% else %} (변화 없음){% endif %}
  </div>
</div>

<div class="card">
  <h2>AI 봇 방문 현황 (최근 7일)</h2>
  {% if not bot_visits.configured %}
  <div class="warn">⚠ CloudFront 로그 미설정 — 봇 방문 추적이 활성화되지 않았습니다</div>
  {% else %}
  {% for bot, count in bot_visits.visits.items() %}
  <div class="bot-row">
    <span>{% if count > 0 %}✓{% else %}…{% endif %} {{ bot }}</span>
    <span>{{ count }}회 방문{% if bot_visits.last_visit_dates[bot] %} (마지막: {{ bot_visits.last_visit_dates[bot] }}){% endif %}</span>
  </div>
  {% endfor %}
  {% endif %}
</div>

<div class="card">
  <h2>AI 읽기 환경 점검</h2>
  <div class="check-row"><span class="{{ 'ok' if geo_file.llms_txt.ok else 'fail' }}">{{ '[O]' if geo_file.llms_txt.ok else '[X]' }}</span> llms.txt{% if geo_file.llms_txt.ok and not geo_file.llms_txt.has_core_pages_section %} <span class="warn">⚠ 핵심 페이지 섹션 없음</span>{% endif %}</div>
  <div class="check-row"><span class="{{ 'ok' if geo_file.llms_full_txt.ok else 'fail' }}">{{ '[O]' if geo_file.llms_full_txt.ok else '[X]' }}</span> llms-full.txt (FAQ {{ geo_file.llms_full_txt.faq_count }}개)</div>
  <div class="check-row"><span class="{{ 'ok' if geo_file.sitemap_xml.ok else 'fail' }}">{{ '[O]' if geo_file.sitemap_xml.ok else '[X]' }}</span> sitemap.xml{% if geo_file.sitemap_xml.ok and not geo_file.sitemap_xml.has_llms_full %} <span class="warn">⚠ llms-full.txt 미포함</span>{% endif %}</div>
  <div class="check-row"><span class="{{ 'ok' if geo_file.robots_txt.ok else 'fail' }}">{{ '[O]' if geo_file.robots_txt.ok else '[X]' }}</span> robots.txt
    ({% for b, ok in geo_file.robots_txt.bots.items() %}<span class="{{ 'ok' if ok else 'fail' }}">{{ b }}</span>{% if not loop.last %}, {% endif %}{% endfor %})
  </div>
  <div class="check-row"><span class="{{ 'ok' if geo_file.jsonld.ok else 'fail' }}">{{ '[O]' if geo_file.jsonld.ok else '[X]' }}</span> JSON-LD ({{ geo_file.jsonld.types_found | join(', ') or '없음' }}){% if not geo_file.jsonld.has_faq_page %} <span class="warn">⚠ FAQPage 없음</span>{% endif %}</div>
</div>

<div class="grid-2">
  <div class="card">
    <h2>구글 인덱싱</h2>
    <div class="metric">
      <div class="metric-label">상태</div>
      <div class="metric-value">{{ '완료' if indexing.indexing_status == 'indexed' else ('진행 중' if indexing.indexing_status == 'likely_indexed' else '대기 중') }}</div>
    </div>
    <div style="font-size:13px; color:#666; margin-top:8px;">발행 후 {{ indexing.days_since_publish }}일 경과 · 인덱싱 확률 {{ indexing.indexing_likelihood_pct }}%</div>
    <div style="font-size:12px; color:#999; margin-top:4px;">{{ indexing.note }}</div>
  </div>
  <div class="card">
    <h2>사이트 성능</h2>
    <div class="metric">
      <div class="metric-label">종합 등급</div>
      <div class="metric-value">{{ performance.performance_grade }}등급</div>
    </div>
    <div style="font-size:13px; color:#666; margin-top:8px;">응답속도 {{ performance.response_ms }}ms · 모바일 {{ performance.mobile_score or 'N/A' }}점 · 데스크탑 {{ performance.desktop_score or 'N/A' }}점</div>
  </div>
</div>

<div class="card">
  <h2>GEO 구조 점수: {{ geo_score.score }}점</h2>
  <div style="background:#e5e7eb; border-radius:4px; height:8px; margin-bottom:12px;">
    <div style="background:#2563eb; border-radius:4px; height:8px; width:{{ geo_score.score }}%;"></div>
  </div>
  {% for issue in geo_score.issues %}
  <div style="font-size:13px; color:#666; padding:2px 0;">• {{ issue }}</div>
  {% endfor %}
</div>

<div class="card">
  <h2>이번 주 개선 액션</h2>
  {% for item in action_items %}
  <div class="action-{{ item.priority }}">
    {% if item.priority == 'red' %}[필수]
    {% elif item.priority == 'yellow' %}[권장]
    {% else %}[잘 됨]{% endif %}
    {{ item.content }}
  </div>
  {% endfor %}
</div>

<footer>
  HEZO AI 준비도 리포트 · 자동 생성 · 다음 측정일: 7일 후<br>
  측정 시각: {{ generated_at }}
</footer>
</body>
</html>"""


def render_html_report(report_data: dict) -> str:
    """report_data → HTML 문자열 반환"""
    env = Environment(loader=BaseLoader())
    template = env.from_string(_TEMPLATE)

    html = template.render(
        business_name=report_data.get("business_name", ""),
        domain_url=report_data.get("domain_url", ""),
        report_date=datetime.now().strftime("%Y년 %m월 %d일"),
        generated_at=report_data.get("generated_at", ""),
        overall_score=report_data.get("overall_score", 0),
        delta=report_data.get("delta", 0),
        bot_visits=report_data.get("bot_visits", {}),
        geo_file=report_data.get("geo_file_check", {}),
        indexing=report_data.get("indexing", {}),
        performance=report_data.get("performance", {}),
        geo_score=report_data.get("geo_structure", {}),
        action_items=report_data.get("action_items", []),
    )
    logger.info("HTML 리포트 렌더링 완료: %d bytes", len(html))
    return html
