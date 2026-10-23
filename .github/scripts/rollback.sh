#!/usr/bin/env bash
set -euo pipefail

CLUSTER_NAME="${1:-${ECS_CLUSTER:-amazon-continuity-hub-cluster}}"
SERVICE_NAME="${2:-${ECS_SERVICE:-amazon-continuity-hub}}"

echo "Initiating automated rollback for service '${SERVICE_NAME}' in cluster '${CLUSTER_NAME}'..."

# Describe current service and extract current task definition ARN
CURRENT_TASKDEF_ARN=$(aws ecs describe-services \
  --cluster "${CLUSTER_NAME}" \
  --services "${SERVICE_NAME}" \
  --query "services[0].taskDefinition" \
  --output text)

if [[ -z "${CURRENT_TASKDEF_ARN}" || "${CURRENT_TASKDEF_ARN}" == "None" ]]; then
  echo "Error: Could not retrieve current task definition for service '${SERVICE_NAME}'." >&2
  exit 1
fi

echo "Current Task Definition: ${CURRENT_TASKDEF_ARN}"

# Extract family and revision
TASKDEF_FAMILY=$(echo "${CURRENT_TASKDEF_ARN}" | sed -E 's/.*task-definition\/([^:]+):([0-9]+)/\1/')
CURRENT_REVISION=$(echo "${CURRENT_TASKDEF_ARN}" | sed -E 's/.*task-definition\/([^:]+):([0-9]+)/\2/')

if [[ "${CURRENT_REVISION}" -le 1 ]]; then
  echo "Warning: Current revision is ${CURRENT_REVISION}; no previous revision exists to roll back to."
  exit 1
fi

PREVIOUS_REVISION=$((CURRENT_REVISION - 1))
TARGET_TASKDEF="${TASKDEF_FAMILY}:${PREVIOUS_REVISION}"

echo "Rolling back to previous revision: ${TARGET_TASKDEF}..."

aws ecs update-service \
  --cluster "${CLUSTER_NAME}" \
  --service "${SERVICE_NAME}" \
  --task-definition "${TARGET_TASKDEF}" \
  --output text > /dev/null

echo "Waiting for service to stabilize on ${TARGET_TASKDEF}..."
aws ecs wait services-stable \
  --cluster "${CLUSTER_NAME}" \
  --services "${SERVICE_NAME}"

echo "Rollback to ${TARGET_TASKDEF} succeeded."
