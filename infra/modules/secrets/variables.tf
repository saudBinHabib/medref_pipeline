variable "project" {
  description = "Project prefix used to name Secrets Manager resources and the IAM policy."
  type        = string
}

variable "deepseek_secret_name" {
  description = "Name of the pre-existing Secrets Manager secret holding the DeepSeek API key (created by hand via AWS CLI, not by Terraform)."
  type        = string
}

variable "db_instance_address" {
  description = "RDS instance endpoint address."
  type        = string
}

variable "db_instance_port" {
  description = "RDS instance port."
  type        = number
}

variable "db_name" {
  description = "Database name."
  type        = string
}

variable "db_username" {
  description = "Database master username."
  type        = string
}

variable "db_password" {
  description = "Database master password (from the rds module's random_password)."
  type        = string
  sensitive   = true
}
