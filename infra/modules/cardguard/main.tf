locals {
  name_prefix = "cardguard-${var.environment}"

  common_tags = {
    Environment = var.environment
    Project     = "cardguard"
    ManagedBy   = "terraform"
  }
}

# ---------------------------------------------------------------------------
# Networking: VPC with public and private subnets
# ---------------------------------------------------------------------------

data "aws_availability_zones" "available" {
  state = "available"
}

resource "aws_vpc" "this" {
  cidr_block           = var.vpc_cidr
  enable_dns_support   = true
  enable_dns_hostnames = true
  tags                 = merge(local.common_tags, { Name = local.name_prefix })
}

resource "aws_internet_gateway" "this" {
  vpc_id = aws_vpc.this.id
  tags   = merge(local.common_tags, { Name = local.name_prefix })
}

resource "aws_subnet" "public" {
  count                   = var.az_count
  vpc_id                  = aws_vpc.this.id
  cidr_block              = cidrsubnet(var.vpc_cidr, 8, count.index)
  availability_zone       = data.aws_availability_zones.available.names[count.index]
  map_public_ip_on_launch = true
  tags = merge(local.common_tags, {
    Name = "${local.name_prefix}-public-${count.index}"
  })
}

resource "aws_subnet" "private" {
  count             = var.az_count
  vpc_id            = aws_vpc.this.id
  cidr_block        = cidrsubnet(var.vpc_cidr, 8, count.index + 100)
  availability_zone = data.aws_availability_zones.available.names[count.index]
  tags = merge(local.common_tags, {
    Name = "${local.name_prefix}-private-${count.index}"
  })
}

resource "aws_eip" "nat" {
  domain = "vpc"
  tags   = merge(local.common_tags, { Name = local.name_prefix })
}

resource "aws_nat_gateway" "this" {
  allocation_id = aws_eip.nat.id
  subnet_id     = aws_subnet.public[0].id
  tags          = merge(local.common_tags, { Name = local.name_prefix })
}

resource "aws_route_table" "public" {
  vpc_id = aws_vpc.this.id
  route {
    cidr_block = "0.0.0.0/0"
    gateway_id = aws_internet_gateway.this.id
  }
  tags = merge(local.common_tags, { Name = "${local.name_prefix}-public" })
}

resource "aws_route_table_association" "public" {
  count          = var.az_count
  subnet_id      = aws_subnet.public[count.index].id
  route_table_id = aws_route_table.public.id
}

resource "aws_route_table" "private" {
  vpc_id = aws_vpc.this.id
  route {
    cidr_block     = "0.0.0.0/0"
    nat_gateway_id = aws_nat_gateway.this.id
  }
  tags = merge(local.common_tags, { Name = "${local.name_prefix}-private" })
}

resource "aws_route_table_association" "private" {
  count          = var.az_count
  subnet_id      = aws_subnet.private[count.index].id
  route_table_id = aws_route_table.private.id
}

# ---------------------------------------------------------------------------
# Secrets Manager
# ---------------------------------------------------------------------------

resource "random_password" "db_password" {
  length  = 32
  special = false
}

resource "aws_secretsmanager_secret" "db_password" {
  name = "${local.name_prefix}-db-password"
  tags = local.common_tags
}

resource "aws_secretsmanager_secret_version" "db_password" {
  secret_id     = aws_secretsmanager_secret.db_password.id
  secret_string = random_password.db_password.result
}

resource "aws_secretsmanager_secret" "database_url" {
  name = "${local.name_prefix}-database-url"
  tags = local.common_tags
}

resource "aws_secretsmanager_secret_version" "database_url" {
  secret_id = aws_secretsmanager_secret.database_url.id
  secret_string = join("", [
    "postgresql+psycopg://cardguard:",
    random_password.db_password.result,
    "@",
    aws_db_instance.this.address,
    ":5432/cardguard",
  ])
}

resource "aws_secretsmanager_secret" "api_keys" {
  name = "${local.name_prefix}-api-keys"
  tags = local.common_tags
}

resource "aws_secretsmanager_secret_version" "api_keys" {
  secret_id     = aws_secretsmanager_secret.api_keys.id
  secret_string = var.api_keys != "" ? var.api_keys : random_password.db_password.result
}

