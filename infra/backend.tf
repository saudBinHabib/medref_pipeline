# Partial backend config -- bucket/key/region/dynamodb_table are supplied
# via `-backend-config=backend.hcl` at `terraform init` time, since the
# state bucket and lock table don't exist until infra/bootstrap is applied
# separately. See infra/backend.hcl.example.
terraform {
  backend "s3" {}
}
