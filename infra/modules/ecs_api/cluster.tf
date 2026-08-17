# The one shared ECS cluster -- ecs_pipeline reuses it via the cluster_arn
# output rather than declaring its own.
resource "aws_ecs_cluster" "this" {
  name = "${var.project}-cluster"
}
