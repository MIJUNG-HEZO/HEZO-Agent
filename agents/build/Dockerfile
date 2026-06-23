FROM --platform=linux/arm64 python:3.12-slim

WORKDIR /app

COPY agents/build/requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY agents/ ./agents/

ENV PYTHONPATH=/app
ENV AWS_DEFAULT_REGION=ap-northeast-2
ENV ARTIFACTS_BUCKET=hezo-artifacts
ENV SITE_BUCKET=hezo-sites

EXPOSE 8080

# ✅ Python import 테스트 (시작 전 의존성 확인)
RUN python3 -c "from agents.build.agent import app; print('✅ P3 앱 import 성공')"

CMD ["uvicorn", "agents.build.agent:app", "--host", "0.0.0.0", "--port", "8080"]
