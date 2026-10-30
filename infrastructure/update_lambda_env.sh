#!/usr/bin/env bash
# Atualiza variáveis de ambiente da Lambda com DynamoDB/SNS/S3.
#
# Uso: 
#   export DYNAMODB_TABLE=suspicious-transactions-processed
#   export SNS_TOPIC_ARN=arn:aws:sns:us-east-2:123456789012:suspicious-transactions-alerts
#   bash infrastructure/update_lambda_env.sh

set -euo pipefail
source "$(dirname "$0")/env.sh"

export AWS_PROFILE="${AWS_PROFILE:-${DEPLOYER_PROFILE}}"

DYNAMODB_TABLE="${DYNAMODB_TABLE:-${PROJECT_PREFIX}-processed}"
SNS_TOPIC_ARN="${SNS_TOPIC_ARN:-arn:aws:sns:${AWS_REGION}:${AWS_ACCOUNT_ID}:${ALERTS_TOPIC}}"
ALERTS_ENABLED="${ALERTS_ENABLED:-true}"
METRICS_ENABLED="${METRICS_ENABLED:-true}"
METRICS_NAMESPACE="${METRICS_NAMESPACE:-SuspiciousTransactions}"
METRICS_SERVICE="${METRICS_SERVICE:-transaction-processor}"

log_step "Atualizando variáveis da Lambda ${LAMBDA_FUNCTION}"

ENV_JSON=$(python3 - "$RAW_BUCKET" "$RAW_PREFIX" "$DYNAMODB_TABLE" "$SNS_TOPIC_ARN" "$ALERTS_ENABLED" "$METRICS_ENABLED" "$METRICS_NAMESPACE" "$METRICS_SERVICE" << 'PY'
import json
import sys

bucket, prefix, table, topic, alerts, metrics, namespace, service = sys.argv[1:9]
vars = {
    "S3_RAW_BUCKET": bucket,
    "S3_RAW_PREFIX": prefix,
    "S3_PROCESSED_PREFIX": "processed/",
    "ALERTS_ENABLED": alerts,
    "METRICS_ENABLED": metrics,
    "METRICS_NAMESPACE": namespace,
    "METRICS_SERVICE": service,
}
if table:
    vars["DYNAMODB_TABLE"] = table
if topic:
    vars["SNS_TOPIC_ARN"] = topic
print(json.dumps({"Variables": vars}))
PY)

aws_cmd lambda update-function-configuration \
  --function-name "$LAMBDA_FUNCTION" \
  --environment "$ENV_JSON" >/dev/null

aws_cmd lambda wait function-updated --function-name "$LAMBDA_FUNCTION"
log_ok "Variáveis atualizadas."
