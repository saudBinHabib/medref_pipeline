variable "project" {
  description = "Project prefix used to name ECS/ALB resources."
  type        = string
}

variable "vpc_id" {
  description = "VPC ID."
  type        = string
}

variable "public_subnet_ids" {
  description = "Public subnet IDs for the ALB and the API ECS service (assign_public_ip = true, no NAT)."
  type        = list(string)
}

variable "alb_sg_id" {
  description = "Security group ID for the ALB."
  type        = string
}

variable "ecs_sg_id" {
  description = "Security group ID for the ECS tasks."
  type        = string
}

variable "ecr_repository_url" {
  description = "ECR repository URL for the shared medref-app image."
  type        = string
}

variable "image_tag" {
  description = "Git-SHA (or 'bootstrap') tag of the image to deploy. Required, no default -- :latest is never used."
  type        = string

  validation {
    condition     = length(var.image_tag) > 0
    error_message = "image_tag must not be empty."
  }
}

variable "secrets_access_policy_arn" {
  description = "IAM policy ARN scoped to the two medref secrets, attached to the execution role."
  type        = string
}

variable "database_url_secret_arn" {
  description = "Secrets Manager ARN for the assembled DATABASE_URL."
  type        = string
}

variable "deepseek_secret_arn" {
  description = "Secrets Manager ARN for the DeepSeek API key."
  type        = string
}

variable "cpu" {
  description = "Fargate task CPU units for the API service."
  type        = number
  default     = 256
}

variable "memory" {
  description = "Fargate task memory (MB) for the API service."
  type        = number
  default     = 512
}

variable "container_port" {
  description = "Container port the API listens on."
  type        = number
  default     = 8000
}

variable "log_retention_days" {
  description = "CloudWatch log group retention in days."
  type        = number
  default     = 7
}
