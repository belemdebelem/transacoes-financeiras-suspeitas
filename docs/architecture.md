# Arquitetura

Documento de referência da solução implementada. O objetivo é explicar como os
componentes se conectam, quais contratos cada camada respeita e como o sistema
se comporta em caso de falha.

## Visão geral

O fluxo principal é orientado a eventos: um arquivo JSON com transações é
publicado no S3, o Lambda é invocado, valida e classifica os registros, grava
o resultado no DynamoDB, publica alertas no SNS e registra logs e métricas no
CloudWatch.

```text
Gerador Python
     |
     v
S3 raw-data/  --evento ObjectCreated-->  Lambda
                                           |
                     +---------------------+---------------------+
                     |                     |                     |
                     v                     v                     v
              DynamoDB              SNS (alertas)         CloudWatch
              processed             e-mail                logs + métricas
                     |
                     v
              S3 processed/ (resultado + resumo)
```

O caminho padrão da CLI usa AWS: envia o lote bruto ao S3 e deixa a Lambda
processá-lo. Para desenvolvimento sem AWS, `python main.py --local` usa os
arquivos em `data/raw/` e `data/processed/`.

## Componentes

| Componente | Responsabilidade | Implementação |
|---|---|---|
| Gerador | Criar lotes reproduzíveis com perfis e anomalias | `src/generator/transactions_generator.py` |
| Validação | Verificar contrato, normalizar tipos e isolar inválidos | `src/processing/validation.py` |
| Regras de risco | Calcular score, nível e motivos | `src/rules/risk_rules.py` e `src/rules/rules_config.py` |
| Orquestração | Ler entrada, deduplicar, persistir e publicar | `src/processing/lambda_handler.py` |
| S3 | Armazenar lote bruto, resultado e resumo | `src/aws/s3_storage.py` |
| DynamoDB | Persistir transações processadas | `src/aws/dynamodb.py` |
| SNS | Publicar alertas por e-mail | `src/aws/sns_publisher.py` |
| CloudWatch | Logs, métricas customizadas e alarmes | `src/aws/cloudwatch_metrics.py` |
| Infraestrutura | Criar e atualizar os recursos AWS | `infrastructure/*.sh` |

## Fluxo de processamento

1. O gerador cria um arquivo JSON e o publica em `raw-data/AAA/MM/DD/`.
2. A criação do objeto dispara a Lambda `suspicious-transactions-processor`.
3. O handler identifica o evento, lê o objeto no S3 e grava uma cópia
   temporária em `/tmp`.
4. O lote é validado. Registros inválidos são separados e contabilizados, sem
   interromper o restante do arquivo.
5. Transações com `transaction_id` repetido são deduplicadas; o primeiro
   registro válido prevalece.
6. As regras de negócio calculam `risk_score`, `risk_level`,
   `suspicious_reason` e `alert`.
7. As transações processadas são gravadas no DynamoDB.
8. Transações com `alert=true` são publicadas no tópico SNS.
9. O resultado e o resumo operacional são gravados temporariamente em `/tmp`
   durante a invocação e enviados para `processed/`.
10. Um conjunto de métricas do lote é enviado ao CloudWatch.
11. O handler devolve um JSON com contadores, caminhos e status das integrações.

### Entradas aceitas pelo handler

| Formato | Uso |
|---|---|
| Evento S3 (`Records[].s3`) | Produção, disparado pelo bucket |
| `{"input_path": "..."}` | Execução local e testes |
| `{"local_path": "..."}` | Espelho local de um evento S3 |
| `{"bucket": "...", "key": "..."}` | Invocação direta para um objeto específico |

### Saída do processamento

O retorno inclui, entre outros campos:

- `status`: `success` ou `error`;
- `records_read`, `records_processed`, `records_invalid`,
  `records_duplicated`, `alerts_generated`;
