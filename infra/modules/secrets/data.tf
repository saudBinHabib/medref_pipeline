# Pre-created by hand via AWS CLI before Phase 1 applies (see
# AWS_Deployment_Plan.md, Secrets section). Terraform only reads it by name --
# the value never enters a .tf file, a variable default, or Terraform state
# as a managed resource.
data "aws_secretsmanager_secret" "deepseek" {
  name = var.deepseek_secret_name
}
