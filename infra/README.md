# Amazon Continuity Hub Infrastructure (Terraform)

This directory contains production-ready Infrastructure as Code (IaC) for deploying the Amazon Continuity Hub on AWS ECS Fargate, backed by DynamoDB, Application Load Balancer, and Amazon Bedrock.

## Remote State Bootstrap

To manage Terraform state securely in a shared team or CI/CD environment, bootstrap an S3 backend with DynamoDB state locking before running Terraform:

### 1. Create S3 Bucket and DynamoDB Lock Table

```bash
# Set your variables
export AWS_REGION="us-east-1"
export STATE_BUCKET="amazon-continuity-hub-tfstate-$(aws sts get-caller-identity --query Account --output text)"
export LOCK_TABLE="amazon-continuity-hub-tflock"

# Create S3 bucket for state storage
aws s3api create-bucket \
  --bucket "${STATE_BUCKET}" \
  --region "${AWS_REGION}"

# Enable bucket versioning
aws s3api put-bucket-versioning \
  --bucket "${STATE_BUCKET}" \
  --versioning-configuration Status=Enabled

# Enable default server-side encryption (AES256)
aws s3api put-bucket-encryption \
  --bucket "${STATE_BUCKET}" \
  --server-side-encryption-configuration '{
    "Rules": [
      {
        "ApplyServerSideEncryptionByDefault": {
          "SSEAlgorithm": "AES256"
        }
      }
    ]
  }'

# Block public access
aws s3api put-public-access-block \
  --bucket "${STATE_BUCKET}" \
  --public-access-block-configuration '{
    "BlockPublicAcls": true,
    "IgnorePublicAcls": true,
    "BlockPublicPolicy": true,
    "RestrictPublicBuckets": true
  }'

# Create DynamoDB table for state locking
aws dynamodb create-table \
  --table-name "${LOCK_TABLE}" \
  --attribute-definitions AttributeName=LockID,AttributeType=S \
  --key-schema AttributeName=LockID,KeyType=HASH \
  --billing-mode PAY_PER_REQUEST \
  --region "${AWS_REGION}"
```

### 2. Configure Backend in `providers.tf`

Add the backend block inside `terraform {}` in `providers.tf`:

```hcl
terraform {
  backend "s3" {
    bucket         = "amazon-continuity-hub-tfstate-<account-id>"
    key            = "production/terraform.tfstate"
    region         = "us-east-1"
    dynamodb_table = "amazon-continuity-hub-tflock"
    encrypt        = true
  }
}
```

### 3. Deploy Infrastructure

```bash
cd infra
cp terraform.tfvars.example terraform.tfvars
terraform init
terraform plan -out=tfplan
terraform apply tfplan
```