- `dynamodb_written`, `alerts_published`;
- `processed_uploaded`, `summary_uploaded`, `metrics_published`;
- `duration_ms`, `processing_date`, `input_path`, `output_path` e
  `summary_path`.

## Armazenamento

### Layout do S3

```text
raw-data/2026/10/29/transactions_20261029_120000.json
processed/2026/10/29/transactions_20261029_120000_processed.json
processed/2026/10/29/transactions_20261029_120000_processed_summary.json
```

O bucket usa versionamento, criptografia SSE-S3, bloqueio de acesso público,
política que nega HTTP e ciclo de vida para versões antigas. Essas proteções
são aplicadas por `infrastructure/s3_setup.sh`.

### Modelo do DynamoDB

Tabela: `suspicious-transactions-processed`.

| Elemento | Definição |
|---|---|
| Chave primária | `transaction_id` (string) |
| Índice GSI | `account_id-index`: `account_id` (HASH) + `event_date` (RANGE) |
| Capacidade | 5 RCU e 5 WCU provisionadas |
| Escrita | `BatchWriteItem` via `batch_writer`, com sobrescrita por chave |

Campos persistidos: dados originais da transação, `risk_score`, `risk_level`,
`suspicious_reason`, `alert`, `event_date` e `processed_at`. Valores
monetários são convertidos para `Decimal`, evitando problemas de precisão do
DynamoDB.

A escrita é idempotente por `transaction_id`: reprocessar o mesmo lote
sobrescreve o item existente em vez de criar duplicatas.

## SNS

O tópico `suspicious-transactions-alerts` recebe uma mensagem de texto por
transação com `alert=true`. O assunto é limitado a 100 caracteres e inclui
nível e identificador da transação. A mensagem apresenta identificador, conta,
timestamp, valor, nível, score e motivo.

Se `ALERTS_ENABLED=false`, nenhum alerta é publicado, mesmo com o tópico
configurado. Uma falha no SNS é registrada em log e não interrompe o
processamento.

## CloudWatch

### Logs

A Lambda grava linhas JSON estruturadas no log group
`/aws/lambda/suspicious-transactions-processor`. Os eventos principais são:

- `s3_direct_read_ok` e `s3_direct_read_fallback`;
- `dynamodb_written`;
- `sns_alerts_published` e `sns_skipped_alerts_disabled`;
- `s3_processed_uploaded` e `s3_summary_uploaded`;
- `batch_processed` e `batch_failed`;
- eventos de falha de integração.

### Métricas customizadas

Namespace `SuspiciousTransactions`, dimensão
`Service=transaction-processor`:

- `TransactionsRead`, `TransactionsProcessed`, `TransactionsInvalid`,
  `TransactionsDuplicated`;
- `AlertsGenerated`, `AlertRatePercent`, `AlertsPublished`;
- `ProcessingDurationMs`, `DynamoDBItemsWritten`;
- `DynamoDBWriteFailures`, `AlertsPublishFailures`,
  `S3ProcessedUploadFailures`, `S3SummaryUploadFailures`;
- `BatchFailures`.

### Alarmes

Criados por `infrastructure/cloudwatch_setup.sh`, com ação padrão no tópico de
alertas:

| Alarme | Métrica | Condição |
|---|---|---|
| `suspicious-transactions-lambda-errors` | `AWS/Lambda Errors` | >= 1 em 5 min |
| `suspicious-transactions-batch-failures` | `BatchFailures` | >= 1 em 5 min |
| `suspicious-transactions-processing-duration-high` | `ProcessingDurationMs` | >= 30000 ms |
| `suspicious-transactions-dynamodb-write-failures` | `DynamoDBWriteFailures` | >= 1 em 5 min |
| `suspicious-transactions-sns-publish-failures` | `AlertsPublishFailures` | >= 1 em 5 min |
| `suspicious-transactions-s3-processed-upload-failures` | `S3ProcessedUploadFailures` | >= 1 em 5 min |
| `suspicious-transactions-s3-summary-upload-failures` | `S3SummaryUploadFailures` | >= 1 em 5 min |

