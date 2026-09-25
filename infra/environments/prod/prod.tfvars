# Production environment settings.
# Secrets (api_keys) are NOT committed; set TF_VAR_api_keys or add a terraform.tfvars
# in your local checkout (gitignored).
aws_region         = "us-east-1"
temporal_address   = "cardguard.tmprl.cloud:7233"
temporal_namespace = "cardguard.prod"
