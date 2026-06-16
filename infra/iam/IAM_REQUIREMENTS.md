# HEZO IAM 관리자 요청서

> 작성일: 2026-06-16  
> 대상: IAM 관리자  
> AWS 계정: `${AWS_ACCOUNT_ID}`  
> 배포 유저: `hezo-dev-donggyun`  
> AWS CLI로 실측 확인 후 작성

---

## 현재 상태 (AWS CLI 직접 확인)

```bash
aws iam get-role --role-name hezo-lambda-execution-role  → ✅ 존재 (2026-06-16 생성)
aws iam get-role --role-name hezo-step-functions-role    → ✅ 존재 (2026-06-16 생성)
aws iam get-role --role-name hezo-bedrock-agent-role     → ✅ 존재 (2026-06-16 생성)
```

---

## 지금 당장 처리 필요한 항목 (P4 배포 블로커)

### 1. Trust Policy 수정 — `${AWS_ACCOUNT_ID}` 리터럴 버그

**문제:** 역할 생성 시 파일을 그대로 적용해서 `${AWS_ACCOUNT_ID}` 문자열이  
실제 계정 ID 대신 그대로 AWS에 저장됨. 이 상태로는 Bedrock·Step Functions이  
역할을 assume하지 못해 에이전트 실행 시 무조건 권한 오류 발생.

**CLI로 직접 확인한 현재 상태:**
```json
"aws:SourceAccount": "${AWS_ACCOUNT_ID}"   ← 리터럴 문자열로 저장되어 있음
```

**아래 명령어 2개를 실행해주세요:**

```bash
# hezo-bedrock-agent-role trust policy 수정
aws iam update-assume-role-policy \
  --role-name hezo-bedrock-agent-role \
  --policy-document '{
    "Version": "2012-10-17",
    "Statement": [{
      "Sid": "BedrockAgentAssumeRole",
      "Effect": "Allow",
      "Principal": { "Service": "bedrock.amazonaws.com" },
      "Action": "sts:AssumeRole",
      "Condition": {
        "StringEquals": { "aws:SourceAccount": "${AWS_ACCOUNT_ID}" },
        "ArnLike": { "aws:SourceArn": "arn:aws:bedrock:ap-northeast-2:${AWS_ACCOUNT_ID}:agent/*" }
      }
    }]
  }'
```

```bash
# hezo-step-functions-role trust policy 수정
aws iam update-assume-role-policy \
  --role-name hezo-step-functions-role \
  --policy-document '{
    "Version": "2012-10-17",
    "Statement": [{
      "Sid": "StepFunctionsAssumeRole",
      "Effect": "Allow",
      "Principal": { "Service": "states.amazonaws.com" },
      "Action": "sts:AssumeRole",
      "Condition": {
        "StringEquals": { "aws:SourceAccount": "${AWS_ACCOUNT_ID}" },
        "ArnLike": { "aws:SourceArn": "arn:aws:states:ap-northeast-2:${AWS_ACCOUNT_ID}:stateMachine:hezo-*" }
      }
    }]
  }'
```

---

### 2. 인라인 정책 적용 확인

3개 역할에 인라인 정책이 실제로 붙어 있는지 확인이 필요합니다.  
`hezo-dev-donggyun`은 `iam:ListRolePolicies` 권한이 없어 직접 확인 불가.

**아래 명령어로 각 역할의 인라인 정책 존재 여부 확인해주세요:**

```bash
aws iam list-role-policies --role-name hezo-lambda-execution-role
aws iam list-role-policies --role-name hezo-step-functions-role
aws iam list-role-policies --role-name hezo-bedrock-agent-role
```

**정책이 없으면 아래 명령어로 적용:**

```bash
# hezo-lambda-execution-role 인라인 정책 적용
aws iam put-role-policy \
  --role-name hezo-lambda-execution-role \
  --policy-name hezo-lambda-inline-policy \
  --policy-document file://infra/iam/lambda-policy.json

# AWSLambdaBasicExecutionRole 관리형 정책 연결
aws iam attach-role-policy \
  --role-name hezo-lambda-execution-role \
  --policy-arn arn:aws:iam::aws:policy/service-role/AWSLambdaBasicExecutionRole
```

```bash
# hezo-step-functions-role 인라인 정책 적용
aws iam put-role-policy \
  --role-name hezo-step-functions-role \
  --policy-name hezo-step-functions-inline-policy \
  --policy-document file://infra/iam/step-functions-policy.json
```

```bash
# hezo-bedrock-agent-role 인라인 정책 적용
# ${AWS_ACCOUNT_ID} → ${AWS_ACCOUNT_ID} 치환 후 적용
sed 's/${AWS_ACCOUNT_ID}/${AWS_ACCOUNT_ID}/g' infra/iam/bedrock-agent-policy.json > /tmp/bedrock-policy.json

aws iam put-role-policy \
  --role-name hezo-bedrock-agent-role \
  --policy-name hezo-bedrock-agent-inline-policy \
  --policy-document file:///tmp/bedrock-policy.json
```

---

### 3. `hezo-dev-donggyun` 배포 권한 확인

배포 스크립트를 실행하는 유저입니다. Lambda, Bedrock Agent, Step Functions 생성 권한이  
있는지 확인이 필요합니다.

**아래 명령어로 현재 부여된 정책 확인해주세요:**

```bash
aws iam list-attached-user-policies --user-name hezo-dev-donggyun
aws iam list-user-policies --user-name hezo-dev-donggyun
aws iam list-groups-for-user --user-name hezo-dev-donggyun
```

