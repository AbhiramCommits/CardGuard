output "vpc_id" {
  value = aws_vpc.this.id
}

output "db_endpoint" {
  value     = aws_db_instance.this.address
  sensitive = true
}

output "api_ecr_repository_url" {
  value = aws_ecr_repository.api.repository_url
}

output "worker_ecr_repository_url" {
  value = aws_ecr_repository.worker.repository_url
}

output "alb_dns_name" {
  value = aws_lb.api.dns_name
}

output "database_url_secret_arn" {
  value = aws_secretsmanager_secret.database_url.arn
}
