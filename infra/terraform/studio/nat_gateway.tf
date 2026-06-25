# ── EIP (NAT GW용) ───────────────────────────────────────────────────────────
resource "aws_eip" "nat" {
  domain = "vpc"
  tags   = { Name = "hezo-nat-eip" }
}

# ── NAT Gateway (Public Subnet AZ-a 단일, MVP) ────────────────────────────────
resource "aws_nat_gateway" "main" {
  allocation_id = aws_eip.nat.id
  subnet_id     = var.public_subnet_a
  tags          = { Name = "hezo-nat-gw" }
}

# ── Route Tables ──────────────────────────────────────────────────────────────

# Private-Web RT: ALB 서브넷 — ALB도 외부 호출 불필요하나 일관성 위해 NAT 연결
resource "aws_route_table" "private_web" {
  vpc_id = var.vpc_id
  route {
    cidr_block     = "0.0.0.0/0"
    nat_gateway_id = aws_nat_gateway.main.id
  }
  tags = { Name = "hezo-private-web-rt" }
}

# Private-WAS RT: ECS 태스크 — Toss/Kakao 등 외부 API는 NAT GW 경유
resource "aws_route_table" "private_was" {
  vpc_id = var.vpc_id
  route {
    cidr_block     = "0.0.0.0/0"
    nat_gateway_id = aws_nat_gateway.main.id
  }
  tags = { Name = "hezo-private-was-rt" }
}

# Private-DB RT: RDS 서브넷 — 인터넷 아웃바운드 없음 (local only)
resource "aws_route_table" "private_db" {
  vpc_id = var.vpc_id
  tags   = { Name = "hezo-private-db-rt" }
}

# ── 서브넷-RT 연결 (6개 서브넷) ───────────────────────────────────────────────
resource "aws_route_table_association" "web_a" {
  subnet_id      = var.private_web_subnet_a
  route_table_id = aws_route_table.private_web.id
}

resource "aws_route_table_association" "web_c" {
  subnet_id      = var.private_web_subnet_c
  route_table_id = aws_route_table.private_web.id
}

resource "aws_route_table_association" "was_a" {
  subnet_id      = var.private_was_subnet_a
  route_table_id = aws_route_table.private_was.id
}

resource "aws_route_table_association" "was_c" {
  subnet_id      = var.private_was_subnet_c
  route_table_id = aws_route_table.private_was.id
}

resource "aws_route_table_association" "db_a" {
  subnet_id      = var.private_db_subnet_a
  route_table_id = aws_route_table.private_db.id
}

resource "aws_route_table_association" "db_c" {
  subnet_id      = var.private_db_subnet_c
  route_table_id = aws_route_table.private_db.id
}
