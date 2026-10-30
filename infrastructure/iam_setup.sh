#!/usr/bin/env bash
# Cria o IAM user dedicado de deploy e o perfil correspondente.
#
# Uso: bash infrastructure/iam_setup.sh
#
# A chave de acesso é gravada em ~/.aws/credentials, no perfil [portfolio].
# A senha da chave só aparece uma vez, por isso fica direto no arquivo
# de credenciais (permissão 600) e nunca é impressa no terminal.

set -euo pipefail
source "$(dirname "$0")/env.sh"

log_step "Usuário de deploy: ${DEPLOYER_USER} (perfil [${DEPLOYER_PROFILE}])"

POLICY_FILE="$(dirname "$0")/iam/policies/${DEPLOYER_POLICY}.json"
if [[ ! -f "$POLICY_FILE" ]]; then
  log_err "Política não encontrada: ${POLICY_FILE}"
  exit 1
fi

log_step "Criando/atualizando a política ${DEPLOYER_POLICY}"
aws_iam iam create-policy \
  --policy-name "$DEPLOYER_POLICY" \
  --policy-document "file://${POLICY_FILE}" \
  >/dev/null 2>&1 \
  || aws_iam iam update-policy \
       --policy-name "$DEPLOYER_POLICY" \
       --policy-document "file://${POLICY_FILE}" >/dev/null
log_ok "Política aplicada."

log_step "Criando o usuário ${DEPLOYER_USER}"
if aws_iam iam get-user --user-name "$DEPLOYER_USER" >/dev/null 2>&1; then
  log_info "O usuário já existe."
else
  aws_iam iam create-user --user-name "$DEPLOYER_USER" >/dev/null
  log_ok "Usuário criado."
fi

log_step "Anexando a política ao usuário"
aws_iam iam attach-user-policy \
  --user-name "$DEPLOYER_USER" \
  --policy-arn "arn:aws:iam::${AWS_ACCOUNT_ID}:policy/${DEPLOYER_POLICY}" >/dev/null
log_ok "Política anexada."

log_step "Garantindo que não há console access para o usuário de deploy"
aws_iam iam delete-login-profile --user-name "$DEPLOYER_USER" >/dev/null 2>&1 || true

log_step "Criando a access key"
KEY_DATA="$(aws_iam iam create-access-key --user-name "$DEPLOYER_USER" --output json)"
ACCESS_KEY="$(printf '%s' "$KEY_DATA" | python3 -c 'import json,sys; print(json.load(sys.stdin)["AccessKey"]["AccessKeyId"])')"
SECRET_KEY="$(printf '%s' "$KEY_DATA" | python3 -c 'import json,sys; print(json.load(sys.stdin)["AccessKey"]["SecretAccessKey"])')"

CREDENTIALS_FILE="${HOME}/.aws/credentials"
mkdir -p "${HOME}/.aws"
chmod 700 "${HOME}/.aws"

python3 - "$CREDENTIALS_FILE" "$ACCESS_KEY" "$SECRET_KEY" "$DEPLOYER_PROFILE" "$AWS_REGION" <<'PY'
import os
import re
import sys

path, access, secret, profile, region = sys.argv[1:6]

content = ""
if os.path.exists(path):
    with open(path, encoding="utf-8") as handle:
        content = handle.read()

pattern = re.compile(rf"^\[{re.escape(profile)}\][^\[]*", re.MULTILINE)
content = pattern.sub("", content).rstrip("\n")

section = (
    f"\n[{profile}]\n"
    f"aws_access_key_id = {access}\n"
    f"aws_secret_access_key = {secret}\n"
    f"region = {region}\n"
)

with open(path, "w", encoding="utf-8") as handle:
    handle.write(content + "\n" + section)

os.chmod(path, 0o600)
PY

log_ok "Perfil [${DEPLOYER_PROFILE}] gravado em ${CREDENTIALS_FILE} (permissão 600)."

log_step "Validando o novo perfil"
aws --profile "$DEPLOYER_PROFILE" sts get-caller-identity --output json