resource "aws_secretsmanager_secret" "temporal_cert" {
  name = "${local.name_prefix}-temporal-cert"
  tags = local.common_tags
}

resource "aws_secretsmanager_secret_version" "temporal_cert" {
  secret_id     = aws_secretsmanager_secret.temporal_cert.id
  secret_string = "replace-with-temporal-cloud-client-certificate-pem"
}

resource "aws_secretsmanager_secret" "temporal_key" {
  name = "${local.name_prefix}-temporal-key"
  tags = local.common_tags
}

resource "aws_secretsmanager_secret_version" "temporal_key" {
  secret_id     = aws_secretsmanager_secret.temporal_key.id
  secret_string = "replace-with-temporal-cloud-client-key-pem"
}

locals {
  app_secret_arns = [
    aws_secretsmanager_secret.database_url.arn,
    aws_secretsmanager_secret.api_keys.arn,
    aws_secretsmanager_secret.temporal_cert.arn,
    aws_secretsmanager_secret.temporal_key.arn,
  ]
}

# ---------------------------------------------------------------------------
# RDS PostgreSQL 16 (private subnets, encrypted, automated backups)
# ---------------------------------------------------------------------------

resource "aws_db_subnet_group" "this" {
  name       = local.name_prefix
  subnet_ids = aws_subnet.private[*].id
  tags       = local.common_tags
}

resource "aws_security_group" "db" {
  name   = "${local.name_prefix}-db"
  vpc_id = aws_vpc.this.id
  tags   = local.common_tags
}

resource "aws_db_instance" "this" {
  identifier = local.name_prefix

  engine         = "postgres"
  engine_version = var.db_engine_version
  instance_class = var.db_instance_class

  db_name  = "cardguard"
  username = "cardguard"
  password = random_password.db_password.result

  allocated_storage      = var.db_allocated_storage
  storage_encrypted      = true
  multi_az               = var.db_multi_az
  db_subnet_group_name   = aws_db_subnet_group.this.name
  vpc_security_group_ids = [aws_security_group.db.id]

  backup_retention_period = var.db_backup_retention_days
  skip_final_snapshot     = false
  deletion_protection     = var.db_deletion_protection

  tags = local.common_tags
}

resource "aws_security_group_rule" "db_from_ecs" {
  type                     = "ingress"
  from_port                = 5432
  to_port                  = 5432
  protocol                 = "tcp"
  security_group_id        = aws_security_group.db.id
  source_security_group_id = aws_security_group.ecs.id
}

resource "aws_security_group_rule" "db_egress" {
  type              = "egress"
  from_port         = 0
  to_port           = 0
  protocol          = "-1"
  security_group_id = aws_security_group.db.id
  cidr_blocks       = ["0.0.0.0/0"]
}

# ---------------------------------------------------------------------------
# ECR repositories
# ---------------------------------------------------------------------------

resource "aws_ecr_repository" "api" {
  name                 = "${local.name_prefix}-api"
  image_tag_mutability = "MUTABLE"
  force_delete         = var.environment != "prod"
  tags                 = local.common_tags
}

resource "aws_ecr_repository" "worker" {
  name                 = "${local.name_prefix}-worker"
  image_tag_mutability = "MUTABLE"
  force_delete         = var.environment != "prod"
  tags                 = local.common_tags
}

# ---------------------------------------------------------------------------
# CloudWatch log groups
# ---------------------------------------------------------------------------

resource "aws_cloudwatch_log_group" "api" {
  name              = "/ecs/${local.name_prefix}-api"
  retention_in_days = 30
  tags              = local.common_tags
}

resource "aws_cloudwatch_log_group" "worker" {
  name              = "/ecs/${local.name_prefix}-worker"
  retention_in_days = 30
  tags              = local.common_tags
}

# ---------------------------------------------------------------------------
# ECS cluster, task IAM, security group
# ---------------------------------------------------------------------------

resource "aws_ecs_cluster" "this" {
  name = local.name_prefix
  tags = local.common_tags
}

resource "aws_security_group" "ecs" {
  name   = "${local.name_prefix}-ecs"
  vpc_id = aws_vpc.this.id
  tags   = local.common_tags
}

