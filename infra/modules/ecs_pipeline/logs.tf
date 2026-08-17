resource "aws_cloudwatch_log_group" "pipeline" {
  name              = "/ecs/${var.project}-pipeline"
  retention_in_days = var.log_retention_days
}

resource "aws_cloudwatch_log_group" "migrate" {
  name              = "/ecs/${var.project}-migrate"
  retention_in_days = var.log_retention_days
}

data "aws_region" "current" {}
