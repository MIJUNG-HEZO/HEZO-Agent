# 🚀 HEZO 에이전트 배포 가이드

## 빠른 시작

### P1 (Chat Agent) 배포

```bash
cd /c/Users/김동균/Desktop/HEZO/HEZO-Agent
bash agents/chat/deploy.sh latest
```

✨ **자동으로 처리되는 것:**
- ✅ Docker 이미지 빌드 (arm64)
- ✅ ECR 푸시
- ✅ AgentCore Runtime 업데이트
- ✅ **IAM 정책 자동 업데이트** (bedrock-agentcore:InvokeAgentRuntime)
- ✅ SSM Parameter 업데이트 (P1_AGENTCORE_RUNTIME_ARN)
- ✅ ECS 서비스 재배포

---

## 🔧 IAM 정책 문제 해결

### 문제: AccessDeniedException: InvokeAgentRuntime 권한 없음

**원인:** 새 AgentCore Runtime을 만들었는데 IAM 정책이 업데이트되지 않음

**해결책:**

#### 방법 1: deploy.sh 사용 (자동)
```bash
cd /c/Users/김동균/Desktop/HEZO/HEZO-Agent
bash agents/chat/deploy.sh latest
# 내부적으로 IAM 정책도 자동 업데이트됨
```

#### 방법 2: 수동으로 IAM만 업데이트
```bash
cd /c/Users/김동균/Desktop/HEZO/HEZO-Agent
bash update-agent-iam-policy.sh
```

---

## 📋 배포 체크리스트

### 새 에이전트 런타임을 배포할 때:

- [ ] 로컬에서 코드 수정 완료
- [ ] `git commit` & `git push`
- [ ] 배포 스크립트 실행 (`bash agents/{agent}/deploy.sh`)
- [ ] CloudWatch 로그 확인 (에러 없음)
- [ ] 프론트엔드에서 기능 테스트

### 만약 "AccessDeniedException" 에러가 나면:

- [ ] `bash update-agent-iam-policy.sh` 실행
- [ ] ECS 서비스 재배포
- [ ] 다시 테스트

---

## 🔐 IAM 정책 구조

배포 스크립트가 자동으로 다음을 설정합니다:

```json
{
  "Effect": "Allow",
  "Action": [
    "bedrock-agentcore:InvokeAgentRuntime"
  ],
  "Resource": "arn:aws:bedrock-agentcore:*:*:runtime/*"
}
```

**Wildcard (`*`)** 사용 → 모든 AgentCore Runtime에 권한 자동 부여 ✨

---

## 📚 상세 배포 단계

### Step 1: 로컬 코드 수정
```bash
# HEZO-Agent의 agents/chat/ 또는 agents/generation/ 등 수정
vim agents/chat/chat_http_handler.py
```

### Step 2: Git 커밋
```bash
git add .
git commit -m "feat(chat): 새로운 기능"
git push origin main
```

### Step 3: 배포 실행
```bash
bash agents/chat/deploy.sh latest
```

### Step 4: 로그 확인
AWS CloudWatch → `/ecs/hezo-backend` (또는 해당 서비스) → 최신 로그 확인

### Step 5: 테스트
- 프론트엔드에서 해당 기능 테스트
- 에러 없으면 배포 완료 ✅

---

## 🛠️ 고급: 특정 태그로 배포

```bash
# v1.0 태그로 배포
bash agents/chat/deploy.sh v1.0

# 결과:
# - ECR: hezo-chat-agent:v1.0
# - AgentCore: hezo_chat_agent_dev (최신 버전)
```

---

## ⚠️ 트러블슈팅

### 1️⃣ "bedrock-agentcore:InvokeAgentRuntime" 권한 없음
```bash
bash update-agent-iam-policy.sh
```

### 2️⃣ Docker 빌드 실패
```bash
# arm64 빌드 불가 (로컬 Windows의 경우)
# → 대신 deploy.sh가 자동으로 처리
# → 또는 AWS CodeBuild 사용
```

### 3️⃣ ECS 업데이트 안 됨
```bash
aws ecs update-service \
  --cluster hezo-cluster \
  --service hezo-backend-svc \
  --force-new-deployment \
  --region ap-northeast-2
```

---

## 📝 파일 위치

| 파일 | 위치 | 용도 |
|---|---|---|
| **P1 배포** | `agents/chat/deploy.sh` | P1 에이전트 배포 (IAM 자동 포함) |
| **P4 배포** | `agents/generation/deploy.sh` | P4 생성·검증·리포트 배포 |
| **IAM 업데이트** | `update-agent-iam-policy.sh` | IAM 정책 수동 업데이트 |

---

## 🎯 앞으로의 프로세스

1. 에이전트 코드 수정
2. `bash agents/{agent}/deploy.sh latest` 실행
3. **IAM 정책은 자동으로 처리됨** ✨
4. 테스트 완료!

**더 이상 수동으로 IAM 정책을 업데이트할 필요 없습니다!**
