variable "account_id" {
  description = "AWS account ID, used to suffix bucket names for global uniqueness."
  type        = string
}

variable "project" {
  description = "Project prefix used to name S3 buckets."
  type        = string
}

variable "s3_endpoint_id" {
  description = "ID of the S3 gateway VPC endpoint created by the network module."
  type        = string
}

variable "public_route_table_id" {
  description = "ID of the public route table to associate the S3 gateway endpoint with."
  type        = string
}
