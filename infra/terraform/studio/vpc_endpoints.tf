locals {
  # Interface Endpoint는 private-was 서브넷 2개에 배치 (ECS 태스크와 같은 서브넷)
  interface_endpoint_subnets = [
    var.private_was_subnet_a,
    var.private_was_subnet_c,
  ]
}

# ── Gateway VPCEs (무료) ──────────────────────────────────────────────────────

resource "aws_vpc_endpoint" "s3" {
  vpc_id            = var.vpc_id
  service_name      = "com.amazonaws.ap-northeast-2.s3"
  vpc_endpoint_type = "Gateway"
  route_table_ids = [
    aws_route_table.private_was.id,
    aws_route_table.private_db.id,
  ]
  tags = { Name = "hezo-vpce-s3" }
}

resource "aws_vpc_endpoint" "dynamodb" {
  vpc_id            = var.vpc_id
  service_name      = "com.amazonaws.ap-northeast-2.dynamodb"
  vpc_endpoint_type = "Gateway"
  route_table_ids   = [aws_route_table.private_was.id]
  tags              = { Name = "hezo-vpce-dynamodb" }
}

# ── Interface VPCEs ($0.013/hr/AZ × 2 AZ = $18.98/월 각) ────────────────────

resource "aws_vpc_endpoint" "ecr_api" {
  vpc_id              = var.vpc_id
  service_name        = "com.amazonaws.ap-northeast-2.ecr.api"
  vpc_endpoint_type   = "Interface"
  subnet_ids          = local.interface_endpoint_subnets
  security_group_ids  = [aws_security_group.vpce.id]
  private_dns_enabled = true
  tags                = { Name = "hezo-vpce-ecr-api" }
}

resource "aws_vpc_endpoint" "ecr_dkr" {
  vpc_id              = var.vpc_id
  service_name        = "com.amazonaws.ap-northeast-2.ecr.dkr"
  vpc_endpoint_type   = "Interface"
  subnet_ids          = local.interface_endpoint_subnets
  security_group_ids  = [aws_security_group.vpce.id]
  private_dns_enabled = true
  tags                = { Name = "hezo-vpce-ecr-dkr" }
}

resource "aws_vpc_endpoint" "logs" {
  vpc_id              = var.vpc_id
  service_name        = "com.amazonaws.ap-northeast-2.logs"
  vpc_endpoint_type   = "Interface"
  subnet_ids          = local.interface_endpoint_subnets
  security_group_ids  = [aws_security_group.vpce.id]
  private_dns_enabled = true
  tags                = { Name = "hezo-vpce-logs" }
}

resource "aws_vpc_endpoint" "bedrock_runtime" {
  vpc_id              = var.vpc_id
  service_name        = "com.amazonaws.ap-northeast-2.bedrock-runtime"
  vpc_endpoint_type   = "Interface"
  subnet_ids          = local.interface_endpoint_subnets
  security_group_ids  = [aws_security_group.vpce.id]
  private_dns_enabled = true
  tags                = { Name = "hezo-vpce-bedrock-runtime" }
}

# AgentCore /invocations 엔드포인트 (bedrock-agent-runtime 서비스 사용)
# 서비스 미지원 리전일 경우 enable_bedrock_agentcore_endpoint = false 로 비활성화
resource "aws_vpc_endpoint" "bedrock_agent_runtime" {
  count               = var.enable_bedrock_agentcore_endpoint ? 1 : 0
  vpc_id              = var.vpc_id
  service_name        = "com.amazonaws.ap-northeast-2.bedrock-agent-runtime"
  vpc_endpoint_type   = "Interface"
  subnet_ids          = local.interface_endpoint_subnets
  security_group_ids  = [aws_security_group.vpce.id]
  private_dns_enabled = true
  tags                = { Name = "hezo-vpce-bedrock-agentcore" }
}
