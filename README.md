# Detecção de transações financeiras suspeitas

Projeto de portfólio que combina análise de dados, regras de negócio em Python e serviços AWS para processar transações sintéticas e destacar padrões que merecem análise.

## Sobre o projeto

A solução gera lotes fictícios de transações, valida os registros e aplica regras explicáveis para calcular um score de risco. O resultado inclui nível de risco, motivos associados e um resumo operacional com contagens e distribuição dos alertas.

O projeto demonstra um fluxo de dados em lote e, opcionalmente, uma arquitetura orientada a eventos na AWS: o envio de um arquivo JSON ao Amazon S3 pode acionar uma função AWS Lambda para processá-lo. Os dados são sintéticos; as regras são didáticas e não comprovam fraude nem substituem a análise de uma instituição financeira.

## O que este projeto demonstra

- Geração, validação e análise de dados com Python.
- Regras de negócio explicáveis e classificação por score de risco.
- Métricas descritivas: volume processado, alertas, motivos e contas mais sinalizadas.
- Persistência de arquivos no Amazon S3 e consulta de registros no DynamoDB.
- Publicação opcional de notificações pelo Amazon SNS.
- Observabilidade com logs, métricas e alarmes no Amazon CloudWatch.

## Fluxo de dados

1. O gerador cria um lote fictício de transações em JSON.
2. O processamento valida os registros, remove duplicatas e aplica as regras de risco.
3. A solução calcula um resumo do lote e grava os arquivos processados.
4. No fluxo AWS, o arquivo bruto é enviado ao S3. Um evento de criação no prefixo configurado pode iniciar a Lambda.
5. Quando configuradas, as integrações persistem registros no DynamoDB, publicam alertas no SNS e enviam métricas ao CloudWatch.

## Regras de risco

As regras e seus parâmetros estão em `src/rules/`. Entre os exemplos estão valor alto, uso de ATM com valor elevado, transações acumuladas em uma janela de tempo e canal ou local incomum em relação ao histórico do lote. Os scores são combinados e limitados a 100.

| Nível | Score | Interpretação no projeto |
|---|---:|---|
| NORMAL | 0–19 | Sem regra de alerta acionada |
| ATTENTION | 20–49 | Padrão atípico para acompanhamento |
| SUSPECT | 50–74 | Regra de alerta acionada |
| CRITICAL | 75–100 | Pontuação elevada por combinação de regras |

As classificações são indicadores para fins de demonstração, não conclusões sobre atividade criminosa.

## Tecnologias

- Python 3.11 ou superior
- boto3 para integrações AWS
- Amazon S3, AWS Lambda, Amazon DynamoDB, Amazon SNS e Amazon CloudWatch (modo AWS)
- pytest para os testes automatizados

## Executar localmente

Clone o repositório, crie um ambiente virtual e instale as dependências:

```bash
git clone https://github.com/belemdebelem/transacoes-financeiras-suspeitas.git
cd transacoes-financeiras-suspeitas
python -m venv .venv
source .venv/bin/activate  # Windows: .venv\Scripts\activate
pip install -r requirements.txt
```

Gere e processe um lote local, sem conectar à AWS:

```bash
python main.py --local --transactions 200 --seed 42
```

O comando mostra a quantidade processada, a distribuição por nível de risco, os motivos de alerta e as contas mais sinalizadas. Os arquivos JSON são gravados em `data/raw/` e `data/processed/`; essa pasta é ignorada pelo Git porque contém saídas geradas localmente.

Para gerar um lote diferente, altere `--transactions` e `--seed`:

```bash
python main.py --local --transactions 500 --seed 7
```

## Executar na AWS (opcional)

O modo AWS envia o lote bruto para um bucket S3 e depende de recursos previamente configurados — incluindo o gatilho S3 da Lambda e as permissões IAM correspondentes. Configure as credenciais AWS fora do repositório, por um perfil local ou outro método seguro recomendado pela AWS. Não salve chaves, arquivos de credenciais, ARNs privados ou identificadores da sua conta neste projeto.

Configure o bucket de destino e a região no ambiente e execute:

```bash
export AWS_REGION=us-east-2
export S3_RAW_BUCKET=nome-do-seu-bucket
python main.py --transactions 200 --seed 42
```

Também é possível passar o bucket diretamente com `--bucket`. Sem `--local`, o programa usa AWS e precisa de um bucket configurado. Os scripts de provisionamento em `infrastructure/` são referências para uma conta própria: revise nomes, região, permissões e custos antes de executá-los. O arquivo de política IAM é um modelo e deve ser adaptado à conta e aos recursos de quem o utilizar.

## Estrutura do projeto

```text
src/
  generator/      geração dos dados sintéticos
  processing/     validação, deduplicação e orquestração do processamento
  rules/          regras e classificação de risco
  monitoring/     resumo operacional do lote
  aws/            integrações com S3, DynamoDB, SNS e CloudWatch
infrastructure/   scripts e modelos para provisionamento AWS
docs/             arquitetura, regras e plano de análise
tests/            testes automatizados
main.py           interface de linha de comando
```

## Métricas e resultados

O resumo operacional inclui total de transações, registros inválidos, alertas, taxa de alertas, níveis de risco, motivos identificados e contas com mais alertas. No fluxo AWS, as métricas customizadas são publicadas no namespace `SuspiciousTransactions` quando a configuração de métricas está habilitada. Os arquivos completos de entrada e saída ficam no S3; o CloudWatch registra a execução e os indicadores operacionais.

## Evidências visuais

Os exemplos abaixo mostram saídas com dados sintéticos e telas do ambiente demonstrativo:

**Exemplo de transação processada:**

![Transação sintética enriquecida com score, nível de risco e motivo](docs/images/processed-transaction-example.png)

**Itens processados no DynamoDB:**

![Itens sintéticos armazenados na tabela DynamoDB](docs/images/dynamodb-processed-items.png)

**Fluxos de execução da Lambda no CloudWatch Logs:**

![Lista de fluxos de log da função Lambda](docs/images/lambda-log-streams.png)

**Visão geral dos alarmes do CloudWatch:**

![Alarmes do CloudWatch em estado OK no período exibido](docs/images/cloudwatch-alarms.png)

As capturas ilustram uma execução do ambiente de demonstração; os horários e estados refletem aquele momento e podem mudar em outras execuções.

## Testes

Para executar a suíte automatizada:

```bash
pytest
```

## Limitações

- Os dados são gerados artificialmente e não representam clientes ou operações reais.
- As regras usam limiares fixos e um histórico limitado ao lote; não são um modelo estatístico ou sistema de produção.
- Uma sinalização indica que uma regra foi acionada e requer análise humana.
- O fluxo AWS exige recursos, credenciais e permissões configurados pelo usuário.

## Autoria

Projeto desenvolvido por **Belém Ribeiro** como portfólio de análise de dados, com foco em Python, análise exploratória, regras de negócio e fundamentos de engenharia de dados na AWS.
