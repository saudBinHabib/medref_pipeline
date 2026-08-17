# Both roles are created exactly once, here, and reused by ecs_pipeline and
# cicd via variables -- never redeclared in those modules.

data "aws_iam_policy_document" "ecs_tasks_assume" {
  statement {
    effect  = "Allow"
    actions = ["sts:AssumeRole"]

    principals {
      type        = "Service"
      identifiers = ["ecs-tasks.amazonaws.com"]
    }
  }
}

# Execution role: pulls the image from ECR, writes logs to CloudWatch, and
# reads the two secrets to inject them into the container at startup.
resource "aws_iam_role" "ecs_execution" {
  name               = "${var.project}-ecs-execution-role"
  assume_role_policy = data.aws_iam_policy_document.ecs_tasks_assume.json
}

resource "aws_iam_role_policy_attachment" "ecs_execution_managed" {
  role       = aws_iam_role.ecs_execution.name
  policy_arn = "arn:aws:iam::aws:policy/service-role/AmazonECSTaskExecutionRolePolicy"
}

resource "aws_iam_role_policy_attachment" "ecs_execution_secrets" {
  role       = aws_iam_role.ecs_execution.name
  policy_arn = var.secrets_access_policy_arn
}

# Task role: assumed by the running application code itself. No inline
# policies attached here -- the app doesn't call AWS APIs directly beyond
# what's already covered by the S3 gateway endpoint's IAM-free routing.
resource "aws_iam_role" "ecs_task" {
  name               = "${var.project}-ecs-task-role"
  assume_role_policy = data.aws_iam_policy_document.ecs_tasks_assume.json
}
