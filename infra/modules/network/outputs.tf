output "vpc_id" {
  value = aws_vpc.this.id
}

output "public_subnet_ids" {
  value = [for s in aws_subnet.public : s.id]
}

output "isolated_subnet_ids" {
  value = [for s in aws_subnet.isolated : s.id]
}

output "alb_sg_id" {
  value = aws_security_group.alb.id
}

output "ecs_sg_id" {
  value = aws_security_group.ecs.id
}

output "rds_sg_id" {
  value = aws_security_group.rds.id
}

output "s3_endpoint_id" {
  value = aws_vpc_endpoint.s3.id
}

output "public_route_table_id" {
  value = aws_route_table.public.id
}
