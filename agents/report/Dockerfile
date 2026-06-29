FROM --platform=linux/arm64 python:3.12-slim

WORKDIR /app

COPY agents/report/requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY agents/ ./agents/
COPY libs/ ./libs/

ENV PYTHONPATH=/app
ENV AWS_DEFAULT_REGION=ap-northeast-2
ENV MODEL_ID=global.anthropic.claude-haiku-4-5-20251001-v1:0
ENV ARTIFACTS_BUCKET=hezo-artifacts
ENV SITE_BUCKET=hezo-sites
ENV REPORTS_BUCKET=hezo-reports
ENV WIKI_BUCKET=hezo-wiki
ENV PIPELINE_STATE_TABLE=hezo_pipeline_state
ENV OPENAI_API_KEY=
ENV PERPLEXITY_API_KEY=
ENV REPORT_SCORES_TABLE=report_scores
ENV CF_LOG_BUCKET=hezo-cloudfront-logs
ENV GOOGLE_SEARCH_API_KEY=

EXPOSE 8080

CMD ["uvicorn", "agents.report.agent:app", "--host", "0.0.0.0", "--port", "8080"]
