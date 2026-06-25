# ── ALB SG ───────────────────────────────────────────────────────────────────
# Internal ALB: CloudFront VPC Origin → HTTP:80 만 허용
resource "aws_security_group" "alb" {
  name        = "hezo-alb-sg"
  description = "HEZO Internal ALB - CloudFront VPC Origin HTTP:80 only"
  vpc_id      = var.vpc_id

  ingress {
    description     = "CloudFront VPC Origin (managed prefix list)"
    from_port       = 80
    to_port         = 80
    protocol        = "tcp"
    prefix_list_ids = [var.cf_prefix_list_id]
  }

  egress {
    from_port   = 0
    to_port     = 0
    protocol    = "-1"
    cidr_blocks = ["0.0.0.0/0"]
  }

  tags = { Name = "hezo-alb-sg" }
}

# ── App SG (ECS 태스크) ───────────────────────────────────────────────────────
# ALB에서만 인바운드 허용 — 포트별 라우팅은 ALB Target Group이 담당
resource "aws_security_group" "app" {
  name        = "hezo-app-sg"
  description = "HEZO ECS tasks - inbound from ALB only"
  vpc_id      = var.vpc_id

  # ALB → App 인바운드 규칙은 circular dep 방지를 위해 아래 별도 resource로 분리

  egress {
    description = "Outbound via NAT GW - Toss Payments, Kakao, Naver OAuth, Serper.dev"
    from_port   = 0
    to_port     = 0
    protocol    = "-1"
    cidr_blocks = ["0.0.0.0/0"]
  }

  tags = { Name = "hezo-app-sg" }
}

# ALB → App 인바운드 (SG 간 순환 참조 방지를 위해 aws_security_group_rule 사용)
resource "aws_security_group_rule" "alb_to_app_inbound" {
  description              = "All TCP from ALB (backend:8000, studio:3000, worker:8080)"
  type                     = "ingress"
  security_group_id        = aws_security_group.app.id
  source_security_group_id = aws_security_group.alb.id
  from_port                = 0
  to_port                  = 65535
  protocol                 = "tcp"
}

# ── VPCE SG (VPC Interface Endpoints) ────────────────────────────────────────
# Interface Endpoint는 HTTPS(443)으로 통신
resource "aws_security_group" "vpce" {
  name        = "hezo-vpce-sg"
  description = "HEZO VPC Interface Endpoints - HTTPS from VPC CIDR"
  vpc_id      = var.vpc_id

  ingress {
    description = "HTTPS from exam-vpc CIDR (ECS tasks to ECR/Logs/Bedrock)"
    from_port   = 443
    to_port     = 443
    protocol    = "tcp"
    cidr_blocks = ["10.0.0.0/16"]
  }

  egress {
    from_port   = 0
    to_port     = 0
    protocol    = "-1"
    cidr_blocks = ["0.0.0.0/0"]
  }

  tags = { Name = "hezo-vpce-sg" }
}

# ── RDS SG ───────────────────────────────────────────────────────────────────
# ECS 태스크에서만 PostgreSQL:5432 접근 허용
resource "aws_security_group" "rds" {
  name        = "hezo-rds-sg"
  description = "HEZO RDS PostgreSQL - App SG port 5432 only"
  vpc_id      = var.vpc_id

  # App → RDS 인바운드 규칙도 별도 resource로 분리

  tags = { Name = "hezo-rds-sg" }
}

# App → RDS :5432 인바운드
resource "aws_security_group_rule" "app_to_rds_inbound" {
  description              = "PostgreSQL from ECS tasks"
  type                     = "ingress"
  security_group_id        = aws_security_group.rds.id
  source_security_group_id = aws_security_group.app.id
  from_port                = 5432
  to_port                  = 5432
  protocol                 = "tcp"
}