## Configuração

| Variável | Padrão | Efeito |
|---|---|---|
| `AWS_REGION` | `us-east-2` | Região dos clientes AWS |
| `S3_RAW_BUCKET` | ausente | Habilita leitura e escrita no S3 |
| `S3_RAW_PREFIX` | `raw-data/` | Prefixo dos lotes brutos |
| `S3_PROCESSED_PREFIX` | `processed/` | Prefixo dos resultados |
| `DYNAMODB_TABLE` | ausente | Habilita persistência |
| `SNS_TOPIC_ARN` | ausente | Habilita publicação |
| `ALERTS_ENABLED` | `false` | Autoriza o envio de alertas |
| `METRICS_ENABLED` | `false` | Autoriza o envio de métricas |
| `METRICS_NAMESPACE` | `SuspiciousTransactions` | Namespace no CloudWatch |
| `METRICS_SERVICE` | `transaction-processor` | Valor da dimensão `Service` |

Integrações ausentes são simplesmente ignoradas. Isso permite rodar o projeto
localmente sem credenciais ou recursos AWS.

## Tratamento de falhas

- **Arquivo ausente ou JSON inválido:** retorno `status=error` com código
  específico e métrica `BatchFailures`.
- **Registro inválido:** vai para a lista de inválidos; os demais continuam.
- **Falha no DynamoDB:** registrada em log; o processamento prossegue e a
  diferença aparece em `DynamoDBWriteFailures`.
- **Falha no SNS:** registrada em log; `AlertsPublishFailures` é publicado.
- **Falha no upload S3:** capturada separadamente para resultado e resumo,
  permitindo identificar qual objeto falhou.
- **Falha no CloudWatch:** apenas registra aviso; nunca altera o resultado do
  lote.

## Segurança

- Usuário de deploy separado do root, com política restrita por ARN.
- Role da Lambda com permissões mínimas: leitura de `raw-data/`, escrita em
  `processed/`, escrita na tabela, publicação no tópico e `PutMetricData`.
- Nenhuma credencial no repositório; o perfil `portfolio` fica em
  `~/.aws/credentials`.
- Bucket sem acesso público, com HTTPS obrigatório.
- Dados usados são sintéticos.

## Decisões e limites

- O processamento é batch, não streaming em tempo real.
- A janela de comportamento usa apenas o lote atual; não há histórico externo.
- As regras são determinísticas e explicáveis, não um modelo de machine
  learning.
- Não há dashboard nem notebook; o acompanhamento usa resumo JSON, DynamoDB,
  logs e alarmes.
- A capacidade do DynamoDB é provisionada em nível baixo, adequado ao volume
  didático.

## Operação

```bash
export AWS_PROFILE=portfolio
export AWS_REGION=us-east-2
export AWS_ACCOUNT_ID=123456789012

bash infrastructure/s3_setup.sh
bash infrastructure/dynamodb_setup.sh
bash infrastructure/sns_setup.sh seu-email@example.com
bash infrastructure/lambda_setup.sh
bash infrastructure/cloudwatch_setup.sh
```

Para revalidar o fluxo ponta a ponta:

```bash
bash infrastructure/e2e_test.sh
```

O modo normal envia o lote ao S3 para processamento pela Lambda:

```bash
export AWS_PROFILE=portfolio
export S3_RAW_BUCKET=suspicious-transactions-raw-123456789012
python main.py --transactions 200 --seed 42
```

Para processar localmente, sem AWS:

```bash
python main.py --local --transactions 200 --seed 42
```

## Referências

- `README.md`: visão geral, escopo e instruções.
- `docs/business_rules.md`: regras, scores e classificação.
- `docs/analysis_plan.md`: perguntas, métricas e abordagem de análise.
- `infrastructure/`: scripts de provisionamento e políticas IAM.
