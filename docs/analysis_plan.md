# Plano de análise

Este documento define as perguntas que o projeto deve responder, os dados
disponíveis para respondê-las e como validar que o processamento e os alertas
estão coerentes. O foco é acompanhamento operacional e revisão dos eventos
sinalizados, não análise exploratória genérica.

## Objetivo

Permitir que uma pessoa consiga:

- verificar se o fluxo processou os lotes esperados;
- entender quais transações foram sinalizadas e por quê;
- comparar volume, alertas e falhas ao longo do tempo;
- identificar contas, períodos ou regras com concentração de alertas;
- distinguir falha de processamento de comportamento de risco.

## Perguntas de acompanhamento

### Operação

1. Quantos lotes foram processados em um período?
2. Quantas transações foram lidas, processadas, invalidadas e deduplicadas?
3. Houve falha de Lambda, DynamoDB, SNS ou upload no S3?
4. Qual foi a duração de cada execução?
5. O volume de alertas publicados corresponde ao volume gerado?

### Risco

6. Quais transações foram sinalizadas e por qual regra?
7. Quantos alertas foram gerados por data UTC (`YYYY-MM-DD`)?
8. Quais são os níveis de risco predominantes?
9. Quais contas concentram mais alertas?
10. Qual regra gera mais alertas?
11. Qual o valor total associado às transações alertadas?

### Qualidade

12. A taxa de registros inválidos está dentro do esperado?
13. Há duplicidade relevante nos lotes?
14. Existem divergências entre gerado, processado e publicado?
15. Os limites das regras continuam adequados ao volume observado?

## Fontes de dados

| Fonte | Conteúdo | Uso |
|---|---|---|
| S3 `processed/` | Resultado completo e resumo JSON por lote | Revisão detalhada e auditoria |
| DynamoDB | Transações processadas com score, nível e motivo | Consulta por transação ou conta |
| CloudWatch Logs | Eventos `batch_processed`, `batch_failed` e integrações | Diagnóstico e rastreabilidade |
| CloudWatch Metrics | Contadores e duração por lote | Acompanhamento operacional |
| SNS | Mensagens de alerta | Notificação e conferência |

### Resumo operacional

Cada lote gera `<arquivo>_summary.json` com:

- `total_transactions`;
- `alert_count` e `alert_rate_percent`;
- `alerted_amount`;
- `risk_levels`;
- `alert_reasons`;
- `top_alert_rule`;
- `alerts_by_account`;
- `alerts_by_period`;
- `records_invalid`.

Esse arquivo é a principal fonte para responder às perguntas de risco sem
consultar registro por registro.

### Métricas no CloudWatch

Namespace `SuspiciousTransactions`, dimensão
`Service=transaction-processor`:

| Métrica | Pergunta relacionada |
|---|---|
| `TransactionsRead` | Volume recebido |
| `TransactionsProcessed` | Volume efetivamente classificado |
| `TransactionsInvalid` | Qualidade da entrada |
| `TransactionsDuplicated` | Duplicidade no lote |
| `AlertsGenerated` | Volume de sinalizações |
| `AlertRatePercent` | Proporção de alertas |
| `AlertsPublished` | Entrega no SNS |
| `ProcessingDurationMs` | Custo operacional da execução |
| `DynamoDBItemsWritten` | Persistência |
| `DynamoDBWriteFailures` | Divergência de persistência |
| `AlertsPublishFailures` | Divergência de publicação |
| `S3ProcessedUploadFailures` | Falha no resultado |
| `S3SummaryUploadFailures` | Falha no resumo |
| `BatchFailures` | Erros que impediram o lote |

## Abordagem de análise

### 1. Verificação de consistência do lote

Comparar, para cada execução:

```text
records_read = records_invalid + records_duplicated + records_processed
alerts_published <= alerts_generated
dynamodb_written <= records_processed
```

As diferenças devem ser explicadas por falhas registradas nos logs ou pelas
métricas de falha.

### 2. Distribuição de risco

Usar `risk_levels` e `alert_rate_percent` para responder:

- o lote tem comportamento parecido com os anteriores?
- há aumento abrupto de `SUSPECT` ou `CRITICAL`?
- o volume de `ATTENTION` está coerente com os limites configurados?

### 3. Motivos de alerta

Usar `alert_reasons` e `top_alert_rule` para identificar qual regra mais
contribuiu. Uma regra dominante pode indicar:

