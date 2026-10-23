output "alb_dns_name" {
  description = "Application Load Balancer DNS hostname"
  value       = aws_lb.main.dns_name
}

output "ecr_repository_url" {
  description = "ECR Repository URL for container image push"
  value       = aws_ecr_repository.hub.repository_url
}

output "ecs_cluster_name" {
  description = "ECS cluster identifier"
  value       = aws_ecs_cluster.main.name
}

output "ecs_service_name" {
  description = "ECS service identifier"
  value       = aws_ecs_service.hub.name
}

output "dynamodb_table_name" {
  description = "DynamoDB table name for unified user states"
  value       = aws_dynamodb_table.continuity_state.name
}
