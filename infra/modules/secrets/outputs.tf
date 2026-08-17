output "deepseek_secret_arn" {
  value = data.aws_secretsmanager_secret.deepseek.arn
}

output "database_url_secret_arn" {
  value = aws_secretsmanager_secret.database_url.arn
}

output "secrets_access_policy_arn" {
  value = aws_iam_policy.secrets_access.arn
}
