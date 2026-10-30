#!/usr/bin/env bash
# Empacota/publica a Lambda e configura o gatilho do bucket S3.
#
# Uso: bash infrastructure/lambda_setup.sh

set -euo pipefail
source "$(dirname "$0")/env.sh"

export AWS_PROFILE="${AWS_PROFILE:-${DEPLOYER_PROFILE}}"

LAMBDA_RUNTIME="${LAMBDA_RUNTIME:-python3.12}"
LAMBDA_HANDLER="${LAMBDA_HANDLER:-src.processing.lambda_handler.lambda_handler}"
LAMBDA_TIMEOUT="${LAMBDA_TIMEOUT:-60}"
LAMBDA_MEMORY="${LAMBDA_MEMORY:-256}"
LAMBDA_TRIGGER_ID="${LAMBDA_TRIGGER_ID:-${PROJECT_PREFIX}-raw-json-created}"
LAMBDA_ROLE_ARN="arn:aws:iam::${AWS_ACCOUNT_ID}:role/${LAMBDA_ROLE}"
LAMBDA_FUNCTION_ARN="arn:aws:lambda:${AWS_REGION}:${AWS_ACCOUNT_ID}:function:${LAMBDA_FUNCTION}"
BUCKET_ARN="arn:aws:s3:::${RAW_BUCKET}"
SNS_TOPIC_ARN="${SNS_TOPIC_ARN:-arn:aws:sns:${AWS_REGION}:${AWS_ACCOUNT_ID}:${ALERTS_TOPIC}}"
METRICS_NAMESPACE="${METRICS_NAMESPACE:-SuspiciousTransactions}"
METRICS_SERVICE="${METRICS_SERVICE:-transaction-processor}"
ROOT_DIR="$(cd "$(dirname "$0")/.." && pwd)"
TMP_DIR="$(mktemp -d)"
trap 'rm -rf "$TMP_DIR"' EXIT

log_step "Empacotando o código da Lambda"
python3 - "$ROOT_DIR" "$TMP_DIR/lambda.zip" <<'PY'
import pathlib
import sys
import zipfile

root = pathlib.Path(sys.argv[1])
archive = pathlib.Path(sys.argv[2])
with zipfile.ZipFile(archive, "w", zipfile.ZIP_DEFLATED) as package:
    for path in (root / "src").rglob("*.py"):
        if "__pycache__" not in path.parts:
            package.write(path, path.relative_to(root))
PY
log_ok "Pacote criado."

log_step "Preparando o papel de execução ${LAMBDA_ROLE}"
TRUST_POLICY="${TMP_DIR}/trust-policy.json"
ROLE_POLICY="${TMP_DIR}/role-policy.json"
cat >"$TRUST_POLICY" <<'JSON'
{
  "Version": "2012-10-17",
  "Statement": [{
    "Effect": "Allow",
    "Principal": {"Service": "lambda.amazonaws.com"},
    "Action": "sts:AssumeRole"
  }]
}
JSON

python3 - "$RAW_BUCKET" "$RAW_PREFIX" "$DYNAMODB_TABLE" "$SNS_TOPIC_ARN" "$AWS_REGION" "$AWS_ACCOUNT_ID" "$ROLE_POLICY" <<'PY'
import json
import sys

bucket, prefix, table, topic_arn, region, account, output = sys.argv[1:8]
policy = {
    "Version": "2012-10-17",
    "Statement": [
        {
            "Effect": "Allow",
            "Action": "s3:GetObject",
            "Resource": f"arn:aws:s3:::{bucket}/{prefix}*",
        },
        {
            "Effect": "Allow",
            "Action": ["s3:PutObject", "s3:PutObjectAcl"],
            "Resource": f"arn:aws:s3:::{bucket}/processed/*",
        },
        {
            "Effect": "Allow",
            "Action": ["dynamodb:BatchWriteItem", "dynamodb:PutItem"],
            "Resource": f"arn:aws:dynamodb:{region}:{account}:table/{table}",
        },
        {
            "Effect": "Allow",
            "Action": "sns:Publish",
            "Resource": topic_arn,
        },
        {
            "Effect": "Allow",
            "Action": "cloudwatch:PutMetricData",
            "Resource": "*",
        },
    ],
}
with open(output, "w", encoding="utf-8") as handle:
    json.dump(policy, handle)
PY

if aws_iam iam get-role --role-name "$LAMBDA_ROLE" >/dev/null 2>&1; then
  aws_iam iam update-assume-role-policy \
    --role-name "$LAMBDA_ROLE" \
    --policy-document "file://${TRUST_POLICY}" >/dev/null
else
  aws_iam iam create-role \
    --role-name "$LAMBDA_ROLE" \
    --assume-role-policy-document "file://${TRUST_POLICY}" >/dev/null
fi
aws_iam iam put-role-policy \
  --role-name "$LAMBDA_ROLE" \
  --policy-name "${PROJECT_PREFIX}-read-raw-transactions" \
  --policy-document "file://${ROLE_POLICY}" >/dev/null
aws_iam iam attach-role-policy \
  --role-name "$LAMBDA_ROLE" \
  --policy-arn arn:aws:iam::aws:policy/service-role/AWSLambdaBasicExecutionRole
aws_iam iam wait role-exists --role-name "$LAMBDA_ROLE"
log_ok "Papel configurado para S3 (${RAW_PREFIX}, ${PROCESSED_PREFIX}), DynamoDB (${DYNAMODB_TABLE}), SNS (${SNS_TOPIC_ARN}), logs e métricas do CloudWatch."