**P4 배포(Lambda + Bedrock Agent + Step Functions)에 필요한 최소 권한:**  
없으면 아래 정책을 `hezo-dev-donggyun`에 적용해주세요.

```json
{
  "Version": "2012-10-17",
  "Statement": [
    {
      "Sid": "LambdaDeploy",
      "Effect": "Allow",
      "Action": [
        "lambda:CreateFunction",
        "lambda:UpdateFunctionCode",
        "lambda:UpdateFunctionConfiguration",
        "lambda:GetFunction",
        "lambda:AddPermission",
        "lambda:RemovePermission",
        "lambda:GetPolicy",
        "lambda:PublishVersion",
        "lambda:WaitFunctionUpdated"
      ],
      "Resource": "arn:aws:lambda:ap-northeast-2:${AWS_ACCOUNT_ID}:function:hezo-p4-*"
    },
    {
      "Sid": "BedrockAgentDeploy",
      "Effect": "Allow",
      "Action": [
        "bedrock:CreateAgent",
        "bedrock:UpdateAgent",
        "bedrock:GetAgent",
        "bedrock:ListAgents",
        "bedrock:PrepareAgent",
        "bedrock:CreateAgentAlias",
        "bedrock:UpdateAgentAlias",
        "bedrock:GetAgentAlias",
        "bedrock:ListAgentAliases",
        "bedrock:CreateAgentActionGroup",
        "bedrock:UpdateAgentActionGroup",
        "bedrock:GetAgentActionGroup",
        "bedrock:ListAgentActionGroups",
        "bedrock:GetFoundationModel",
        "bedrock:ListFoundationModels"
      ],
      "Resource": "*"
    },
    {
      "Sid": "StepFunctionsDeploy",
      "Effect": "Allow",
      "Action": [
        "states:CreateStateMachine",
        "states:UpdateStateMachine",
        "states:DescribeStateMachine",
        "states:ListStateMachines",
        "states:TagResource"
      ],
      "Resource": "arn:aws:states:ap-northeast-2:${AWS_ACCOUNT_ID}:stateMachine:hezo-*"
    },
    {
      "Sid": "IAMPassRoleForP4",
      "Effect": "Allow",
      "Action": "iam:PassRole",
      "Resource": [
        "arn:aws:iam::${AWS_ACCOUNT_ID}:role/hezo-lambda-execution-role",
        "arn:aws:iam::${AWS_ACCOUNT_ID}:role/hezo-bedrock-agent-role",
        "arn:aws:iam::${AWS_ACCOUNT_ID}:role/hezo-step-functions-role"
      ],
      "Condition": {
        "StringLike": {
          "iam:PassedToService": [
            "lambda.amazonaws.com",
            "bedrock.amazonaws.com",
            "states.amazonaws.com"
          ]
        }
      }
    },
    {
      "Sid": "S3Setup",
      "Effect": "Allow",
      "Action": [
        "s3:CreateBucket",
        "s3:HeadBucket",
        "s3:PutBucketVersioning",
        "s3:PutBucketEncryption",
        "s3:PutPublicAccessBlock"
      ],
      "Resource": [
        "arn:aws:s3:::hezo-artifacts",
        "arn:aws:s3:::hezo-sites"
      ]
    },
    {
      "Sid": "SSMSetup",
      "Effect": "Allow",
      "Action": [
        "ssm:PutParameter",
        "ssm:GetParameter",
        "ssm:GetParameters"
      ],
      "Resource": "arn:aws:ssm:ap-northeast-2:${AWS_ACCOUNT_ID}:parameter/hezo-*"
    },
    {
      "Sid": "DynamoDBSetup",
      "Effect": "Allow",
      "Action": [
        "dynamodb:CreateTable",
        "dynamodb:DescribeTable",
        "dynamodb:TagResource"
      ],
      "Resource": "arn:aws:dynamodb:ap-northeast-2:${AWS_ACCOUNT_ID}:table/hezo_pipeline_state"
    },
    {
      "Sid": "CloudWatchLogsSetup",
      "Effect": "Allow",
      "Action": [
        "logs:CreateLogGroup",
        "logs:PutRetentionPolicy",
        "logs:DescribeLogGroups"
      ],
      "Resource": [
        "arn:aws:logs:ap-northeast-2:${AWS_ACCOUNT_ID}:log-group:/hezo/*",
        "arn:aws:logs:ap-northeast-2:${AWS_ACCOUNT_ID}:log-group:/aws/lambda/hezo-*",
        "arn:aws:logs:ap-northeast-2:${AWS_ACCOUNT_ID}:log-group:/aws/bedrock/agents/*"
      ]
    }
  ]
}
```

---

## 나중에 처리할 항목 (P3 ECS — 아직 개발 단계 아님)

P3 빌드 워커(ECS Fargate) 개발 시작 전에 추가로 요청드릴 예정입니다.

- `hezo-ecs-execution-role` 생성 (ECR Pull + CloudWatch Logs)
- `hezo-ecs-task-role` 생성 (S3 read/write)
- `hezo-dev-donggyun`에 ECR Push + ECS 태스크 정의 권한 추가

---

## 처리 순서 요약 (체크리스트)

- [ ] **1번 — trust policy 수정** (hezo-bedrock-agent-role, hezo-step-functions-role)
- [ ] **2번 — 인라인 정책 확인 후 없으면 적용** (3개 역할 모두)
- [ ] **3번 — hezo-dev-donggyun 현재 권한 확인 후 부족하면 위 정책 적용**

1번이 가장 급합니다. 현재 trust policy가 깨진 상태라 Bedrock Agent가 아예 실행되지 않습니다.
