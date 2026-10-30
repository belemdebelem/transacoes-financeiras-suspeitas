#!/usr/bin/env bash
# Configuração compartilhada pelos scripts de infraestrutura.
#
# Uso:  source infrastructure/env.sh
# Todos os scripts de setup sourced este arquivo antes de qualquer comando.

set -euo pipefail

AWS_REGION="${AWS_REGION:-us-east-2}"
AWS_ACCOUNT_ID="${AWS_ACCOUNT_ID:?Defina AWS_ACCOUNT_ID com o ID da sua conta antes de usar os scripts AWS}"

PROJECT_PREFIX="${PROJECT_PREFIX:-suspicious-transactions}"

RAW_BUCKET="${RAW_BUCKET:-${PROJECT_PREFIX}-raw-${AWS_ACCOUNT_ID}}"
DYNAMODB_TABLE="${DYNAMODB_TABLE:-${PROJECT_PREFIX}-processed}"
ALERTS_TOPIC="${ALERTS_TOPIC:-${PROJECT_PREFIX}-alerts}"
OPS_TOPIC="${OPS_TOPIC:-${PROJECT_PREFIX}-ops}"
LAMBDA_FUNCTION="${LAMBDA_FUNCTION:-${PROJECT_PREFIX}-processor}"
LAMBDA_ROLE="${LAMBDA_ROLE:-${PROJECT_PREFIX}-lambda-role}"

DEPLOYER_USER="${DEPLOYER_USER:-portfolio-deployer}"
DEPLOYER_PROFILE="${DEPLOYER_PROFILE:-portfolio}"
DEPLOYER_POLICY="${DEPLOYER_POLICY:-PortfolioDeployerPolicy}"

RAW_PREFIX="${RAW_PREFIX:-raw-data/}"
PROCESSED_PREFIX="${PROCESSED_PREFIX:-processed/}"

METRICS_NAMESPACE="${METRICS_NAMESPACE:-SuspiciousTransactions}"
METRICS_SERVICE="${METRICS_SERVICE:-transaction-processor}"
ALARM_ACTION_ARN="${ALARM_ACTION_ARN:-arn:aws:sns:${AWS_REGION}:${AWS_ACCOUNT_ID}:${ALERTS_TOPIC}}"
PROCESSING_DURATION_ALARM_MS="${PROCESSING_DURATION_ALARM_MS:-30000}"

# --- helpers -----------------------------------------------------------------

log_step() { printf '\n\033[1;34m==> %s\033[0m\n' "$*"; }
log_info() { printf '    %s\n' "$*"; }
log_ok()   { printf '\033[1;32m    ok: %s\033[0m\n' "$*"; }
log_warn() { printf '\033[1;33m    aviso: %s\033[0m\n' "$*"; }
log_err()  { printf '\033[1;31m    erro: %s\033[0m\n' "$*" >&2; }

aws_cmd() { aws --region "$AWS_REGION" "$@"; }

aws_iam() { aws --region "$AWS_REGION" "$@"; }

bucket_exists() {
  aws s3api head-bucket --bucket "$RAW_BUCKET" >/dev/null 2>&1
}

resource_exists() {
  # $1 = service, $2 = comando de listagem, $3 = nome esperado
  aws_cmd "$1" "$2" --output json 2>/dev/null | grep -q "\"$3\""
}
