variable "project" {
  description = "Project prefix used to name RDS resources."
  type        = string
}

variable "isolated_subnet_ids" {
  description = "Isolated subnet IDs (no route to anything) for the DB subnet group."
  type        = list(string)
}

variable "ecs_sg_id" {
  description = "Security group ID of the ECS tasks, allowed to reach RDS on 5432."
  type        = string
}

variable "rds_sg_id" {
  description = "Security group ID created by the network module for RDS; this module adds its ingress rule."
  type        = string
}

variable "db_name" {
  description = "Name of the initial database created on the RDS instance."
  type        = string
}

variable "db_username" {
  description = "Master username for the RDS instance."
  type        = string
}

variable "instance_class" {
  description = "RDS instance class."
  type        = string
  default     = "db.t4g.micro"
}

variable "allocated_storage" {
  description = "Allocated storage in GB (gp3)."
  type        = number
  default     = 20
}
