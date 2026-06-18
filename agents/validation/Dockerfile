FROM --platform=linux/arm64 python:3.12-slim

WORKDIR /app

COPY agents/validation/requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY agents/ ./agents/
COPY libs/ ./libs/

ENV AWS_DEFAULT_REGION=ap-northeast-2
ENV MODEL_ID=global.anthropic.claude-sonnet-4-6
ENV ARTIFACTS_BUCKET=hezo-artifacts
ENV SITE_BUCKET=hezo-sites
ENV WIKI_BUCKET=hezo-wiki
ENV MAX_PATCH_ATTEMPTS=3
ENV ECS_CLUSTER=hezo-cluster
ENV BUILD_TASK_DEFINITION=hezo-build-worker
ENV ECS_SUBNETS=
ENV ECS_SECURITY_GROUPS=

EXPOSE 8080

CMD ["uvicorn", "agents.validation.agent:app", "--host", "0.0.0.0", "--port", "8080"]
