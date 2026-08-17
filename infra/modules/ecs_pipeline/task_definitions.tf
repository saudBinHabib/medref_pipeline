# Same image as the API (selected via CMD override, no separate ECR repo),
# same execution/task roles created once in ecs_api and passed in here.

resource "aws_ecs_task_definition" "pipeline" {
  family                   = "${var.project}-pipeline"
  requires_compatibilities = ["FARGATE"]
  network_mode             = "awsvpc"
  cpu                      = var.cpu
  memory                   = var.memory
  execution_role_arn       = var.ecs_execution_role_arn
  task_role_arn            = var.ecs_task_role_arn

  container_definitions = jsonencode([
    {
      name      = "pipeline"
      image     = "${var.ecr_repository_url}:${var.image_tag}"
      essential = true
      command   = ["python", "-m", "src.pipeline", "--feed", "s3://${var.raw_landing_bucket_name}/latest.csv"]

      environment = [
        {
          name  = "DEAD_LETTER_BUCKET"
          value = var.dead_letter_bucket_name
        }
      ]

      secrets = [
        {
          name      = "DATABASE_URL"
          valueFrom = var.database_url_secret_arn
        },
        {
          name      = "DEEPSEEK_API_KEY"
          valueFrom = var.deepseek_secret_arn
        }
      ]

      logConfiguration = {
        logDriver = "awslogs"
        options = {
          "awslogs-group"         = aws_cloudwatch_log_group.pipeline.name
          "awslogs-region"        = data.aws_region.current.name
          "awslogs-stream-prefix" = "pipeline"
        }
      }
    }
  ])
}

# One-off migration task, run once manually via `aws ecs run-task` after
# Phase 2, and again whenever the schema changes. Uses `sh -c` so
# $DATABASE_URL (injected as a secret env var by ECS) actually expands --
# ECS's `command` list does not itself perform shell interpolation.
#
# DATABASE_URL is stored in SQLAlchemy dialect form (`postgresql+psycopg://`)
# for the app's benefit -- psql doesn't recognize the `+psycopg` suffix and
# silently falls back to a local socket connection instead of erroring, so
# it's stripped here before psql ever sees the URL.
resource "aws_ecs_task_definition" "migrate" {
  family                   = "${var.project}-migrate"
  requires_compatibilities = ["FARGATE"]
  network_mode             = "awsvpc"
  cpu                      = var.cpu
  memory                   = var.memory
  execution_role_arn       = var.ecs_execution_role_arn
  task_role_arn            = var.ecs_task_role_arn

  container_definitions = jsonencode([
    {
      name      = "migrate"
      image     = "${var.ecr_repository_url}:${var.image_tag}"
      essential = true
      command   = ["sh", "-c", "psql \"$(echo \"$DATABASE_URL\" | sed 's/+psycopg//')\" -f migrations/001_init.sql"]

      secrets = [
        {
          name      = "DATABASE_URL"
          valueFrom = var.database_url_secret_arn
        }
      ]

      logConfiguration = {
        logDriver = "awslogs"
        options = {
          "awslogs-group"         = aws_cloudwatch_log_group.migrate.name
          "awslogs-region"        = data.aws_region.current.name
          "awslogs-stream-prefix" = "migrate"
        }
      }
    }
  ])
}
