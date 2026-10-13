# Deploying to AWS

Everything is defined in [`template.yaml`](template.yaml) (AWS SAM). Region: `ap-south-1` (Mumbai) is the closest to Nepal.

## Prerequisites

- An AWS account and credentials (`aws configure`)
- [AWS SAM CLI](https://docs.aws.amazon.com/serverless-application-model/latest/developerguide/install-sam-cli.html) and Docker
- A trained model locally: `make train`

## Deploy

```bash
sam build --template-file infra/template.yaml
sam deploy --guided            # stack name e.g. hawa; set AlertEmail and OpsEmail if you want emails
```

`sam deploy --guided` creates an ECR repository for the image and remembers your answers in `samconfig.toml`.

## Seed

The stack starts empty. This uploads the replay feed, backfills the raw archive up to the replay "now", and uploads your local model as the `bootstrap` version the SSM parameter points at:

```bash
python scripts/seed_aws.py --bucket <DataBucketName from the stack outputs>
```

From then on the schedules run by themselves: ingest at :05, forecast at :15 every hour, retrain every Saturday 02:00 Nepal time. Check it with:

```bash
curl https://<ApiUrl>/health
curl https://<ApiUrl>/forecast/ratnapark
```

## Replay mode

`ReplayOffsetDays` (default 3421) shifts "now" back into the 2016-17 data so the system has something to forecast. Today minus 3,421 days lands in mid-2017; the value drifts a day per day, so pick one that lands between 8 Nov 2016 and 29 Dec 2017. Set it to `0` once a live source replaces `_replay_readings` (see "Going live" in [docs/ARCHITECTURE.md](../docs/ARCHITECTURE.md)).

## Roll back a model

```bash
aws s3 ls s3://<bucket>/models/                       # list versions
aws ssm put-parameter --name /hawa/champion-model-version --value <version> --overwrite
```

The next hourly run serves that version.

## Tear down

```bash
sam delete
```

The S3 bucket must be emptied first (`aws s3 rm s3://<bucket> --recursive`, including old versions) because it has versioning on.

## What's verified and what isn't

- `cfn-lint infra/template.yaml` passes (CI runs it).
- The four Lambda handlers run against mocked S3, DynamoDB, SNS and SSM in `tests/test_aws_handlers.py`.
- The stack has **not** been deployed to a real account from this repo yet, so expect to fix small things on a first deploy (IAM edge cases, image build on arm64). Deploying it is the obvious next step.
