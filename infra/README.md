# Operating the AWS infrastructure

How to stand up, redeploy, pause, and tear down the medref-pipeline AWS
deployment. For *what* gets built and *why* (architecture, module layout,
cost estimate), see [`../AWS_Deployment_Plan.md`](../AWS_Deployment_Plan.md)
— this doc is the day-to-day operator's guide, not the design doc.

## Architecture

![Infrastructure diagram: client and GitHub reach an ALB and OIDC deploy role inside the AWS account; the ECS API service sits in a public subnet behind the ALB, talks to RDS in an isolated subnet, and calls out to api.deepseek.com; an EventBridge schedule runs the pipeline task against S3 raw-landing and dead-letter buckets; all ECS tasks share one ECR image and read Secrets Manager; Phase 4 CI/CD (amber) is coded but not yet applied](https://claude.ai/code/artifact/fe2f0303-1334-4a07-9ce3-b759cdd7abcd?via=auto_preview)

Every box is a real resource in `infra/modules/`; every arrow is a real
network hop or API call. Solid boundaries are applied and live (Steps 1–7
below); the amber region is generated Terraform waiting on
[Step 8](#step-8-cicd-terraform-optional) to be applied.

**Why tasks have public IPs but aren't exposed:** `ecs_sg` only accepts
`:8000` from `alb_sg` — never `0.0.0.0/0`. Public IPs exist purely so
`/v1/ask` can reach `api.deepseek.com` without paying for a NAT Gateway; the
security group, not subnet placement, is what actually keeps the tasks
closed to the internet.

## Terraform vs. AWS CLI — read this first

If you've used Terraform before, you're probably expecting `terraform init
&& terraform apply` and you're done. **That doesn't work here on the first
run**, for one real reason: the ECS task definitions point at an image tag
in ECR, and Terraform can't build or push Docker images — so on a truly
empty account there's nothing for the first `apply` to pull, and the ECS
tasks would fail to start. [Step 1–8](#deploying-from-scratch) below walks
around that with one `docker push` in the middle. **After that one-time
bootstrap, it genuinely is just `terraform apply`** — see
[Redeploying after a code change](#redeploying-after-a-code-change).

Day-to-day pause/resume (further down this doc) is a *second*, unrelated
reason you'll see AWS CLI commands: Terraform has no concept of "paused."
It declares a `desired_count` and an RDS instance to exist — it doesn't
manage on/off toggles for them. Pausing and resuming are handled directly
against the AWS API, not through Terraform.

| I want to... | Use | Where |
|---|---|---|
| Stand up the infra for the first time | `terraform apply` (per step) + 2 manual commands | [Deploying from scratch](#deploying-from-scratch) |
| Ship a code change (API/pipeline image) | `git push` (CI) — or `docker buildx build` + `aws ecs update-service` | [Redeploying](#redeploying-after-a-code-change) |
| Change infra (add a var, resize a task, etc.) | `terraform apply` | routine Terraform, not covered separately here |
| Pause to save cost overnight/weekend | `aws ecs update-service` + `aws rds stop-db-instance` | [Pausing](#pausing-keep-everything-save-cost) |
| Resume after pausing | `terraform apply` (recommended) or `aws rds start-db-instance` + `aws ecs update-service` | [Resuming](#resuming-after-pausing) |
| Tear everything down | `terraform destroy` + a couple of cleanup commands | [Destroying](#destroying-full-teardown) |

## Contents

- [Prerequisites](#prerequisites)
- [The AWS-credentials gotcha](#the-aws-credentials-gotcha)
- [One-time setup: state backend](#one-time-setup-state-backend)
- [Deploying from scratch](#deploying-from-scratch)
- [Redeploying after a code change](#redeploying-after-a-code-change)
- [Pausing (keep everything, save cost)](#pausing-keep-everything-save-cost)
- [Resuming after pausing](#resuming-after-pausing)
- [Destroying (full teardown)](#destroying-full-teardown)
- [Troubleshooting](#troubleshooting)

---

## Prerequisites

- `aws` CLI v2, authenticated (`aws sts get-caller-identity` should return your account)
- `terraform` >= 1.6
- `docker`, running (Docker Desktop or equivalent)
- Both installable via Homebrew: `brew install awscli && brew tap hashicorp/tap && brew install hashicorp/tap/terraform` (Terraform was pulled from `homebrew/core` — it needs the official tap, not a plain `brew install terraform`)

## The AWS-credentials gotcha

If your AWS CLI is authenticated via a browser-based login flow (`aws login`,
shows up as `login_session` in `~/.aws/config`), the AWS CLI can resolve
credentials itself, but **Terraform's AWS provider cannot see them** — it'll
fail with `No valid credential sources found` even though `aws sts
get-caller-identity` works fine. Bridge it before every `terraform`
invocation in a fresh shell (shell state doesn't persist between separate
commands):

```bash
eval "$(aws configure export-credentials --format env)"
```

This exports proper `AWS_ACCESS_KEY_ID`/`AWS_SECRET_ACCESS_KEY`/`AWS_SESSION_TOKEN`
for whatever the AWS CLI is currently authenticated as. Run it once per shell
session, before any `terraform plan`/`apply`/`destroy` or `aws` command below.
If you're using long-lived IAM user keys instead, you won't hit this at all.

## One-time setup: state backend

`infra/bootstrap/` is a separate, tiny Terraform root (its own local state —
never touches the S3 backend) that creates the state bucket and lock table
the main config needs. Apply it once, ever, per AWS account:

```bash
cd infra/bootstrap
eval "$(aws configure export-credentials --format env)"
terraform init
terraform apply
```

Note the outputs (`state_bucket_name`, `lock_table_name`), then wire the main
config to them:

```bash
cd ../
cp backend.hcl.example backend.hcl   # gitignored -- never commit this
# edit backend.hcl: fill in the real bucket name (medref-tfstate-<account_id>)
terraform init -backend-config=backend.hcl
```

You only redo this if the state bucket/lock table are ever destroyed (see
[Destroying](#destroying-full-teardown) — they're deliberately *not* touched
by the main config's destroy).

## Deploying from scratch

Eight steps, run in order — each depends on resources the previous one
created. Steps marked **Terraform** are `terraform apply`; steps marked
**Manual** are plain AWS CLI/Docker commands that Terraform can't do for
you. Run `eval "$(aws configure export-credentials --format env)"` first in
each new shell (see [the gotcha](#the-aws-credentials-gotcha) above).

### Step 1 — network + ECR (Terraform)

```bash
cd infra
terraform apply -var="image_tag=bootstrap" -target=module.network -target=module.ecr
```

### Step 2 — push the initial image (manual)

Nothing exists in ECR yet, and Steps 4 and 7 need *something* there before
they can start a task. Build for `linux/amd64` even on Apple Silicon —
Fargate defaults to x86_64, and an arm64 image fails at container start with
`exec format error`:

```bash
aws ecr get-login-password --region us-east-1 | docker login --username AWS --password-stdin <account_id>.dkr.ecr.us-east-1.amazonaws.com
docker buildx build --platform linux/amd64 -t <account_id>.dkr.ecr.us-east-1.amazonaws.com/medref-app:bootstrap --push .
```

### Step 3 — create the DeepSeek secret (manual)

Run this directly in your own terminal — not relayed through an AI
assistant or any chat — so the key value never passes through a transcript:

```bash
aws secretsmanager create-secret --name medref/deepseek-api-key --secret-string "sk-..." --region us-east-1
```

### Step 4 — data layer (Terraform)

RDS, S3 buckets, and the assembled `DATABASE_URL` secret:

```bash
terraform apply -var="image_tag=bootstrap" -target=module.rds -target=module.s3 -target=module.secrets
```

### Step 5 — API compute (Terraform)

```bash
terraform apply -var="image_tag=bootstrap" -target=module.ecs_api
```

Get the ALB DNS name from the output, then confirm: `curl http://<alb_dns_name>/health`.

### Step 6 — run the DB migration (manual)

This needs Step 5's cluster to exist, even though it applies Step 4's
schema — a real dependency, not optional:

```bash
aws ecs run-task --cluster medref-cluster --task-definition medref-migrate \
  --launch-type FARGATE \
  --network-configuration 'awsvpcConfiguration={subnets=[<public-subnet-id>],securityGroups=[<ecs-sg-id>],assignPublicIp=ENABLED}' \
  --region us-east-1
```

Confirm it worked: `curl http://<alb_dns_name>/v1/stats/dosage-forms` should
return `200 {}` (empty but not erroring — the tables exist).

### Step 7 — pipeline compute (Terraform)

```bash
terraform apply -var="image_tag=bootstrap" -target=module.ecs_pipeline
```

This also creates the EventBridge schedule (nominally "biweekly," but
EventBridge has no true 14-day recurrence — it's actually `cron(0 3 ? * 2#2
*)`, the 2nd Monday of each month, so real-world spacing drifts between
~4 and ~5 weeks; see `infra/modules/ecs_pipeline/scheduler.tf` for the
tradeoff). To test manually before
waiting for the schedule, upload a feed CSV to the raw-landing bucket and
override the task's `--feed` argument:

```bash
aws s3 cp my-test-feed.csv s3://medref-raw-landing-<account_id>/test.csv
aws ecs run-task --cluster medref-cluster --task-definition medref-pipeline \
  --launch-type FARGATE \
  --network-configuration 'awsvpcConfiguration={subnets=[<public-subnet-id>],securityGroups=[<ecs-sg-id>],assignPublicIp=ENABLED}' \
  --overrides '{"containerOverrides":[{"name":"pipeline","command":["python","-m","src.pipeline","--feed","s3://medref-raw-landing-<account_id>/test.csv"]}]}' \
  --region us-east-1
```

### Step 8 — CI/CD (Terraform, optional)

```bash
terraform apply -var="image_tag=bootstrap" -target=module.cicd
```

Note the `deploy_role_arn` output, then set it as the GitHub repo
**variable** `AWS_DEPLOY_ROLE_ARN` — that's the exact name
`.github/workflows/deploy.yml` reads; anything else and the workflow fails
to authenticate. Also set `AWS_REGION`, `ECR_REPOSITORY`, `ECS_CLUSTER`,
`ECS_SERVICE` (Settings → Secrets and variables → Actions → Variables) —
these are non-sensitive, so variables not secrets. `DEEPSEEK_API_KEY` never
touches GitHub at all.

Once this step's workflow (`.github/workflows/deploy.yml`) has run at least
once from a real push to `main`, every subsequent redeploy is just `git
push` — see the next section.

**That's the whole bootstrap.** Everything after this point is routine
Terraform (`terraform apply`, `terraform plan`) unless you're specifically
redeploying app code, pausing, or destroying — those three have their own
sections below because they *aren't* `terraform apply`.

## Redeploying after a code change

**Via CI (normal path, once Step 8 is set up):** push to `main`. GitHub
Actions builds (correctly, on `ubuntu-latest` = x86_64, so the arm64 gotcha
above doesn't recur), pushes a SHA-tagged image, and rolls the ECS service.

**Manually (before Step 8, or to bypass CI):**
```bash
eval "$(aws configure export-credentials --format env)"
docker buildx build --platform linux/amd64 -t <account_id>.dkr.ecr.us-east-1.amazonaws.com/medref-app:bootstrap --push .
aws ecs update-service --cluster medref-cluster --service medref-api --force-new-deployment --region us-east-1
```
If the service doesn't recover on its own within a minute or two after a
prior failed deployment, it may be in an ECS scheduler backoff. A fresh
`aws ecs run-task` with the same task definition (not through the service)
is a fast way to independently confirm whether a new image actually boots,
decoupled from the service's retry timing.

## Pausing (keep everything, save cost)

**None of this is Terraform.** `desired_count = 1` and the RDS instance are
declared in `infra/modules/ecs_api/service.tf` and `infra/modules/rds/`, and
Terraform has no "paused" state for either — so pausing means calling AWS
directly, and it's two separate actions (one of them, the ALB, can't
actually be paused, only deleted):

**Scale the API service to zero** (stops Fargate compute billing for the API):
```bash
aws ecs update-service --cluster medref-cluster --service medref-api --desired-count 0 --region us-east-1
```

**Stop the RDS instance** (pauses compute billing, storage still bills):
```bash
aws rds stop-db-instance --db-instance-identifier <rds-instance-id> --region us-east-1
```
RDS auto-restarts after 7 days if left stopped — AWS doesn't allow indefinite
stops. If you're pausing longer than a week, you'll need to re-stop it or
just destroy and rebuild from Terraform instead.

**What you can't stop, only destroy:** the ALB bills hourly regardless of
traffic (~$16-20/mo of the ~$45-55/mo total estimate in the plan doc) — there
is no "paused ALB." If cost matters more than round-trip setup time, prefer
[destroying](#destroying-full-teardown) over stopping for anything longer
than a day or two.

> **Heads up:** scaling the service to 0 via the CLI drifts it from
> Terraform's `desired_count = 1`. That's harmless while paused, but the
> *next* `terraform apply` you run (for any reason) will see the drift and
> scale it back to 1 — which doubles as a resume trigger, see below.

## Resuming after pausing

Two options, pick one:

**Recommended — let Terraform do it** (also reconciles any other drift):
```bash
cd infra
eval "$(aws configure export-credentials --format env)"
terraform apply -var="image_tag=<the tag currently running>"
```
This scales `medref-api` back to `desired_count = 1`. It does **not** start
RDS back up — Terraform doesn't manage that toggle at all, so you still need:
```bash
aws rds start-db-instance --db-instance-identifier <rds-instance-id> --region us-east-1
```

**Or — do it by hand, no Terraform involved:**
```bash
aws rds start-db-instance --db-instance-identifier <rds-instance-id> --region us-east-1
# wait for it to become available, then:
aws ecs update-service --cluster medref-cluster --service medref-api --desired-count 1 --region us-east-1
```

## Destroying (full teardown)

**S3 buckets aren't `force_destroy`d** — if the raw-landing or dead-letter
buckets have objects in them (they will, after any pipeline run),
`terraform destroy` fails on them until emptied:
```bash
aws s3 rm s3://medref-raw-landing-<account_id> --recursive
aws s3 rm s3://medref-dead-letter-<account_id> --recursive
```

Then destroy the main config:
```bash
cd infra
eval "$(aws configure export-credentials --format env)"
terraform destroy -var="image_tag=bootstrap"
```
This removes everything from Steps 1-8. ECR (`force_delete = true`) and the
`database-url` Secrets Manager entry (`recovery_window_in_days = 0`) are
configured to fully delete rather than soft-delete, so this one command is
genuinely final for those.

**What `terraform destroy` does *not* remove** — clean these up separately
if you want the account fully clean:

| Resource | Why it's separate | How to remove |
|---|---|---|
| `infra/bootstrap`'s state bucket + lock table | Own root module, own state, deliberately outside the main config's blast radius | `cd infra/bootstrap && terraform destroy` |
| `medref/deepseek-api-key` secret | Created manually via CLI, only ever *read* by Terraform (`data` source), never managed by it | `aws secretsmanager delete-secret --secret-id medref/deepseek-api-key --region us-east-1` |
| Local `bootstrap`-tagged image in your Docker cache | Not an AWS resource at all | `docker rmi <account_id>.dkr.ecr.us-east-1.amazonaws.com/medref-app:bootstrap` |

Destroy the bootstrap state backend **last**, after the main config's
destroy has succeeded (it needs the backend to run its own destroy first).

## Troubleshooting

Real issues hit during this deployment's first build-out, kept here so they
don't get rediscovered the hard way:

- **`exec /usr/local/bin/uvicorn: exec format error`** in ECS task logs — arm64/x86_64 mismatch. Rebuild with `docker buildx build --platform linux/amd64`.
- **`psql: error: connection to server on socket ".../.s.PGSQL.5432" failed`** — `DATABASE_URL` is stored in SQLAlchemy dialect form (`postgresql+psycopg://`); `psql` doesn't understand the `+psycopg` suffix and silently falls back to a local socket instead of erroring on the URL. The `medref-migrate` task definition already strips this (`sed 's/+psycopg//'`) — if you see this error elsewhere, you're passing the raw secret value to `psql` directly somewhere.
- **`ModuleNotFoundError: No module named 'httpx'`** at pipeline startup — `httpx` must be in `pyproject.toml`'s core `dependencies`, not `dev` extras; the container image only ever runs `pip install .`, never `.[dev]`.
- **`FileNotFoundError: /app/data/atc_reference.csv`** — the Dockerfile must `COPY data ./data`; it's easy to add `migrations/` and forget `data/` when wiring up a new task type.
- **ECS service stuck at `running: 0, pending: 0` with no new events for several minutes** after a failed deployment — this is the ECS scheduler's backoff after repeated task failures, not a hang. `aws ecs run-task` (bypassing the service) is the fastest way to confirm a fix independently, then `aws ecs update-service --force-new-deployment` to make the service retry immediately instead of waiting out the backoff.
- **Terraform can't see AWS credentials that `aws` CLI commands work fine with** — see [The AWS-credentials gotcha](#the-aws-credentials-gotcha) above.
