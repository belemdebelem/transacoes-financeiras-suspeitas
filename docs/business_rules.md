# Regras de negócio

Este documento descreve como uma transação é validada, pontuada e classificada.
As regras são determinísticas, aditivas e mantidas em
`src/rules/rules_config.py`; a lógica que as aplica está em
`src/rules/risk_rules.py`.

## Objetivo

Identificar transações com indícios de risco de forma simples, explicável e
reproduzível, atribuindo um score de 0 a 100, um nível de risco e uma lista de
motivos. Não é um sistema antifraude de produção nem um modelo estatístico.

## Entrada considerada

Antes das regras, o lote passa por:

1. **Validação de contrato** (`src/processing/validation.py`): campos
   obrigatórios, tipos, valores permitidos, normalização de timestamp, moeda,
   canal e valor.
2. **Deduplicação**: registros com `transaction_id` repetido são descartados,
   mantendo o primeiro válido.
3. **Aplicação das regras** sobre os registros únicos e válidos.

Campos usados pelas regras:

- `transaction_id`
- `timestamp`
- `account_id`
- `amount`
- `channel`
- `location`

`transaction_type`, `customer_id`, `currency` e `merchant_or_counterparty`
fazem parte do contrato, mas não alteram o score na versão atual.

## Configuração

Todos os limites ficam em `RuleConfig`:

| Parâmetro | Padrão | Uso |
|---|---:|---|
| `high_amount_threshold` | 10000.0 | Limite da regra de valor alto |
| `high_amount_score` | 50 | Pontos por valor alto |
| `atm_amount_threshold` | 5000.0 | Limite da regra de ATM |
| `atm_score` | 20 | Pontos por ATM de valor elevado |
| `burst_window_seconds` | 600 | Janela deslizante de acumulação |
| `burst_min_transactions` | 3 | Mínimo de transações na janela |
| `burst_amount_threshold` | 8000.0 | Soma mínima na janela |
| `burst_score` | 50 | Pontos por acumulação suspeita |
| `unusual_amount_threshold` | 5000.0 | Valor mínimo para canal/local incomum |
| `unusual_channel_share` | 0.30 | Participação máxima do canal incomum |
| `unusual_location_share` | 0.30 | Participação máxima do local incomum |
| `unusual_channel_score` | 20 | Pontos por canal incomum |
| `unusual_location_score` | 20 | Pontos por local incomum |
| `min_baseline_samples` | 6 | Histórico mínimo para avaliar padrão |
| `dominance_threshold` | 0.50 | Dominância mínima do canal/local principal |
| `combination_score` | 15 | Pontos por combinação severa |
| `alert_score` | 50 | Score mínimo para gerar alerta |
| `max_score` | 100 | Teto do score |

## Regras

### 1. Valor alto (`HIGH_AMOUNT`)

Condição:

```text
amount > 10000
```

Efeito:

```text
score += 50
suspicious_reason += "HIGH_AMOUNT"
```

Exemplo: uma compra de `18500.00` recebe 50 pontos e atinge o limite de
alerta, resultando em `SUSPECT`.

### 2. Acumulação em curto intervalo (`MULTIPLE_TRANSACTIONS_WINDOW`)

A regra é aplicada por conta, com uma janela deslizante que termina na
transação atual:

```text
[t - 600 segundos, t]
```

Condição:

```text
quantidade de transações na janela >= 3
E soma dos valores na janela > 8000
```

Efeito:

```text
score += 50 em todas as transações da janela
suspicious_reason += "MULTIPLE_TRANSACTIONS_WINDOW"
```

A janela considera apenas o passado, nunca transações futuras. O histórico
disponível é o próprio lote processado.

Exemplo: três transações da mesma conta em 8 minutos somando `9000.00` geram
50 pontos para as três, classificando-as como `SUSPECT`.

### 3. Valor alto em ATM (`ATM_HIGH_AMOUNT`)

Condição:

```text
channel == "ATM"
E amount >= 5000
```

Efeito:

```text
score += 20
suspicious_reason += "ATM_HIGH_AMOUNT"
```

Um saque de `6000.00` em ATM recebe 20 pontos e fica em `ATTENTION`, sem
alerta isolado. O alerta depende de outras regras somadas.

O canal ATM é excluído da regra de canal incomum para não contar o mesmo
fenômeno duas vezes.

### 4. Canal incomum (`UNUSUAL_CHANNEL`)

O perfil da conta é construído com as transações do próprio lote.

Condições:

