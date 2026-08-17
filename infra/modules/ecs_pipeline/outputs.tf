output "pipeline_task_family" {
  value = aws_ecs_task_definition.pipeline.family
}

output "migrate_task_family" {
  value = aws_ecs_task_definition.migrate.family
}

output "migrate_task_definition_arn" {
  value = aws_ecs_task_definition.migrate.arn
}

output "scheduler_role_arn" {
  value = aws_iam_role.eventbridge_scheduler.arn
}

output "schedule_arn" {
  value = aws_scheduler_schedule.pipeline.arn
}
