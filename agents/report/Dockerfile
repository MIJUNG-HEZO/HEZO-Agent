FROM python:3.12-slim

WORKDIR /app

COPY agents/report/requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY agents/ ./agents/

ENV AWS_DEFAULT_REGION=ap-northeast-2
ENV MODEL_ID=global.anthropic.claude-haiku-4-5-20251001
ENV ARTIFACTS_BUCKET=hezo-artifacts
ENV SITE_BUCKET=hezo-sites
ENV SSM_FLAG_KEY=hezo-report-enabled

EXPOSE 8080

CMD ["uvicorn", "agents.report.agent:app", "--host", "0.0.0.0", "--port", "8080"]