```text
amount >= 5000
E channel != "ATM"
E a conta tem pelo menos 6 transações no lote
E o canal mais usado representa >= 50% do histórico da conta
E o canal atual representa < 30% do histórico da conta
```

Efeito:

```text
score += 20
suspicious_reason += "UNUSUAL_CHANNEL"
```

Exemplo: uma conta usa `APP` em 90% das transações e realiza uma operação de
`7000.00` em `WEB`. O canal é incomum, mas os 20 pontos isolados não geram
alerta.

### 5. Local incomum (`UNUSUAL_LOCATION`)

Mesma lógica de perfil, aplicada a `location`:

```text
amount >= 5000
E a conta tem pelo menos 6 transações no lote
E o local mais usado representa >= 50% do histórico da conta
E o local atual representa < 30% do histórico da conta
```

Efeito:

```text
score += 20
suspicious_reason += "UNUSUAL_LOCATION"
```

Local vazio ou ausente não participa da regra.

### 6. Combinação severa (`CRITICAL_COMBINATION`)

Condição:

```text
valor alto
E (ATM de valor elevado OU canal incomum)
E (acumulação suspeita OU local incomum)
```

Efeito:

```text
score += 15
suspicious_reason += "CRITICAL_COMBINATION"
```

Essa regra não substitui as anteriores; ela soma um adicional quando fatores
graves aparecem juntos.

## Cálculo do score

O score é a soma dos pontos de todas as regras aplicáveis, limitado ao
intervalo de 0 a 100:

```text
score = min(100, max(0, soma_dos_pontos))
```

As regras são independentes e podem se acumular. A ordem de avaliação não
altera o resultado.

## Classificação

| Nível | Faixa | Alerta | Interpretação |
|---|---|---:|---|
| `NORMAL` | 0 a 19 | Não | Sem indícios relevantes |
| `ATTENTION` | 20 a 49 | Não | Comportamento fora do padrão, ainda plausível |
| `SUSPECT` | 50 a 74 | Sim | Pelo menos uma condição crítica |
| `CRITICAL` | 75 a 100 | Sim | Combinação severa ou múltiplas regras |

O alerta é emitido quando `score >= 50`. O nível é derivado do score por
`RuleConfig.risk_level_for`.

## Exemplos resolvidos

### Exemplo A: valor alto

```text
amount = 18500
canal = APP
local = local habitual
```

- `HIGH_AMOUNT`: +50
- Score final: 50
- Nível: `SUSPECT`
- Alerta: sim

### Exemplo B: acumulação

```text
3 transações da mesma conta em 8 minutos
soma = 9000
```

- `MULTIPLE_TRANSACTIONS_WINDOW`: +50 em cada
- Score final: 50 em cada
- Nível: `SUSPECT`
- Alerta: sim

### Exemplo C: ATM isolado

```text
amount = 6000
canal = ATM
```

- `ATM_HIGH_AMOUNT`: +20
- Score final: 20
- Nível: `ATTENTION`
- Alerta: não

### Exemplo D: combinação

```text
amount = 20000
canal = ATM
local incomum
3 transações próximas com soma > 8000
```

- `HIGH_AMOUNT`: +50
- `ATM_HIGH_AMOUNT`: +20
- `UNUSUAL_LOCATION`: +20
- `MULTIPLE_TRANSACTIONS_WINDOW`: +50
- `CRITICAL_COMBINATION`: +15
- Soma: 155, limitada a 100
- Nível: `CRITICAL`
- Alerta: sim

## Saída

Cada transação processada recebe:

```json
{
  "risk_score": 50,
  "risk_level": "SUSPECT",
  "suspicious_reason": "HIGH_AMOUNT",
  "alert": true
}
```

Quando há mais de um motivo, `suspicious_reason` usa `"; "` como separador.
Transações sem regra aplicável recebem `suspicious_reason: null`.

## Limitações e evoluções

- O baseline é calculado apenas com o lote atual; contas com pouco histórico
  podem não ativar as regras de canal/local.
- Não há análise de série temporal entre lotes.
- Os limites são fixos por configuração, sem aprendizado automático.
- A regra de acumulação pode sinalizar transações legítimas de alto volume.
- Ajustes de negócio devem ser feitos em `RuleConfig`, com testes que cubram o
  novo comportamento.

## Referências

- `src/rules/risk_rules.py`
- `src/rules/rules_config.py`
- `tests/test_risk_rules.py`
- `docs/analysis_plan.md`
