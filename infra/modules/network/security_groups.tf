# alb_sg: internet-facing, HTTP only (no HTTPS/custom domain in this demo).
resource "aws_security_group" "alb" {
  name        = "${var.project}-alb-sg"
  description = "ALB security group: allows inbound HTTP from the internet."
  vpc_id      = aws_vpc.this.id

  ingress {
    description = "HTTP from internet"
    from_port   = 80
    to_port     = 80
    protocol    = "tcp"
    cidr_blocks = ["0.0.0.0/0"]
  }

  egress {
    description = "All outbound"
    from_port   = 0
    to_port     = 0
    protocol    = "-1"
    cidr_blocks = ["0.0.0.0/0"]
  }

  tags = {
    Name = "${var.project}-alb-sg"
  }
}

# ecs_sg: this is the actual security boundary in the public-subnet, no-NAT
# design. Tasks have public IPs, but only the ALB can reach them on 8000 --
# never 0.0.0.0/0.
resource "aws_security_group" "ecs" {
  name        = "${var.project}-ecs-sg"
  description = "ECS tasks security group: allows inbound app traffic from the ALB only."
  vpc_id      = aws_vpc.this.id

  ingress {
    description     = "App traffic from ALB only"
    from_port       = 8000
    to_port         = 8000
    protocol        = "tcp"
    security_groups = [aws_security_group.alb.id]
  }

  egress {
    description = "All outbound (needed for api.deepseek.com, ECR, etc.)"
    from_port   = 0
    to_port     = 0
    protocol    = "-1"
    cidr_blocks = ["0.0.0.0/0"]
  }

  tags = {
    Name = "${var.project}-ecs-sg"
  }
}

# rds_sg: egress-only shell for now. The ingress rule (5432 from ecs_sg) is
# added by the rds module to avoid a forward reference to a resource that
# doesn't exist yet in this module.
resource "aws_security_group" "rds" {
  name        = "${var.project}-rds-sg"
  description = "RDS security group: ingress added by the rds module."
  vpc_id      = aws_vpc.this.id

  egress {
    description = "All outbound"
    from_port   = 0
    to_port     = 0
    protocol    = "-1"
    cidr_blocks = ["0.0.0.0/0"]
  }

  tags = {
    Name = "${var.project}-rds-sg"
  }
}
