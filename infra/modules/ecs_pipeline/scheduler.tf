# NOTE on the biweekly-cron limitation: EventBridge Scheduler's cron syntax
# has no native "every other week" support. `2#2` means "the 2nd Monday of
# the month", which is a monthly-cadence approximation of "every other
# Monday" -- it is NOT a true biweekly schedule (it will sometimes be ~4
# weeks and sometimes ~5 weeks between runs depending on the month, never a
# strict 14-day cadence). This is the closest built-in equivalent without
# standing up a second Lambda/step-function purely to compute real biweekly
# offsets, which is out of scope for a demo. See AWS_Deployment_Plan.md.
resource "aws_scheduler_schedule" "pipeline" {
  name                = "${var.project}-pipeline-schedule"
  schedule_expression = var.schedule_expression

  flexible_time_window {
    mode = "OFF"
  }

  target {
    arn      = var.cluster_arn
    role_arn = aws_iam_role.eventbridge_scheduler.arn

    ecs_parameters {
      task_definition_arn = aws_ecs_task_definition.pipeline.arn
      launch_type         = "FARGATE"

      network_configuration {
        subnets          = var.public_subnet_ids
        security_groups  = [var.ecs_sg_id]
        assign_public_ip = true
      }
    }
  }
}
