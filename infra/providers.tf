provider "aws" {
  region = var.aws_region

  default_tags {
    tags = {
      Project   = "medref"
      ManagedBy = "terraform"
    }
  }
}
