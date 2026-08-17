variable "project" {
  description = "Project prefix used to name pipeline resources."
  type        = string
}

variable "cluster_arn" {
  description = "ARN of the shared ECS cluster (created by ecs_api)."
  type        = string
}

variable "public_subnet_ids" {
  description = "Public subnet IDs for the pipeline/migrate tasks (assign_public_ip = true, no NAT)."
  type        = list(string)
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

variable "ecs_execution_role_arn" {
  description = "Execution role ARN created once in ecs_api, reused here."
  type        = string
}

variable "ecs_task_role_arn" {
  description = "Task role ARN created once in ecs_api, reused here."
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

variable "raw_landing_bucket_name" {
  description = "Raw-landing S3 bucket name, used to build the default feed path."
  type        = string
}

variable "dead_letter_bucket_name" {
  description = "Dead-letter S3 bucket name (referenced for completeness / future use by the pipeline task)."
  type        = string
}

variable "raw_landing_bucket_arn" {
  description = "Raw-landing S3 bucket ARN, used to scope the pipeline task role's s3:GetObject permission."
  type        = string
}

variable "dead_letter_bucket_arn" {
  description = "Dead-letter S3 bucket ARN, used to scope the pipeline task role's s3:PutObject permission."
  type        = string
}

variable "schedule_expression" {
  description = "EventBridge Scheduler cron expression for the pipeline run."
  type        = string
}

variable "cpu" {
  description = "Fargate task CPU units for the pipeline task."
  type        = number
  default     = 512
}

variable "memory" {
  description = "Fargate task memory (MB) for the pipeline task."
  type        = number
  default     = 1024
}

variable "log_retention_days" {
  description = "CloudWatch log group retention in days for pipeline/migrate tasks."
  type        = number
  default     = 7
}
