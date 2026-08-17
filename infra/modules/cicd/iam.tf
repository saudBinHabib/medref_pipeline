# Trust policy scoped to push-to-main only, not every branch/PR.
data "aws_iam_policy_document" "deploy_assume" {
  statement {
    effect  = "Allow"
    actions = ["sts:AssumeRoleWithWebIdentity"]

    principals {
      type        = "Federated"
      identifiers = [aws_iam_openid_connect_provider.github_actions.arn]
    }

    condition {
      test     = "StringEquals"
      variable = "token.actions.githubusercontent.com:aud"
      values   = ["sts.amazonaws.com"]
    }

    condition {
      test     = "StringEquals"
      variable = "token.actions.githubusercontent.com:sub"
      values   = ["repo:${var.github_org}/${var.github_repo}:ref:refs/heads/main"]
    }
  }
}

resource "aws_iam_role" "deploy" {
  name               = "${var.project}-deploy-role"
  assume_role_policy = data.aws_iam_policy_document.deploy_assume.json
}

# Least-privilege deploy permissions -- never AdministratorAccess, never a
# wildcard resource except where the AWS API itself requires it
# (ecr:GetAuthorizationToken has no resource-level scoping).
data "aws_iam_policy_document" "deploy" {
  statement {
    sid       = "EcrAuth"
    effect    = "Allow"
    actions   = ["ecr:GetAuthorizationToken"]
    resources = ["*"]
  }

  statement {
    sid    = "EcrPush"
    effect = "Allow"
    actions = [
      "ecr:BatchCheckLayerAvailability",
      "ecr:PutImage",
      "ecr:InitiateLayerUpload",
      "ecr:UploadLayerPart",
      "ecr:CompleteLayerUpload",
    ]
    resources = [var.ecr_repository_arn]
  }

  statement {
    sid    = "EcsDeploy"
    effect = "Allow"
    actions = [
      "ecs:DescribeServices",
      "ecs:UpdateService",
    ]
    resources = [var.ecs_cluster_arn, var.ecs_service_arn]
  }

  statement {
    sid    = "EcsTaskDef"
    effect = "Allow"
    actions = [
      "ecs:RegisterTaskDefinition",
      "ecs:DescribeTaskDefinition",
    ]
    # RegisterTaskDefinition/DescribeTaskDefinition do not support
    # resource-level scoping in IAM -- AWS requires "*" for these actions.
    resources = ["*"]
  }

  statement {
    sid       = "PassEcsRoles"
    effect    = "Allow"
    actions   = ["iam:PassRole"]
    resources = [var.ecs_execution_role_arn, var.ecs_task_role_arn]
  }
}

resource "aws_iam_policy" "deploy" {
  name        = "${var.project}-deploy-policy"
  description = "Least-privilege CI/CD deploy permissions for GitHub Actions OIDC role."
  policy      = data.aws_iam_policy_document.deploy.json
}

resource "aws_iam_role_policy_attachment" "deploy" {
  role       = aws_iam_role.deploy.name
  policy_arn = aws_iam_policy.deploy.arn
}
