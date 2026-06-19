FROM --platform=linux/arm64 python:3.12-slim

WORKDIR /app

COPY agents/build/requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY agents/ ./agents/
COPY libs/ ./libs/

ENV PYTHONPATH=/app
ENV AWS_DEFAULT_REGION=ap-northeast-2
ENV ARTIFACTS_BUCKET=hezo-artifacts
ENV SITE_BUCKET=hezo-sites

EXPOSE 8080

CMD ["uvicorn", "agents.build.agent:app", "--host", "0.0.0.0", "--port", "8080"]
