FROM python:3.12-slim

WORKDIR /app

COPY agents/validation/requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY agents/ ./agents/

ENV AWS_DEFAULT_REGION=ap-northeast-2
ENV MODEL_ID=global.anthropic.claude-sonnet-4-6
ENV ARTIFACTS_BUCKET=hezo-artifacts
ENV SITE_BUCKET=hezo-sites

EXPOSE 8080

CMD ["uvicorn", "agents.validation.agent:app", "--host", "0.0.0.0", "--port", "8080"]
