variable "aws_region" {
  type        = string
  description = "AWS region for infrastructure deployment"
  default     = "us-east-1"
}

variable "environment" {
  type        = string
  description = "Environment stage (production, staging, demo)"
  default     = "production"
}

variable "app_name" {
  type        = string
  description = "Application and resource naming prefix"
  default     = "amazon-continuity-hub"
}

variable "dynamodb_table_name" {
  type        = string
  description = "DynamoDB table name for unified device states"
  default     = "AmazonContinuityState"
}

variable "container_image" {
  type        = string
  description = "Initial container image URL for ECS task"
  default     = "public.ecr.aws/amazonlinux/amazonlinux:latest"
}

variable "certificate_arn" {
  type        = string
  description = "Optional ACM certificate ARN for HTTPS ALB listener. If empty, HTTP is used."
  default     = ""
}
