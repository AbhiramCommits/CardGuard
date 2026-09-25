# Development environment settings.
# Secrets (api_keys) are NOT committed; set TF_VAR_api_keys or add a terraform.tfvars
# in your local checkout (gitignored).
aws_region         = "us-west-2"
temporal_address   = "localhost:7233"
temporal_namespace = "default"