resource "aws_security_group_rule" "ecs_egress" {
  type              = "egress"
  from_port         = 0
  to_port           = 0
  protocol          = "-1"
  security_group_id = aws_security_group.ecs.id
  cidr_blocks       = ["0.0.0.0/0"]
}

data "aws_iam_policy_document" "ecs_task_assume_role" {
  statement {
    actions = ["sts:AssumeRole"]
    principals {
      type        = "Service"
      identifiers = ["ecs-tasks.amazonaws.com"]
    }
  }
}

resource "aws_iam_role" "task" {
  name               = "${local.name_prefix}-task"
  assume_role_policy = data.aws_iam_policy_document.ecs_task_assume_role.json
  tags               = local.common_tags
}

resource "aws_iam_role" "execution" {
  name               = "${local.name_prefix}-execution"
  assume_role_policy = data.aws_iam_policy_document.ecs_task_assume_role.json
  tags               = local.common_tags
}

data "aws_iam_policy_document" "task_permissions" {
  statement {
    actions = [
      "logs:CreateLogStream",
      "logs:PutLogEvents",
    ]
    resources = [
      aws_cloudwatch_log_group.api.arn,
      aws_cloudwatch_log_group.worker.arn,
      "${aws_cloudwatch_log_group.api.arn}:*",
      "${aws_cloudwatch_log_group.worker.arn}:*",
    ]
  }
  statement {
    actions   = ["secretsmanager:GetSecretValue"]
    resources = local.app_secret_arns
  }
}

resource "aws_iam_policy" "task" {
  name   = "${local.name_prefix}-task"
  policy = data.aws_iam_policy_document.task_permissions.json
  tags   = local.common_tags
}

resource "aws_iam_role_policy_attachment" "task" {
  role       = aws_iam_role.task.name
  policy_arn = aws_iam_policy.task.arn
}

resource "aws_iam_role_policy_attachment" "execution_ecr" {
  role       = aws_iam_role.execution.name
  policy_arn = "arn:aws:iam::aws:policy/AmazonECSTaskExecutionRolePolicy"
}

resource "aws_iam_role_policy_attachment" "execution_secrets" {
  role       = aws_iam_role.execution.name
  policy_arn = aws_iam_policy.task.arn
}

# ---------------------------------------------------------------------------
# ALB + target group
# ---------------------------------------------------------------------------

resource "aws_security_group" "alb" {
  name   = "${local.name_prefix}-alb"
  vpc_id = aws_vpc.this.id
  tags   = local.common_tags
}

resource "aws_security_group_rule" "alb_ingress" {
  type              = "ingress"
  from_port         = 80
  to_port           = 80
  protocol          = "tcp"
  security_group_id = aws_security_group.alb.id
  cidr_blocks       = var.alb_ingress_cidrs
}

resource "aws_security_group_rule" "alb_egress" {
  type              = "egress"
  from_port         = 0
  to_port           = 0
  protocol          = "-1"
  security_group_id = aws_security_group.alb.id
  cidr_blocks       = ["0.0.0.0/0"]
}

resource "aws_lb" "api" {
  name               = local.name_prefix
  internal           = false
  load_balancer_type = "application"
  security_groups    = [aws_security_group.alb.id]
  subnets            = aws_subnet.public[*].id
  tags               = local.common_tags
}

resource "aws_lb_target_group" "api" {
  name        = local.name_prefix
  port        = 8000
  protocol    = "HTTP"
  vpc_id      = aws_vpc.this.id
  target_type = "ip"

  health_check {
    path                = "/healthz"
    interval            = 15
    timeout             = 5
    healthy_threshold   = 2
    unhealthy_threshold = 5
    matcher             = "200"
  }

  tags = local.common_tags
}

resource "aws_lb_listener" "api" {
  load_balancer_arn = aws_lb.api.arn
  port              = 80
  protocol          = "HTTP"

  default_action {
    type             = "forward"
    target_group_arn = aws_lb_target_group.api.arn
  }
}

resource "aws_security_group_rule" "ecs_from_alb" {
  type                     = "ingress"
  from_port                = 8000
  to_port                  = 8000
  protocol                 = "tcp"
  security_group_id        = aws_security_group.ecs.id
  source_security_group_id = aws_security_group.alb.id
}
