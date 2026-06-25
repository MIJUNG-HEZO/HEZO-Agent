resource "aws_db_subnet_group" "main" {
  name        = "hezo-rds-subnet-group"
  description = "HEZO RDS PostgreSQL - private-db subnets (AZ-a + AZ-c)"
  subnet_ids  = [var.private_db_subnet_a, var.private_db_subnet_c]
  tags        = { Name = "hezo-rds-subnet-group" }
}

resource "aws_db_instance" "main" {
  identifier = "hezo-postgres"

  engine         = "postgres"
  engine_version = "15"
  instance_class = "db.t3.micro"

  allocated_storage = 20
  storage_type      = "gp2"

  db_name  = var.db_name
  username = var.db_username
  password = var.db_password

  db_subnet_group_name   = aws_db_subnet_group.main.name
  vpc_security_group_ids = [aws_security_group.rds.id]

  multi_az            = false
  publicly_accessible = false
  deletion_protection = false

  # 최초 생성 후 DATABASE_URL SSM 파라미터 업데이트 + ECS force-new-deployment로 전환
  skip_final_snapshot       = false
  final_snapshot_identifier = "hezo-postgres-final-snapshot"

  backup_retention_period = 7
  backup_window           = "19:00-20:00"   # UTC = KST 04:00-05:00
  maintenance_window      = "sun:20:00-sun:21:00"  # UTC = KST 일 05:00-06:00

  tags = { Name = "hezo-postgres" }
}
