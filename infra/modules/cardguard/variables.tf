variable "environment" {
  description = "Environment name (dev, prod)"
  type        = string
}

variable "aws_region" {
  description = "AWS region"
  type        = string
  default     = "us-west-2"
}

variable "vpc_cidr" {
  description = "VPC CIDR block"
  type        = string
  default     = "10.0.0.0/16"
}

variable "az_count" {
  description = "Number of availability zones"
  type        = number
  default     = 2
}

variable "db_instance_class" {
  description = "RDS instance class"
  type        = string
  default     = "db.t4g.micro"
}

variable "db_engine_version" {
  description = "PostgreSQL engine version"
  type        = string
  default     = "16.4"
}

variable "db_allocated_storage" {
  type    = number
  default = 20
}

variable "db_multi_az" {
  type    = bool
  default = false
}

variable "db_backup_retention_days" {
  description = "Automated backup retention in days (0 disables)"
  type        = number
  default     = 7
}

variable "api_task_cpu" {
  type    = number
  default = 512
}

variable "api_task_memory" {
  type    = number
  default = 1024
}

variable "api_task_count" {
  type    = number
  default = 2
}

variable "worker_task_cpu" {
  type    = number
  default = 512
}

variable "worker_task_memory" {
  type    = number
  default = 1024
}

variable "worker_task_count" {
  type    = number
  default = 1
}

variable "api_image_tag" {
  description = "Docker tag for the api image in ECR"
  type        = string
  default     = "latest"
}

variable "worker_image_tag" {
  description = "Docker tag for the worker image in ECR"
  type        = string
  default     = "latest"
}

variable "temporal_address" {
  description = "Temporal server address (Temporal Cloud host:port)"
  type        = string
  default     = "localhost:7233"
}

variable "temporal_namespace" {
  description = "Temporal namespace"
  type        = string
  default     = "default"
}

variable "api_keys" {
  description = "Comma-separated API keys for /v1 auth. Empty -> a random key is generated."
  type        = string
  sensitive   = true
  default     = ""
}

variable "alb_ingress_cidrs" {
  description = "CIDRs allowed to reach the ALB"
  type        = list(string)
  default     = ["0.0.0.0/0"]
}

variable "db_deletion_protection" {
  type    = bool
  default = true
}
