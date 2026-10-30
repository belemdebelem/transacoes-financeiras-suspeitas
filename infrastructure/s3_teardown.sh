#!/usr/bin/env bash
# Remove o bucket de dados brutos e todo o seu conteúdo.
#
# Uso: bash infrastructure/s3_teardown.sh
# Use --dry-run para ver o que seria removido sem apagar nada.

set -euo pipefail
source "$(dirname "$0")/env.sh"

DRY_RUN=false
if [[ "${1:-}" == "--dry-run" ]]; then
  DRY_RUN=true
fi

if ! bucket_exists; then
  log_info "O bucket ${RAW_BUCKET} não existe, nada a fazer."
  exit 0
fi

log_step "Objetos presentes no bucket ${RAW_BUCKET}"
aws_cmd s3 ls "s3://${RAW_BUCKET}" --recursive --region "$AWS_REGION" || true

if [[ "$DRY_RUN" == true ]]; then
  log_warn "Modo dry-run: nada será removido."
  exit 0
fi

log_step "Removendo objetos e versões do bucket"
aws_cmd s3 rm "s3://${RAW_BUCKET}" --recursive --region "$AWS_REGION" >/dev/null 2>&1 || true
aws_cmd s3api list-object-versions \
  --bucket "$RAW_BUCKET" \
  --query 'Versions[].{Key:Key,VersionId:VersionId}' \
  --output text 2>/dev/null | while read -r key version; do
  [[ -z "$key" || "$key" == "None" ]] && continue
  aws_cmd s3api delete-object --bucket "$RAW_BUCKET" --key "$key" --version-id "$version" >/dev/null 2>&1 || true
done

log_step "Removendo as versões não atuais"
aws_cmd s3api delete-objects \
  --bucket "$RAW_BUCKET" \
  --delete "$(aws_cmd s3api list-object-versions --bucket "$RAW_BUCKET" --query '{Objects: Versions[].{Key:Key,VersionId:VersionId}}' --output json 2>/dev/null | python3 -c 'import json,sys; d=json.load(sys.stdin); print(json.dumps(d))' 2>/dev/null || echo '{"Objects":[]}')" >/dev/null 2>&1 || true

log_step "Removendo as configurações do bucket"
aws_cmd s3api delete-bucket-policy --bucket "$RAW_BUCKET" >/dev/null 2>&1 || true
aws_cmd s3api delete-bucket-lifecycle --bucket "$RAW_BUCKET" >/dev/null 2>&1 || true
aws_cmd s3api delete-bucket-tagging --bucket "$RAW_BUCKET" >/dev/null 2>&1 || true

log_step "Removendo o bucket"
aws_cmd s3api delete-bucket --bucket "$RAW_BUCKET"
log_ok "Bucket ${RAW_BUCKET} removido."
