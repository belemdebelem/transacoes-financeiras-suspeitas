#!/usr/bin/env bash
# Cria/garante o tópico SNS para alertas.
#
# Uso: bash infrastructure/sns_setup.sh [seu-email@example.com]

set -euo pipefail
source "$(dirname "$0")/env.sh"

export AWS_PROFILE="${AWS_PROFILE:-${DEPLOYER_PROFILE}}"
EMAIL="${1:-}"

log_step "Configurando tópico SNS ${ALERTS_TOPIC}"

TOPIC_ARN=$(aws_cmd sns create-topic --name "$ALERTS_TOPIC" --query TopicArn --output text)
log_ok "Tópico: ${TOPIC_ARN}"

if [ -n "$EMAIL" ]; then
  log_step "Inscrevendo ${EMAIL} no tópico"
  SUB_ARN=$(aws_cmd sns subscribe \
    --topic-arn "$TOPIC_ARN" \
    --protocol email \
    --notification-endpoint "$EMAIL" \
    --query SubscriptionArn --output text)
  log_info "Inscrição criada (pode ficar 'PendingConfirmation' até o e-mail ser confirmado)."
fi

log_info "Exportar para a Lambda: SNS_TOPIC_ARN=${TOPIC_ARN} ALERTS_ENABLED=true"
