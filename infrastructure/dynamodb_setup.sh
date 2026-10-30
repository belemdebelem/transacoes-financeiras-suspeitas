#!/usr/bin/env bash
# Cria/garante a tabela DynamoDB para transações processadas.
#
# Uso: bash infrastructure/dynamodb_setup.sh

set -euo pipefail
source "$(dirname "$0")/env.sh"

export AWS_PROFILE="${AWS_PROFILE:-${DEPLOYER_PROFILE}}"

log_step "Configurando tabela DynamoDB ${DYNAMODB_TABLE}"

if aws_cmd dynamodb describe-table --table-name "$DYNAMODB_TABLE" >/dev/null 2>&1; then
  log_ok "Tabela ${DYNAMODB_TABLE} já existe."
else
  aws_cmd dynamodb create-table \
    --table-name "$DYNAMODB_TABLE" \
    --attribute-definitions \
      AttributeName=transaction_id,AttributeType=S \
      AttributeName=account_id,AttributeType=S \
      AttributeName=event_date,AttributeType=S \
    --key-schema \
      AttributeName=transaction_id,KeyType=HASH \
    --global-secondary-indexes \
      "[{\"IndexName\": \"account_id-index\", \"KeySchema\": [{\"AttributeName\": \"account_id\", \"KeyType\": \"HASH\"}, {\"AttributeName\": \"event_date\", \"KeyType\": \"RANGE\"}], \"Projection\": {\"ProjectionType\": \"ALL\"}, \"ProvisionedThroughput\": {\"ReadCapacityUnits\": 5, \"WriteCapacityUnits\": 5}}]" \
    --provisioned-throughput \
      ReadCapacityUnits=5,WriteCapacityUnits=5 \
    >/dev/null
  aws_cmd dynamodb wait table-exists --table-name "$DYNAMODB_TABLE"
  log_ok "Tabela ${DYNAMODB_TABLE} criada."
fi

log_info "Tabela: ${DYNAMODB_TABLE} (região ${AWS_REGION})"
