#!/usr/bin/env bash
# Etapa 1 - Cria e protege o bucket de dados brutos.
#
# Uso: bash infrastructure/s3_setup.sh
# Idempotente: pode ser executado quantas vezes for preciso.

set -euo pipefail
source "$(dirname "$0")/env.sh"

log_step "Bucket de dados brutos: ${RAW_BUCKET} (região ${AWS_REGION})"

if bucket_exists; then
  log_info "O bucket já existe, apenas reaplicando a configuração."
else
  log_info "Criando o bucket..."
  if [[ "$AWS_REGION" == "us-east-1" ]]; then
    aws_cmd s3api create-bucket --bucket "$RAW_BUCKET"
  else
    # fora de us-east-1 o LocationConstraint é obrigatório
    aws_cmd s3api create-bucket \
      --bucket "$RAW_BUCKET" \
      --create-bucket-configuration "LocationConstraint=${AWS_REGION}"
  fi
  log_ok "Bucket criado."
fi

log_step "Bloqueando todo o acesso público (Block Public Access)"
aws_cmd s3api put-public-access-block \
  --bucket "$RAW_BUCKET" \
  --public-access-block-configuration \
  "BlockPublicAcls=true,IgnorePublicAcls=true,BlockPublicPolicy=true,RestrictPublicBuckets=true"
log_ok "Acesso público bloqueado."

log_step "Habilitando versionamento de objetos"
aws_cmd s3api put-bucket-versioning \
  --bucket "$RAW_BUCKET" \
  --versioning-configuration Status=Enabled
log_ok "Versionamento habilitado."

log_step "Habilitando criptografia padrão (SSE-S3 / AES256)"
aws_cmd s3api put-bucket-encryption \
  --bucket "$RAW_BUCKET" \
  --server-side-encryption-configuration \
  '{"Rules":[{"ApplyServerSideEncryptionByDefault":{"SSEAlgorithm":"AES256"}}]}'
log_ok "Criptografia padrão habilitada."

log_step "Negando requisições feitas sem HTTPS"
aws_cmd s3api put-bucket-policy --bucket "$RAW_BUCKET" --policy "$(cat <<POLICY
{
  "Version": "2012-10-17",
  "Statement": [
    {
      "Sid": "DenyInsecureTransport",
      "Effect": "Deny",
      "Principal": "*",
      "Action": "s3:*",
      "Resource": [
        "arn:aws:s3:::${RAW_BUCKET}",
        "arn:aws:s3:::${RAW_BUCKET}/*"
      ],
      "Condition": {
        "Bool": { "aws:SecureTransport": "false" }
      }
    }
  ]
}
POLICY
)"
log_ok "Somente HTTPS permitido."

log_step "Configurando ciclo de vida (versões antigas expiram em 30 dias)"
aws_cmd s3api put-bucket-lifecycle-configuration \
  --bucket "$RAW_BUCKET" \
  --lifecycle-configuration "$(cat <<LIFECYCLE
{
  "Rules": [
    {
      "ID": "expire-noncurrent-versions",
      "Status": "Enabled",
      "Filter": { "Prefix": "" },
      "NoncurrentVersionExpiration": { "NoncurrentDays": 30 },
      "AbortIncompleteMultipartUpload": { "DaysAfterInitiation": 7 }
    }
  ]
}
LIFECYCLE
)"
log_ok "Ciclo de vida configurado."

log_step "Aplicando etiquetas de projeto"
aws_cmd s3api put-bucket-tagging \
  --bucket "$RAW_BUCKET" \
  --tagging '{"TagSet":[{"Key":"project","Value":"suspicious-transactions"},{"Key":"managed-by","Value":"infrastructure/s3_setup.sh"}]}'
log_ok "Etiquetas aplicadas."

log_step "Estado final do bucket"
aws_cmd s3api head-bucket --bucket "$RAW_BUCKET" && log_ok "Bucket acessível com as credenciais atuais."
log_info "URL do bucket: https://${RAW_BUCKET}.s3.${AWS_REGION}.amazonaws.com"
log_info "Região:       ${AWS_REGION}"
