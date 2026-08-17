variable "aws_region" {
  description = "AWS region for all resources."
  type        = string
  default     = "us-east-1"
}

variable "project" {
  description = "Project prefix used throughout resource names."
  type        = string
  default     = "medref"
}

variable "github_org" {
  description = "GitHub org/user that owns the repo (used to scope the CI/CD OIDC trust policy)."
  type        = string
  default     = "saudBinHabib"
}

variable "github_repo" {
  description = "GitHub repo name (used to scope the CI/CD OIDC trust policy)."
  type        = string
  default     = "medref-pipeline"
}

variable "image_tag" {
  description = "Git-SHA (or 'bootstrap') tag of the image to deploy to ECS. Required, no default -- :latest is never used, every plan/apply must pass this explicitly."
  type        = string
}

variable "db_name" {
  description = "Name of the initial database created on the RDS instance."
  type        = string
  default     = "medref"
}

variable "db_username" {
  description = "Master username for the RDS instance."
  type        = string
  default     = "medref"
}

variable "deepseek_secret_name" {
  description = "Name of the pre-existing Secrets Manager secret holding the DeepSeek API key (created by hand via AWS CLI before Phase 1, not by Terraform)."
  type        = string
  default     = "medref/deepseek-api-key"
}

variable "pipeline_schedule_expression" {
  description = <<-EOT
    EventBridge Scheduler cron expression for the pipeline run. "Every other
    Monday 03:00 UTC" has no exact native cron equivalent; cron(0 3 ? * 2#2 *)
    (2nd Monday of the month) is used as the closest built-in approximation --
    see infra/modules/ecs_pipeline/scheduler.tf for the tradeoff writeup.
  EOT
  type        = string
  default     = "cron(0 3 ? * 2#2 *)"
}
