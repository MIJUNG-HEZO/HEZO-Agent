FROM --platform=linux/arm64 python:3.12-slim

WORKDIR /app

COPY agents/chat/requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY agents/ ./agents/
COPY libs/ ./libs/

ENV PYTHONPATH=/app
ENV AWS_DEFAULT_REGION=ap-northeast-2
ENV AWS_REGION=ap-northeast-2
ENV MODEL_ID=global.anthropic.claude-sonnet-4-5-20250929-v1:0
ENV HEZO_BEDROCK_MODEL_ID=anthropic.claude-sonnet-4-5-20250929-v1:0
ENV HEZO_BEDROCK_INFERENCE_PROFILE_ID=global.anthropic.claude-sonnet-4-5-20250929-v1:0
ENV HEZO_CHAT_BUCKET=hezo-chat
ENV HEZO_P2_MARKDOWNS_BUCKET=hezo-wiki
ENV HEZO_ENRICHED_MARKDOWNS_BUCKET=hezo-wiki-staging
ENV HEZO_CONTRACTS_BUCKET=hezo-artifacts
ENV HEZO_AGENT_DYNAMODB_TABLE=hezo_agent_chat
ENV P2_MARKDOWNS_BUCKET=hezo-wiki
ENV ENRICHED_MARKDOWNS_BUCKET=hezo-wiki-staging
ENV CONTRACTS_BUCKET=hezo-artifacts
ENV CHAT_STATE_TABLE=hezo_agent_chat
ENV HEZO_BEDROCK_GUARDRAIL_ID=q8dcjc2um846
ENV HEZO_BEDROCK_GUARDRAIL_VERSION=DRAFT
ENV BEDROCK_GUARDRAIL_ID=q8dcjc2um846
ENV BEDROCK_GUARDRAIL_VERSION=DRAFT

EXPOSE 8080

CMD ["uvicorn", "agents.chat.agent:app", "--host", "0.0.0.0", "--port", "8080"]
