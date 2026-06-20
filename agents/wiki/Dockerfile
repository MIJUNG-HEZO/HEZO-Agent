# P2 위키 람다 3개(crawl/generate/reinforce) 공용 컨테이너 이미지
# - AWS Lambda 전용 베이스 이미지 사용 (일반 컨테이너와 다름)
# - 핸들러(CMD)는 람다 생성 시 ImageConfig.Command로 덮어씀
#   crawl    : agents.wiki.handlers.crawl_lambda.handler
#   generate : agents.wiki.handlers.generate_lambda.handler
#   reinforce: agents.wiki.handlers.reinforce_lambda.handler
FROM public.ecr.aws/lambda/python:3.12

# 의존성 (boto3·httpx·trafilatura) — trafilatura/lxml 때문에 컨테이너로 패키징
COPY agents/wiki/requirements.txt ${LAMBDA_TASK_ROOT}/
RUN pip install --no-cache-dir -r ${LAMBDA_TASK_ROOT}/requirements.txt

# 위키 코드 (agents.wiki.* 절대 import 위해 agents 패키지 전체 복사)
COPY agents/ ${LAMBDA_TASK_ROOT}/agents/

# 관측 키트 (libs.telemetry — record_llm_usage)
COPY libs/ ${LAMBDA_TASK_ROOT}/libs/

# 기본 핸들러 (람다 생성 시 핸들러별로 덮어씀)
CMD ["agents.wiki.handlers.crawl_lambda.handler"]
