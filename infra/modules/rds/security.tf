# Fills the gap left by the network module: rds_sg allows 5432 from the ECS
# tasks security group only, never from 0.0.0.0/0.
resource "aws_security_group_rule" "rds_ingress_from_ecs" {
  type                     = "ingress"
  from_port                = 5432
  to_port                  = 5432
  protocol                 = "tcp"
  security_group_id        = var.rds_sg_id
  source_security_group_id = var.ecs_sg_id
  description              = "Postgres from ECS tasks (API + pipeline) only"
}
