#!/usr/bin/env bash
# Teste end-to-end: gera lote local, faz upload no S3 e acompanha logs da Lambda.
#
# Uso: bash infrastructure/e2e_test.sh

set -euo pipefail
source "$(dirname "$0")/env.sh"

export AWS_PROFILE="${AWS_PROFILE:-${DEPLOYER_PROFILE}}"
export AWS_REGION
# The standard CLI path uploads only the raw object; the S3 event invokes Lambda.

log_step "Gerando e enviando lote de transações para S3"
UPLOAD_OUTPUT="$(python3 main.py --bucket "$RAW_BUCKET" --transactions 200 --seed 42)"
printf '%s\n' "$UPLOAD_OUTPUT"
RAW_FILE="$(sed -n 's/^Arquivo bruto:[[:space:]]*//p' <<<"$UPLOAD_OUTPUT")"
RAW_KEY="$(sed -n 's/.*Publicado no S3: s3:\/\/[^/]*\///p' <<<"$UPLOAD_OUTPUT")"
if [[ -z "$RAW_FILE" || -z "$RAW_KEY" ]]; then
  log_err "Não foi possível identificar o arquivo local e a chave S3 do lote."
  exit 1
fi
RAW_NAME="${RAW_KEY##*/}"
PROCESSED_NAME="${RAW_NAME%.json}_processed.json"
SUMMARY_NAME="${RAW_NAME%.json}_processed_summary.json"

log_step "Aguardando a execução da Lambda nos logs do CloudWatch (até 60s)"
LOG_GROUP="/aws/lambda/${LAMBDA_FUNCTION}"
START_TIME=$(($(date +%s)*1000 - 900000))
RESULT_JSON=""
for _ in $(seq 1 12); do
  LOG_EVENTS="$(aws_cmd logs filter-log-events \
    --log-group-name "$LOG_GROUP" \
    --start-time "$START_TIME" \
    --max-items 100 \
    --output json 2>&1 || true)"
  RESULT_JSON="$(LOG_EVENTS="$LOG_EVENTS" RAW_NAME="$RAW_NAME" python3 - <<'PY'
import json, os, sys

def json_objects(value):
    """Find JSON objects in plain, prefixed, or Lambda JSON-formatted logs."""
    if isinstance(value, dict):
        yield value
        for child in value.values():
            if isinstance(child, (dict, str)):
                yield from json_objects(child)
        return
    if not isinstance(value, str):
        return
    decoder = json.JSONDecoder()
    for offset, char in enumerate(value):
        if char != "{":
            continue
        try:
            parsed, _ = decoder.raw_decode(value[offset:])
        except json.JSONDecodeError:
            continue
        if isinstance(parsed, dict):
            yield from json_objects(parsed)

try:
    events = json.loads(os.environ["LOG_EVENTS"]).get("events", [])
except json.JSONDecodeError:
    events = []
matches = []
for event in events:
    message = event.get("message", "")
    for payload in json_objects(message):
        if (payload.get("event") == "batch_processed"
                and payload.get("status") == "success"
                and os.environ["RAW_NAME"] in payload.get("input_path", "")):
            matches.append(payload)
if not matches:
    sys.exit(0)
result = matches[-1]
if result.get("dynamodb_written") != result.get("records_processed"):
    print(f"DynamoDB incompleto: {result.get('dynamodb_written')}/{result.get('records_processed')}", file=sys.stderr)
    sys.exit(1)
if result.get("alerts_published") != result.get("alerts_generated"):
    print("A quantidade de alertas publicados difere dos alertas gerados.", file=sys.stderr)
    sys.exit(1)
print(json.dumps(result, ensure_ascii=False))
PY
  )"
  [[ -n "$RESULT_JSON" ]] && break
  sleep 5
done
if [[ -z "$RESULT_JSON" ]]; then
  if grep -Eqi 'AccessDenied|not authorized|UnauthorizedOperation' <<<"$LOG_EVENTS"; then
    log_err "O perfil ${AWS_PROFILE} não pode consultar os logs. Atualize PortfolioDeployerPolicy com ReadTransactionProcessorLogs."
    exit 1
  fi
  log_err "Nenhum evento batch_processed bem-sucedido para ${RAW_NAME} apareceu no CloudWatch."
  LOG_EVENTS="$LOG_EVENTS" RAW_NAME="$RAW_NAME" python3 - <<'PY'
import json, os
try:
    events = json.loads(os.environ["LOG_EVENTS"]).get("events", [])
except json.JSONDecodeError:
    events = []
related = [event.get("message", "") for event in events
           if os.environ["RAW_NAME"] in event.get("message", "")]
if related:
    print("Logs encontrados para o arquivo, mas sem resultado de sucesso:")
    for message in related[-5:]:
        print(message.rstrip())
else:
    print("Não há eventos de log que mencionem o arquivo enviado.")
PY
  log_info "Confira a permissão de invocação S3, o filtro raw-data/ e os logs de erro da função."
  exit 1
fi
printf '%s\n' "$RESULT_JSON"

log_step "Aguardando os objetos de saída no S3 (até 60s)"
PROCESSED_KEY=""
SUMMARY_KEY=""
for _ in $(seq 1 12); do
  PROCESSED_KEY="$(aws_cmd s3api list-objects-v2 --bucket "$RAW_BUCKET" --prefix "$PROCESSED_PREFIX" --query "Contents[?ends_with(Key, '$PROCESSED_NAME')].Key | [0]" --output text 2>/dev/null || true)"
  SUMMARY_KEY="$(aws_cmd s3api list-objects-v2 --bucket "$RAW_BUCKET" --prefix "$PROCESSED_PREFIX" --query "Contents[?ends_with(Key, '$SUMMARY_NAME')].Key | [0]" --output text 2>/dev/null || true)"
  [[ "$PROCESSED_KEY" != "None" && -n "$PROCESSED_KEY" && "$SUMMARY_KEY" != "None" && -n "$SUMMARY_KEY" ]] && break
  sleep 5
done
if [[ -z "$PROCESSED_KEY" || "$PROCESSED_KEY" == "None" || -z "$SUMMARY_KEY" || "$SUMMARY_KEY" == "None" ]]; then
  log_err "A Lambda não publicou ambos os objetos esperados em ${PROCESSED_PREFIX}."
  exit 1
fi
log_ok "Saídas encontradas: ${PROCESSED_KEY} e ${SUMMARY_KEY}."

TRANSACTION_ID="$(python3 - "$RAW_FILE" <<'PY'
import json, sys
with open(sys.argv[1], encoding="utf-8") as handle:
    transactions = json.load(handle)
print(transactions[0]["transaction_id"])
PY
)"
STORED_ID="$(aws_cmd dynamodb get-item --table-name "$DYNAMODB_TABLE" --key "{\"transaction_id\":{\"S\":\"${TRANSACTION_ID}\"}}" --query 'Item.transaction_id.S' --output text)"
if [[ "$STORED_ID" != "$TRANSACTION_ID" ]]; then
  log_err "Transação ${TRANSACTION_ID} não foi encontrada no DynamoDB."
  exit 1
fi
log_ok "Transação ${TRANSACTION_ID} confirmada no DynamoDB."
log_ok "Fluxo ponta a ponta validado: gatilho/Lambda, S3, DynamoDB, SNS e CloudWatch."
