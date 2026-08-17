variable "aws_region" {
  description = "AWS region for the Terraform state backend resources."
  type        = string
  default     = "us-east-1"
}

variable "project" {
  description = "Project prefix used to name backend resources."
  type        = string
  default     = "medref"
}
