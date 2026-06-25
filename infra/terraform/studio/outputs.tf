output "nat_gateway_id" {
  description = "NAT Gateway ID — Task 5(ALB), Task 6(ECS) 참조용"
  value       = aws_nat_gateway.main.id
}

output "nat_gateway_public_ip" {
  description = "NAT GW 퍼블릭 IP (ECS 아웃바운드 출구 IP — Toss/Kakao IP 화이트리스트 등록용)"
  value       = aws_eip.nat.public_ip
}

output "alb_sg_id" {
  description = "Internal ALB SG ID — Task 5(ALB 생성) 시 사용"
  value       = aws_security_group.alb.id
}

output "app_sg_id" {
  description = "ECS 태스크 SG ID — Task 6(ECS 재배포) 시 사용"
  value       = aws_security_group.app.id
}

output "vpce_sg_id" {
  description = "VPC Endpoints SG ID"
  value       = aws_security_group.vpce.id
}

output "rds_sg_id" {
  description = "RDS SG ID"
  value       = aws_security_group.rds.id
}

output "rds_endpoint" {
  description = "RDS 엔드포인트 — DATABASE_URL SSM 파라미터 업데이트 시 사용"
  value       = aws_db_instance.main.endpoint
}

output "rds_port" {
  value = aws_db_instance.main.port
}

output "database_url" {
  description = "DATABASE_URL 형식 (SSM 파라미터 업데이트 시 그대로 사용)"
  value       = "postgresql+asyncpg://${var.db_username}:***@${aws_db_instance.main.endpoint}/${var.db_name}"
  sensitive   = false
}

output "private_web_rt_id" {
  description = "Private-Web Route Table ID — Task 5(ALB) 참조"
  value       = aws_route_table.private_web.id
}

output "private_was_rt_id" {
  description = "Private-WAS Route Table ID — Task 6(ECS) 참조"
  value       = aws_route_table.private_was.id
}

output "private_db_rt_id" {
  value = aws_route_table.private_db.id
}
