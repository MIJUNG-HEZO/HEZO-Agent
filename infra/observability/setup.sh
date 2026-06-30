#!/bin/bash
set -e
exec > /var/log/hezo-obs-setup.log 2>&1

# Docker 설치
dnf update -y
dnf install -y docker
systemctl start docker
systemctl enable docker
usermod -aG docker ec2-user

# Docker Compose v2 설치
mkdir -p /usr/local/lib/docker/cli-plugins
curl -SL https://github.com/docker/compose/releases/download/v2.27.0/docker-compose-linux-x86_64 \
  -o /usr/local/lib/docker/cli-plugins/docker-compose
chmod +x /usr/local/lib/docker/cli-plugins/docker-compose

# 작업 디렉터리
mkdir -p /opt/hezo-obs/grafana/provisioning/datasources
mkdir -p /opt/hezo-obs/grafana/provisioning/dashboards

echo "Setup complete. Run: cd /opt/hezo-obs && docker compose up -d"
