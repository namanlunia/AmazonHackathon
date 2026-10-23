# Amazon Continuity Hub

Real-time cross-device state synchronization and event-routing mesh unifying Alexa, Fire TV, Firefox, and smart-home devices under one user context.

## 5-Line Quickstart

```bash
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env
uvicorn app.main:app --host 0.0.0.0 --port 8000 &
python scripts/simulate_mesh.py
```

## Environment Variables

| Variable | Type | Default | Description |
|---|---|---|---|
| `USE_AWS` | bool | `false` | `false` for offline in-memory store + mock AI; `true` for DynamoDB + Bedrock |
| `AWS_REGION` | string | `us-east-1` | AWS region for DynamoDB and Bedrock Runtime |
| `DYNAMODB_TABLE` | string | `AmazonContinuityState` | DynamoDB table name for unified user state persistence |
| `BEDROCK_MODEL_ID` | string | `us.amazon.nova-micro-v1:0` | Bedrock model ID or inference profile (alternative: `anthropic.claude-3-haiku-20240307-v1:0`) |
| `HUB_API_KEY` | string | `""` | Optional API Key (`X-API-Key` header or `api_key` query param); auth disabled if empty |
| `DEFAULT_USER_ID` | string | `default-user` | Fallback user ID when neither `X-User-Id` header nor Alexa session user is provided |
| `CORS_ORIGINS` | JSON list | `["*"]` | Allowed CORS origins for browser extension connections |
| `LOG_LEVEL` | string | `INFO` | Logging level (`DEBUG`, `INFO`, `WARNING`, `ERROR`) formatted as structured JSON |
| `STATE_TTL_DAYS` | integer | `7` | DynamoDB time-to-live expiration period in days |

## WebSocket Envelope Contract

All device communication uses standardized envelopes:

```json
{
  "type": "state_update",
  "id": "7b8d4e51-419a-4c22-bca4-d4ef4bcae310",
  "source_device_id": "firefox-mac",
  "target_device_id": null,
  "ts": "2026-10-23T17:00:00.000000+00:00",
  "payload": {
    "device_type": "firefox",
    "tabs": [
      {
        "title": "Prime Video - The Expanse",
        "url": "https://video.amazon.com/watch?v=123",
        "active": true,
        "media_position_seconds": 754.0
      }
    ]
  }
}
```

Envelope types supported: `snapshot`, `state_update`, `command`, `presence`, `ping`, `pong`, `ack`, and `error`.

## Switching on AWS Mode (`USE_AWS=true`)

Set `USE_AWS=true` in your environment or `.env`. On startup, the hub automatically creates the DynamoDB table (on-demand billing mode) and configures native TTL. If any AWS service throttles or fails at runtime, the hub logs a warning and transparently falls back to local execution.

### Minimum IAM Permissions Policy

```json
{
  "Version": "2012-10-17",
  "Statement": [
    {
      "Sid": "ContinuityDynamoDBAccess",
      "Effect": "Allow",
      "Action": [
        "dynamodb:GetItem",
        "dynamodb:PutItem",
        "dynamodb:CreateTable",
        "dynamodb:DescribeTable",
        "dynamodb:UpdateTimeToLive"
      ],
      "Resource": "arn:aws:dynamodb:*:*:table/AmazonContinuityState"
    },
    {
      "Sid": "ContinuityBedrockAccess",
      "Effect": "Allow",
      "Action": [
        "bedrock:InvokeModel"
      ],
      "Resource": [
        "arn:aws:bedrock:*::foundation-model/*",
        "arn:aws:bedrock:*:*:inference-profile/*"
      ]
    }
  ]
}
```

## Assumptions

1. **User Identity Mapping**: Requests without an `X-User-Id` header check `session.user.userId` in the Alexa payload, falling back to `DEFAULT_USER_ID`.
2. **Active Context Resolution**: When a browser publishes a tab with `active: true` and `media_position_seconds > 0`, or Fire TV publishes `now_playing` with `playing` state, the hub marks this as the unified `active_context`.
3. **Disconnection Handling**: Device disconnection marks `online=False` and broadcasts a `presence` update; prior device state and active context remain persisted in the store for subsequent continuity queries.
4. **Voice Constraints**: Alexa speech responses are strictly formatted to under 300 plain-text characters without SSML or markdown to avoid Alexa TTS syntax errors.
5. **Bedrock Timeout**: Bedrock Converse calls are wrapped in a 4.5s deadline; upon timeout or error, the hub returns a rule-derived mock response with `degraded=True` to satisfy Alexa's 6s deadline.

## Deployment Guide

### Deploying to Amazon EC2

1. Launch an Amazon Linux 2023 EC2 instance with an IAM Instance Profile containing the IAM permissions above.
2. Install Docker:
   ```bash
   sudo dnf install -y docker
   sudo systemctl enable --now docker
   sudo usermod -aG docker ec2-user
   ```
3. Run the container:
   ```bash
   docker run -d \
     --name amazon-continuity-hub \
     --restart unless-stopped \
     -p 8000:8000 \
     -e USE_AWS=true \
     -e AWS_REGION=us-east-1 \
     amazon-continuity-hub:latest
   ```

### Deploying to Amazon ECS (AWS Fargate)

1. Build and push the image to Amazon ECR:
   ```bash
   aws ecr get-login-password --region us-east-1 | docker login --username AWS --password-stdin <account_id>.dkr.ecr.us-east-1.amazonaws.com
   docker build -t amazon-continuity-hub .
   docker tag amazon-continuity-hub:latest <account_id>.dkr.ecr.us-east-1.amazonaws.com/amazon-continuity-hub:latest
   docker push <account_id>.dkr.ecr.us-east-1.amazonaws.com/amazon-continuity-hub:latest
   ```
2. Create an ECS Task Definition with:
   - Launch type: `FARGATE`
   - CPU / Memory: `0.5 vCPU` / `1 GB`
   - Task Role: Attached IAM role with the DynamoDB and Bedrock permissions above.
   - Container Port: `8000`
   - Healthcheck: `CMD-SHELL`, `python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/health')"`
3. Attach to an Application Load Balancer with a target group configured for WebSocket support (default HTTP/1.1 sticky sessions or connection upgrade).
