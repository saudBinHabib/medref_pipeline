module "network" {
  source = "./modules/network"

  project = var.project
}

module "ecr" {
  source = "./modules/ecr"

  project = var.project
}

module "rds" {
  source = "./modules/rds"

  project             = var.project
  isolated_subnet_ids = module.network.isolated_subnet_ids
  ecs_sg_id           = module.network.ecs_sg_id
  rds_sg_id           = module.network.rds_sg_id
  db_name             = var.db_name
  db_username         = var.db_username
}

module "s3" {
  source = "./modules/s3"

  account_id            = data.aws_caller_identity.current.account_id
  project               = var.project
  s3_endpoint_id        = module.network.s3_endpoint_id
  public_route_table_id = module.network.public_route_table_id
}

module "secrets" {
  source = "./modules/secrets"

  project              = var.project
  deepseek_secret_name = var.deepseek_secret_name
  db_instance_address  = module.rds.db_instance_address
  db_instance_port     = module.rds.db_instance_port
  db_name              = module.rds.db_name
  db_username          = module.rds.db_username
  db_password          = module.rds.db_password
}

module "ecs_api" {
  source = "./modules/ecs_api"

  project                   = var.project
  vpc_id                    = module.network.vpc_id
  public_subnet_ids         = module.network.public_subnet_ids
  alb_sg_id                 = module.network.alb_sg_id
  ecs_sg_id                 = module.network.ecs_sg_id
  ecr_repository_url        = module.ecr.repository_url
  image_tag                 = var.image_tag
  secrets_access_policy_arn = module.secrets.secrets_access_policy_arn
  database_url_secret_arn   = module.secrets.database_url_secret_arn
  deepseek_secret_arn       = module.secrets.deepseek_secret_arn
}

module "ecs_pipeline" {
  source = "./modules/ecs_pipeline"

  project                 = var.project
  cluster_arn             = module.ecs_api.cluster_arn
  public_subnet_ids       = module.network.public_subnet_ids
  ecs_sg_id               = module.network.ecs_sg_id
  ecr_repository_url      = module.ecr.repository_url
  image_tag               = var.image_tag
  ecs_execution_role_arn  = module.ecs_api.ecs_execution_role_arn
  ecs_task_role_arn       = module.ecs_api.ecs_task_role_arn
  database_url_secret_arn = module.secrets.database_url_secret_arn
  deepseek_secret_arn     = module.secrets.deepseek_secret_arn
  raw_landing_bucket_name = module.s3.raw_landing_bucket_name
  dead_letter_bucket_name = module.s3.dead_letter_bucket_name
  raw_landing_bucket_arn  = module.s3.raw_landing_bucket_arn
  dead_letter_bucket_arn  = module.s3.dead_letter_bucket_arn
  schedule_expression     = var.pipeline_schedule_expression
}

module "cicd" {
  source = "./modules/cicd"

  project                = var.project
  github_org             = var.github_org
  github_repo            = var.github_repo
  ecr_repository_arn     = module.ecr.repository_arn
  ecs_cluster_arn        = module.ecs_api.cluster_arn
  ecs_service_arn        = module.ecs_api.service_arn
  ecs_execution_role_arn = module.ecs_api.ecs_execution_role_arn
  ecs_task_role_arn      = module.ecs_api.ecs_task_role_arn
  account_id             = data.aws_caller_identity.current.account_id
  region                 = var.aws_region
}
