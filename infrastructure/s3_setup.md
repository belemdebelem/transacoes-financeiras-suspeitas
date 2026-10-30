# Etapa 1 - Bucket S3 de dados brutos

Bucket: `suspicious-transactions-raw-123456789012` (região `us-east-2`)

O bucket guarda os lotes brutos que o Lambda processa e, no prefixo `processed/`,
o resultado de cada processamento.

```
raw-data/2026/10/21/transactions_20261021_195325.json
processed/2026/10/21/transactions_20261021_195325_processed.json
processed/2026/10/21/transactions_20261021_195325_processed_summary.json
```

## Caminho rápido (CLI)

```bash
bash infrastructure/s3_setup.sh
```

O script é idempotente: pode rodar quantas vezes quiser. Ele cria o bucket (se
não existir) e aplica todas as proteções abaixo.

Remover o bucket e todo o conteúdo:

```bash
bash infrastructure/s3_teardown.sh --dry-run   # só mostra o que seria apagado
bash infrastructure/s3_teardown.sh
```

## Caminho pelo console

Se você quiser fazer cada passo na mão, o equivalente no console é este.

**1. Criar o bucket.** Console → S3 → *Create bucket*.
Nome: `suspicious-transactions-raw-123456789012`. Região: *US East (Ohio)*
(`us-east-2`). Deixe a opção de versionamento desmarcada por enquanto, o script
aplica logo em seguida.

**2. Block Public Access.** Na tela de criação, o painel *Object ownership* e
*Block public access* aparecem no canto inferior. Marque as quatro opções:

| Opção | Valor |
|---|---|
| Block all public access | ligado |
| Block public access to buckets and objects granted through new access control lists (ACLs) | ligado |
| Block public access to buckets and objects granted through any access control list (ACLs) | ligado |
| Block public access to buckets and objects granted through new public bucket or access point policies | ligado |

Pelo CLI, o equivalente é:

```bash
aws s3api put-public-access-block --bucket suspicious-transactions-raw-123456789012 \
  --public-access-block-configuration \
  "BlockPublicAcls=true,IgnorePublicAcls=true,BlockPublicPolicy=true,RestrictPublicBuckets=true"
```

**3. Versionamento.** Aba *Object management* → *Versioning* → *Enable*.

**4. Criptografia padrão.** Aba *Object management* → *Default encryption* →
*Enable*. Em *Encryption type* escolha **SSE-S3** (*Server-side encryption with
Amazon S3-managed keys*). É o suficiente: o S3 cifra cada objeto com chaves
gerenciadas pela AWS, sem custo adicional.

**5. Negar tráfego sem HTTPS.** Aba *Permissions* → *Bucket policy* → *Policy*.
Cole:

```json
{
  "Version": "2012-10-17",
  "Statement": [
    {
      "Sid": "DenyInsecureTransport",
      "Effect": "Deny",
      "Principal": "*",
      "Action": "s3:*",
      "Resource": [
        "arn:aws:s3:::suspicious-transactions-raw-123456789012",
        "arn:aws:s3:::suspicious-transactions-raw-123456789012/*"
      ],
      "Condition": { "Bool": { "aws:SecureTransport": "false" } }
    }
  ]
}
```

O efeito é bloquear qualquer requisição feita por `http://`. Em redes internas
ou proxies mal configurados isso aparece como `AccessDenied` e não como erro de
rede, o que costuma confundir.

**6. Ciclo de vida.** Aba *Object management* → *Lifecycle rules* → *Create
rule*. Nome `expire-noncurrent-versions`. Regra: *Noncurrent object versions*,
expirar após **30 dias**. Em *Delete expired object versions* marque *Delete
expired object versions*. Isso evita que o versionamento acumule cópias
antigas para sempre, já que cada gravação de um arquivo novo cria uma versão.

**7. Etiquetas.** Aba *Object management* → *Tags*. Adicione `project =
suspicious-transactions` e `managed-by = infrastructure/s3_setup.sh`. Etiquetas
custam nada e ajudam a filtrar recursos na conta.

## Por que cada proteção importa

| Proteção | O que ela evita |
|---|---|
| Block Public Access | O bucket inteiro ou parte dele ficar legível por qualquer pessoa na internet. É o erro de configuração mais comum em S3 e o que gera vazamentos de dados. |
| Versioning | Um `upload` errado sobrescreve o arquivo sem volta. Com versionamento, a versão anterior continua acessível. |
| SSE-S3 | Os dados ficam cifrados em repouso sem você precisar gerenciar chave. |
| Deny HTTP | Dados em trânsito sem TLS podem ser lidos na rede. |
| Lifecycle | Versões antigas não serem removidas, o custo cresce sem ninguém perceber. |

## Verificação

```bash
aws s3api head-bucket --bucket suspicious-transactions-raw-123456789012
aws s3 ls s3://suspicious-transactions-raw-123456789012/ --recursive
aws s3api get-public-access-block --bucket suspicious-transactions-raw-123456789012
aws s3api get-bucket-versioning --bucket suspicious-transactions-raw-123456789012
aws s3api get-bucket-encryption --bucket suspicious-transactions-raw-123456789012
```

Para publicar um lote de teste usando o projeto:

```bash
export AWS_PROFILE=portfolio
export S3_RAW_BUCKET=suspicious-transactions-raw-123456789012
python main.py --transactions 50 --seed 42
```

## Custo

S3 cobra por GB armazenado e por requisição. O free tier cobre 5 GB, 20.000
requisições de leitura e 10.000 de escrita por mês. O volume deste projeto
ficaria dentro disso sem custo. Ainda assim, o `s3_teardown.sh` existe para
encerrar a experiência e zerar a conta.

O detalhe que costuma passar batido: buckets fora de `us-east-1` exigem
`--create-bucket-configuration LocationConstraint=<região>`. Sem isso a API
responde `IllegalLocationConstraintException`.
