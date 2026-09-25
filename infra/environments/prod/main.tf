terraform {
  required_version = ">= 1.5"

  required_providers {
    aws = {
      source  = "hashicorp/aws"
      version = "~> 5.0"
    }
    random = {
      source  = "hashicorp/random"
      version = "~> 3.6"
    }
  }

  backend "s3" {}
}

provider "aws" {
  region = var.aws_region
}

variable "aws_region" {
  type    = string
  default = "us-east-1"
}

module "cardguard" {
  source = "../../modules/cardguard"

  environment              = "prod"
  aws_region               = var.aws_region
  db_instance_class        = "db.t4g.small"
  db_backup_retention_days = 7
  db_multi_az              = true
  db_deletion_protection   = true
  api_task_count           = 2
  worker_task_count        = 1
  temporal_address         = var.temporal_address
  temporal_namespace       = var.temporal_namespace
  api_keys                 = var.api_keys
}

variable "temporal_address" {
  description = "Temporal Cloud host:port, e.g. cardguard.tmprl.cloud:7233"
  type        = string
  default     = "localhost:7233"
}

variable "temporal_namespace" {
  type    = string
  default = "default"
}

variable "api_keys" {
  type      = string
  sensitive = true
  default   = ""
}
