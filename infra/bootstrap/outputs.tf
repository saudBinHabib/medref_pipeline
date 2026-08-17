output "state_bucket_name" {
  description = "Name of the S3 bucket holding the main config's Terraform state."
  value       = aws_s3_bucket.tfstate.bucket
}

output "lock_table_name" {
  description = "Name of the DynamoDB table used for Terraform state locking."
  value       = aws_dynamodb_table.tf_locks.name
}
