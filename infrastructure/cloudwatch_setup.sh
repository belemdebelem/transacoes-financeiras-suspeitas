#!/usr/bin/env bash
# Cria/atualiza os alarmes do CloudWatch para o processamento.
#
# Uso:
#   bash infrastructure/cloudwatch_setup.sh
#
# Por padrão as notificações usam o tópico de alertas já configurado. Defina
# ALARM_ACTION_ARN para usar um tópico operacional separado.

set -euo pipefail
source "$(dirname "$0")/env.sh"

export AWS_PROFILE="${AWS_PROFILE:-${DEPLOYER_PROFILE}}"

ALARM_ACTION_ARN="${ALARM_ACTION_ARN:-arn:aws:sns:${AWS_REGION}:${AWS_ACCOUNT_ID}:${ALERTS_TOPIC}}"
PROCESSING_DURATION_ALARM_MS="${PROCESSING_DURATION_ALARM_MS:-30000}"
CUSTOM_DIMENSIONS="Name=Service,Value=${METRICS_SERVICE}"

log_step "Configurando alarmes em ${AWS_REGION}"

ALARM_ACTIONS=()
if aws_cmd sns get-topic-attributes --topic-arn "$ALARM_ACTION_ARN" >/dev/null 2>&1; then
  ALARM_ACTIONS=(--alarm-actions "$ALARM_ACTION_ARN")
  log_info "Notificações: ${ALARM_ACTION_ARN}"
else
  log_warn "Tópico ${ALARM_ACTION_ARN} não encontrado; os alarmes serão criados sem ação de notificação."
fi

put_alarm() {
  local name="$1"
  shift
  aws_cmd cloudwatch put-metric-alarm \
    --alarm-name "$name" \
    --actions-enabled \
    --treat-missing-data notBreaching \
    "${ALARM_ACTIONS[@]}" \
    "$@" >/dev/null
  log_ok "Alarme ${name} configurado."
}

put_alarm "${PROJECT_PREFIX}-lambda-errors" \
  --alarm-description "Erros não tratados na função ${LAMBDA_FUNCTION}." \
  --namespace AWS/Lambda \
  --metric-name Errors \
  --dimensions "Name=FunctionName,Value=${LAMBDA_FUNCTION}" \
  --statistic Sum \
  --period 300 \
  --evaluation-periods 1 \
  --datapoints-to-alarm 1 \
  --threshold 1 \
  --comparison-operator GreaterThanOrEqualToThreshold \
  --unit Count

put_alarm "${PROJECT_PREFIX}-batch-failures" \
  --alarm-description "Lotes que terminaram com status de erro no processamento." \
  --namespace "$METRICS_NAMESPACE" \
  --metric-name BatchFailures \
  --dimensions "$CUSTOM_DIMENSIONS" \
  --statistic Sum \
  --period 300 \
  --evaluation-periods 1 \
  --datapoints-to-alarm 1 \
  --threshold 1 \
  --comparison-operator GreaterThanOrEqualToThreshold \
  --unit Count

put_alarm "${PROJECT_PREFIX}-processing-duration-high" \
  --alarm-description "Processamento acima de ${PROCESSING_DURATION_ALARM_MS} ms." \
  --namespace "$METRICS_NAMESPACE" \
  --metric-name ProcessingDurationMs \
  --dimensions "$CUSTOM_DIMENSIONS" \
  --statistic Maximum \
  --period 300 \
  --evaluation-periods 1 \
  --datapoints-to-alarm 1 \
  --threshold "$PROCESSING_DURATION_ALARM_MS" \
  --comparison-operator GreaterThanOrEqualToThreshold \
  --unit Milliseconds

put_alarm "${PROJECT_PREFIX}-dynamodb-write-failures" \
  --alarm-description "Registros processados que não foram gravados no DynamoDB." \
  --namespace "$METRICS_NAMESPACE" \
  --metric-name DynamoDBWriteFailures \
  --dimensions "$CUSTOM_DIMENSIONS" \
  --statistic Sum \
  --period 300 \
  --evaluation-periods 1 \
  --datapoints-to-alarm 1 \
  --threshold 1 \
  --comparison-operator GreaterThanOrEqualToThreshold \
  --unit Count

put_alarm "${PROJECT_PREFIX}-sns-publish-failures" \
  --alarm-description "Alertas que não foram publicados no SNS." \
  --namespace "$METRICS_NAMESPACE" \
  --metric-name AlertsPublishFailures \
  --dimensions "$CUSTOM_DIMENSIONS" \
  --statistic Sum \
  --period 300 \
  --evaluation-periods 1 \
  --datapoints-to-alarm 1 \
  --threshold 1 \
  --comparison-operator GreaterThanOrEqualToThreshold \
  --unit Count

put_alarm "${PROJECT_PREFIX}-s3-processed-upload-failures" \
  --alarm-description "Falhas ao enviar o arquivo processado ao S3." \
  --namespace "$METRICS_NAMESPACE" \
  --metric-name S3ProcessedUploadFailures \
  --dimensions "$CUSTOM_DIMENSIONS" \
  --statistic Sum \
  --period 300 \
  --evaluation-periods 1 \
  --datapoints-to-alarm 1 \
  --threshold 1 \
  --comparison-operator GreaterThanOrEqualToThreshold \
  --unit Count

put_alarm "${PROJECT_PREFIX}-s3-summary-upload-failures" \
  --alarm-description "Falhas ao enviar o resumo operacional ao S3." \
  --namespace "$METRICS_NAMESPACE" \
  --metric-name S3SummaryUploadFailures \
  --dimensions "$CUSTOM_DIMENSIONS" \
  --statistic Sum \
  --period 300 \
  --evaluation-periods 1 \
  --datapoints-to-alarm 1 \
  --threshold 1 \
  --comparison-operator GreaterThanOrEqualToThreshold \
  --unit Count

log_step "Alarmes configurados"
aws_cmd cloudwatch describe-alarms \
  --alarm-name-prefix "${PROJECT_PREFIX}-" \
  --query 'MetricAlarms[].[AlarmName,StateValue]' \
  --output table
