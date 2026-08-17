data "aws_iam_policy_document" "scheduler_assume" {
  statement {
    effect  = "Allow"
    actions = ["sts:AssumeRole"]

    principals {
      type        = "Service"
      identifiers = ["scheduler.amazonaws.com"]
    }
  }
}

resource "aws_iam_role" "eventbridge_scheduler" {
  name               = "${var.project}-scheduler-role"
  assume_role_policy = data.aws_iam_policy_document.scheduler_assume.json
}

# Scoped to ecs:RunTask on the pipeline task def only, plus iam:PassRole on
# exactly the two ecs_api roles (execution + task) it must pass to ECS when
# starting the task -- never a wildcard.
data "aws_iam_policy_document" "scheduler_run_task" {
  statement {
    sid       = "RunPipelineTask"
    effect    = "Allow"
    actions   = ["ecs:RunTask"]
    resources = [aws_ecs_task_definition.pipeline.arn]

    condition {
      test     = "ArnEquals"
      variable = "ecs:cluster"
      values   = [var.cluster_arn]
    }
  }

  statement {
    sid       = "PassEcsRoles"
    effect    = "Allow"
    actions   = ["iam:PassRole"]
    resources = [var.ecs_execution_role_arn, var.ecs_task_role_arn]
  }
}

resource "aws_iam_role_policy" "scheduler_run_task" {
  name   = "${var.project}-scheduler-run-task"
  role   = aws_iam_role.eventbridge_scheduler.id
  policy = data.aws_iam_policy_document.scheduler_run_task.json
}

# Pipeline task role S3 access -- scoped to exactly the two buckets it needs:
# read the feed from raw-landing, write the run's dead-letter CSV to
# dead-letter. No wildcards, no broader S3 permissions.
data "aws_iam_policy_document" "pipeline_s3_access" {
  statement {
    sid       = "ReadRawLanding"
    effect    = "Allow"
    actions   = ["s3:GetObject"]
    resources = ["${var.raw_landing_bucket_arn}/*"]
  }

  statement {
    sid       = "WriteDeadLetter"
    effect    = "Allow"
    actions   = ["s3:PutObject"]
    resources = ["${var.dead_letter_bucket_arn}/*"]
  }
}

resource "aws_iam_policy" "pipeline_s3_access" {
  name   = "${var.project}-pipeline-s3-access"
  policy = data.aws_iam_policy_document.pipeline_s3_access.json
}

resource "aws_iam_role_policy_attachment" "pipeline_s3_access" {
  # aws_iam_role_policy_attachment takes a role *name*, not an ARN -- extract
  # the trailing path segment so this still works if the role ARN ever
  # includes an IAM path (arn:aws:iam::<acct>:role/<path>/<name>).
  role       = element(split("/", var.ecs_task_role_arn), length(split("/", var.ecs_task_role_arn)) - 1)
  policy_arn = aws_iam_policy.pipeline_s3_access.arn
}
