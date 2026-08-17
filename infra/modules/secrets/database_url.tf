# Managed by Terraform: nothing here is hardcoded, it's assembled at apply
# time from the rds module's outputs plus its random_password result.
# recovery_window_in_days = 0 so `terraform destroy` fully removes it rather
# than soft-deleting (see teardown notes in the plan doc).
resource "aws_secretsmanager_secret" "database_url" {
  name                    = "${var.project}/database-url"
  recovery_window_in_days = 0
}

resource "aws_secretsmanager_secret_version" "database_url" {
  secret_id     = aws_secretsmanager_secret.database_url.id
  secret_string = "postgresql+psycopg://${var.db_username}:${urlencode(var.db_password)}@${var.db_instance_address}:${var.db_instance_port}/${var.db_name}"
}
