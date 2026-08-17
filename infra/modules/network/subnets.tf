# Public subnets host the ALB and the ECS tasks (API + pipeline), each with
# assign_public_ip = true. There is no NAT gateway in this design -- see
# AWS_Deployment_Plan.md for the tradeoff rationale (outbound internet to
# api.deepseek.com is required, and a private-subnet+VPC-endpoints-only
# design cannot reach it).
resource "aws_subnet" "public" {
  for_each = var.public_subnet_cidrs

  vpc_id                  = aws_vpc.this.id
  cidr_block              = each.value
  availability_zone       = each.key
  map_public_ip_on_launch = true

  tags = {
    Name = "${var.project}-public-${each.key}"
  }
}

# Isolated subnets host RDS only. No route out at all (not even NAT) --
# RDS never needs outbound internet, so isolating it is free and strictly
# more secure than putting it in a public subnet.
resource "aws_subnet" "isolated" {
  for_each = var.isolated_subnet_cidrs

  vpc_id                  = aws_vpc.this.id
  cidr_block              = each.value
  availability_zone       = each.key
  map_public_ip_on_launch = false

  tags = {
    Name = "${var.project}-isolated-${each.key}"
  }
}