log_step "Configurando a Lambda ${LAMBDA_FUNCTION}"
ENVIRONMENT_FILE="${TMP_DIR}/lambda-environment.json"
python3 - "$RAW_BUCKET" "$RAW_PREFIX" "$DYNAMODB_TABLE" "$SNS_TOPIC_ARN" "$ENVIRONMENT_FILE" "$METRICS_NAMESPACE" "$METRICS_SERVICE" <<'PY'
import json
import sys

bucket, prefix, table, topic, output, metrics_namespace, metrics_service = sys.argv[1:8]
with open(output, "w", encoding="utf-8") as handle:
    json.dump({"Variables": {
        "S3_RAW_BUCKET": bucket,
        "S3_RAW_PREFIX": prefix,
        "S3_PROCESSED_PREFIX": "processed/",
        "DYNAMODB_TABLE": table,
        "SNS_TOPIC_ARN": topic,
        "ALERTS_ENABLED": "true",
        "METRICS_ENABLED": "true",
        "METRICS_NAMESPACE": metrics_namespace,
        "METRICS_SERVICE": metrics_service,
    }}, handle)
PY

if aws_cmd lambda get-function --function-name "$LAMBDA_FUNCTION" >/dev/null 2>&1; then
  aws_cmd lambda update-function-code \
    --function-name "$LAMBDA_FUNCTION" \
    --zip-file "fileb://${TMP_DIR}/lambda.zip" >/dev/null
  aws_cmd lambda wait function-updated --function-name "$LAMBDA_FUNCTION"
  aws_cmd lambda update-function-configuration \
    --function-name "$LAMBDA_FUNCTION" \
    --role "$LAMBDA_ROLE_ARN" \
    --runtime "$LAMBDA_RUNTIME" \
    --handler "$LAMBDA_HANDLER" \
    --timeout "$LAMBDA_TIMEOUT" \
    --memory-size "$LAMBDA_MEMORY" \
    --environment "file://${ENVIRONMENT_FILE}" >/dev/null
  aws_cmd lambda wait function-updated --function-name "$LAMBDA_FUNCTION"
else
  aws_cmd lambda create-function \
    --function-name "$LAMBDA_FUNCTION" \
    --runtime "$LAMBDA_RUNTIME" \
    --role "$LAMBDA_ROLE_ARN" \
    --handler "$LAMBDA_HANDLER" \
    --timeout "$LAMBDA_TIMEOUT" \
    --memory-size "$LAMBDA_MEMORY" \
    --zip-file "fileb://${TMP_DIR}/lambda.zip" \
    --environment "file://${ENVIRONMENT_FILE}" >/dev/null
fi
aws_cmd lambda wait function-active-v2 --function-name "$LAMBDA_FUNCTION"
log_ok "Função ${LAMBDA_FUNCTION} publicada."

log_step "Autorizando o S3 a invocar a Lambda"
EXISTING_POLICY="$(aws_cmd lambda get-policy \
  --function-name "$LAMBDA_FUNCTION" \
  --query Policy --output text 2>/dev/null || true)"
if ! python3 -c '
import json, sys
try:
    policy = json.loads(sys.stdin.read())
    if isinstance(policy, str):
        policy = json.loads(policy)
    exists = any(
        statement.get("Sid") == sys.argv[1]
        for statement in policy.get("Statement", [])
    )
except (json.JSONDecodeError, TypeError):
    exists = False
sys.exit(0 if exists else 1)
' "${LAMBDA_TRIGGER_ID}" <<<"$EXISTING_POLICY"; then
  aws_cmd lambda add-permission \
    --function-name "$LAMBDA_FUNCTION" \
    --statement-id "$LAMBDA_TRIGGER_ID" \
    --action lambda:InvokeFunction \
    --principal s3.amazonaws.com \
    --source-arn "$BUCKET_ARN" \
    --source-account "$AWS_ACCOUNT_ID" >/dev/null
fi

NOTIFICATIONS_FILE="${TMP_DIR}/bucket-notifications.json"
aws_cmd s3api get-bucket-notification-configuration \
  --bucket "$RAW_BUCKET" >"${TMP_DIR}/current-notifications.json"
python3 - \
  "${TMP_DIR}/current-notifications.json" \
  "$NOTIFICATIONS_FILE" \
  "$LAMBDA_TRIGGER_ID" \
  "$LAMBDA_FUNCTION_ARN" \
  "$RAW_PREFIX" <<'PY'
import json
import sys

source, target, trigger_id, function_arn, prefix = sys.argv[1:6]
with open(source, encoding="utf-8") as handle:
    content = handle.read().strip()
configuration = json.loads(content) if content else {}

lambda_configs = configuration.setdefault("LambdaFunctionConfigurations", [])
lambda_configs[:] = [
    item for item in lambda_configs if item.get("Id") != trigger_id
]
lambda_configs.append({
    "Id": trigger_id,
    "LambdaFunctionArn": function_arn,
    "Events": ["s3:ObjectCreated:*"],
    "Filter": {"Key": {"FilterRules": [
        {"Name": "prefix", "Value": prefix},
        {"Name": "suffix", "Value": ".json"},
    ]}},
})
with open(target, "w", encoding="utf-8") as handle:
    json.dump(configuration, handle)
PY
aws_cmd s3api put-bucket-notification-configuration \
  --bucket "$RAW_BUCKET" \
  --notification-configuration "file://${NOTIFICATIONS_FILE}"

log_ok "Gatilho ativo para objetos .json no prefixo ${RAW_PREFIX}."
log_info "Função: ${LAMBDA_FUNCTION_ARN}"
