output "alb_dns_name" {
  description = "Public DNS name of the ALB fronting the API. curl http://<this>/health to validate Phase 2."
  value       = module.ecs_api.alb_dns_name
}

output "ecr_repository_url" {
  description = "ECR repository URL for the shared medref-app image."
  value       = module.ecr.repository_url
}

output "deploy_role_arn" {
  description = "IAM role ARN for GitHub Actions OIDC deploys. Set as the AWS_DEPLOY_ROLE_ARN repo variable."
  value       = module.cicd.deploy_role_arn
}

output "database_url_secret_arn" {
  description = "Secrets Manager ARN for the assembled DATABASE_URL."
  value       = module.secrets.database_url_secret_arn
}

output "raw_landing_bucket_name" {
  description = "Raw-landing S3 bucket name."
  value       = module.s3.raw_landing_bucket_name
}

output "dead_letter_bucket_name" {
  description = "Dead-letter S3 bucket name."
  value       = module.s3.dead_letter_bucket_name
}

output "ecs_cluster_name" {
  description = "Shared ECS cluster name."
  value       = module.ecs_api.cluster_name
}

output "api_service_name" {
  description = "medref-api ECS service name."
  value       = module.ecs_api.service_name
}
