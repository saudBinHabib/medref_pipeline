variable "project" {
  description = "Project prefix used to name IAM resources."
  type        = string
}

variable "github_org" {
  description = "GitHub org/user that owns the repo (used to scope the OIDC trust policy)."
  type        = string
}

variable "github_repo" {
  description = "GitHub repo name (used to scope the OIDC trust policy)."
  type        = string
}

variable "ecr_repository_arn" {
  description = "ARN of the medref-app ECR repository."
  type        = string
}

variable "ecs_cluster_arn" {
  description = "ARN of the shared ECS cluster."
  type        = string
}

variable "ecs_service_arn" {
  description = "ARN of the medref-api ECS service."
  type        = string
}

variable "ecs_execution_role_arn" {
  description = "Execution role ARN the deploy role is allowed to pass to ECS."
  type        = string
}

variable "ecs_task_role_arn" {
  description = "Task role ARN the deploy role is allowed to pass to ECS."
  type        = string
}

variable "account_id" {
  description = "AWS account ID."
  type        = string
}

variable "region" {
  description = "AWS region."
  type        = string
}