- perfil de dados adequado ao cenário;
- limite muito sensível;
- necessidade de revisar a configuração.

### 4. Concentração por conta e período

`alerts_by_account` e `alerts_by_period` mostram onde os alertas se concentram.
`alerts_by_period` agrupa por data UTC (`YYYY-MM-DD`), não por hora. A leitura
deve considerar o volume de transações da conta e os dias cobertos pelo lote,
não apenas a contagem absoluta.

### 5. Revisão individual

Para os casos prioritários (`CRITICAL` e `SUSPECT`), consultar no DynamoDB:

- dados originais da transação;
- `risk_score` e `risk_level`;
- `suspicious_reason`;
- `processed_at`.

O motivo permite reconstruir a decisão sem reexecutar o lote.

### 6. Diagnóstico de falhas

Quando um alarme disparar:

1. consultar os logs da execução pelo `batch_failed` ou pelo identificador do
   lote;
2. verificar a métrica de falha correspondente;
3. conferir o objeto em `processed/` e o item no DynamoDB;
4. confirmar a assinatura e a entrega no SNS;
5. reprocessar o lote após corrigir a causa, se necessário.

O reprocessamento é seguro para o DynamoDB porque a escrita é idempotente por
`transaction_id`. O SNS, porém, pode emitir alertas novamente.

## Consultas úteis

### Últimos objetos processados

```bash
aws s3 ls s3://suspicious-transactions-raw-123456789012/processed/ --recursive
```

### Resumo de um lote

```bash
aws s3 cp \
  s3://suspicious-transactions-raw-123456789012/processed/2026/10/29/lote_processed_summary.json \
  - | python3 -m json.tool
```

### Transação por identificador

```bash
aws dynamodb get-item \
  --table-name suspicious-transactions-processed \
  --key '{"transaction_id": {"S": "txn_000001"}}'
```

### Transações de uma conta

```bash
aws dynamodb query \
  --table-name suspicious-transactions-processed \
  --index-name account_id-index \
  --key-condition-expression "account_id = :account" \
  --expression-attribute-values '{":account": {"S": "ACC_0001"}}'
```

### Métricas de um período

```bash
aws cloudwatch get-metric-statistics \
  --namespace SuspiciousTransactions \
  --metric-name AlertsGenerated \
  --dimensions Name=Service,Value=transaction-processor \
  --start-time 2026-10-29T00:00:00Z \
  --end-time 2026-10-29T23:59:59Z \
  --period 300 \
  --statistics Sum
```

### Estado dos alarmes

```bash
aws cloudwatch describe-alarms \
  --alarm-name-prefix suspicious-transactions-
```

### Logs de um lote

```bash
aws logs filter-log-events \
  --log-group-name /aws/lambda/suspicious-transactions-processor \
  --filter-pattern '"batch_processed"'
```

## Critérios de validação

Um lote é considerado consistente quando:

- o status é `success`;
- `records_read` corresponde ao tamanho do arquivo de entrada;
- `records_invalid` e `records_duplicated` são explicáveis;
- `alerts_generated` é igual à contagem de `alert=true`;
- `dynamodb_written` é igual a `records_processed` quando o DynamoDB está
  configurado;
- `alerts_published` é igual a `alerts_generated` quando o SNS está habilitado;
- `processed_uploaded` e `summary_uploaded` são verdadeiros no ambiente AWS;
- `metrics_published` é verdadeiro no ambiente AWS;
- os alarmes permanecem em `OK` sem causas conhecidas.

## Limitações

- O baseline de comportamento é local ao lote; contas com histórico fora do
  lote não são comparadas.
- Não há análise entre lotes por conta; o resumo agrega alertas por data UTC,
  sem granularidade horária.
- A taxa de alerta depende dos limiares configurados e do gerador sintético;
  não representa uma taxa real de fraude.
- O projeto não inclui dashboard, notebook ou ferramenta de BI.
- A revisão é manual e baseada nos motivos registrados.

## Evoluções possíveis

- consultas agregadas por regra e por conta em uma ferramenta de BI;
- histórico de scores por conta entre lotes;
- comparação entre versões de `RuleConfig`;
- relatório periódico de qualidade dos dados;
- painel operacional no CloudWatch.

## Referências

- `docs/architecture.md`
- `docs/business_rules.md`
- `src/monitoring/operational_metrics.py`
- `src/aws/cloudwatch_metrics.py`
- `infrastructure/cloudwatch_setup.sh`
