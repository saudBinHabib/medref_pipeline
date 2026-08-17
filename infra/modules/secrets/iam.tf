# Scoped to exactly these two secret ARNs -- no wildcard secretsmanager:*.
data "aws_iam_policy_document" "secrets_access" {
  statement {
    sid    = "ReadMedrefSecrets"
    effect = "Allow"
    actions = [
      "secretsmanager:GetSecretValue",
    ]
    resources = [
      data.aws_secretsmanager_secret.deepseek.arn,
      aws_secretsmanager_secret.database_url.arn,
    ]
  }
}

resource "aws_iam_policy" "secrets_access" {
  name        = "${var.project}-secrets-access"
  description = "Least-privilege access to the medref DeepSeek API key and database URL secrets only."
  policy      = data.aws_iam_policy_document.secrets_access.json
}
