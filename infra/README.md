# cardguard infrastructure (Terraform)

Provisions the full AWS stack for cardguard, parameterized by environment.

## What this creates

| Resource | Notes |
|---|---|
| VPC | 2 AZs, public + private subnets, NAT gateway, internet gateway |
| RDS PostgreSQL 16 | private subnets, `storage_encrypted = true`, automated backups, deletion protection in prod |
| ECS Fargate cluster | two services: `api` (behind an ALB) and `worker` (no public ingress, private subnets) |
| ECR | `cardguard-<env>-api` and `cardguard-<env>-worker` repositories |
| Secrets Manager | DB password + composed DATABASE_URL, API keys, Temporal Cloud client cert/key |
| CloudWatch | log groups `/ecs/cardguard-<env>-api` and `/ecs/cardguard-<env>-worker` |
| IAM | least-privilege task role: logs PutLogEvents + GetSecretValue on the four app secrets only; execution role: ECR pull |

## Environments

```sh
cd infra/environments/dev   # or prod
terraform init -backend=false            # first time, no remote state
terraform validate
terraform plan -var-file=dev.tfvars
```

`terraform validate` runs clean. `terraform plan` requires AWS credentials
(`AWS_ACCESS_KEY_ID`/`AWS_SECRET_ACCESS_KEY`) and, for real use, a remote state
backend:

```sh
terraform init \
  -backend-config="bucket=<state-bucket>" \
  -backend-config="key=cardguard/dev.tfstate" \
  -backend-config="region=us-west-2"
```

No credentials are committed; `api_keys` defaults to a generated random value and can
be overridden with `TF_VAR_api_keys`.

## Temporal Cloud vs self-hosted

**Temporal Cloud (default in prod tfvars).** Point `temporal_address` at your Cloud
endpoint (`<namespace>.<account>.tmprl.cloud:7233`) and put the mTLS client
certificate/key into the `*-temporal-cert` / `*-temporal-key` Secrets Manager secrets
(replace the placeholder values). The api and worker containers read
`TEMPORAL_CLIENT_CERT`/`TEMPORAL_CLIENT_KEY` (cert/key file paths) and connect with
mTLS when both are present.

**Self-hosted fallback.** If you do not want Temporal Cloud, run the bundled
self-hosted stack instead (no AWS resources involved):

```sh
docker compose up -d postgres temporal temporal-ui worker api
```

`docker-compose.yml` runs `temporalio/auto-setup` (Postgres-backed) with the worker on
the `cardguard-risk` task queue. In that mode set `temporal_address = "temporal:7233"`
(ECS cannot reach your compose network, so self-hosting on AWS means running
`temporalio/auto-setup` in ECS as well — a documented but intentionally out-of-scope
variation).

## Cost notes

The main cost drivers are the NAT gateway (~$32/month per gateway, plus $0.045/GB
data processed), the RDS instance (db.t4g.micro ~ $16/month, t4g.small ~ $33/month,
doubled with multi-AZ in prod), the ALB (~$22/month), and Fargate vCPU/memory
(512 CPU / 1 GB per task ≈ $0.04/hour per task). Dev as configured is roughly
$60–80/month; prod roughly $120–160/month. RDS backups add ~$0.10/GB-month beyond
the free snapshot storage. The Temporal UI/self-hosted stack costs nothing beyond the
compute you run it on; Temporal Cloud bills separately by action count.

## Teardown

```sh
cd infra/environments/dev
terraform destroy -var-file=dev.tfvars
```

RDS has `deletion_protection = true` in prod and `skip_final_snapshot = false`: a
final snapshot is taken before the instance is deleted, so disable deletion
protection first:

```sh
cd infra/environments/prod
terraform state rm module.cardguard.aws_db_instance.this  # after disabling protection in the console
terraform destroy -var-file=prod.tfvars
```
