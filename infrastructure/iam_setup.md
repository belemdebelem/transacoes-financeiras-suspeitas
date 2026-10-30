# Etapa 0 - IAM user dedicado de deploy

Usuário: `portfolio-deployer` · Perfil local: `[portfolio]`
Política gerenciada: `PortfolioDeployerPolicy`

O objetivo aqui é separar a credencial de trabalho da credencial de deploy.
Usar o usuário root da conta para tudo significa que qualquer script rodado
— inclusive um com bug — opera com permissão irrestrita sobre a conta inteira.

## Por que isso importa no contexto do portfólio

| | Usuário root | IAM user dedicado |
|---|---|---|
| Permissões | Tudo, sem limite | Só os recursos do projeto |
| Rastreabilidade | Tudo aparece como "root" | Aparece como `portfolio-deployer` |
| Auditoria | Sem responsável identificável | Histórico por usuário |
| Exposição | Chave usada em toda máquina e em todo script | Chave isolada em um perfil |
| Rotação | Diffícil, quebra tudo | Rotacionar é barato |

O `--profile portfolio` no comando é o que mantém as duas credenciais
separadas: o `default` continua sendo o root, mas os scripts de infraestrutura
e os comandos de deploy podem apontar para a credencial restrita.

## Caminho rápido (CLI)

```bash
bash infrastructure/iam_setup.sh
```

O script:

1. cria (ou atualiza) a política `PortfolioDeployerPolicy`;
2. cria o usuário `portfolio-deployer`, sem acesso ao console;
3. anexa a política;
4. cria uma access key e grava no perfil `[portfolio]` do `~/.aws/credentials`
   com permissão `600`.

A senha da chave aparece uma única vez na criação do IAM. Por isso o script
escreve direto no arquivo de credenciais em vez de imprimir no terminal: assim
ela não fica no histórico do shell nem na saída do script.

## Caminho pelo console

**1. Criar o usuário.** Console → IAM → *Users* → *Create user*.
User name: `portfolio-deployer`. Em *Permissions set*, escolha
*Attach policies directly* e avance sem selecionar nada (a política é criada
no passo 3).

**2. Não dar acesso ao console.** Na tela de criação do usuário, a opção
*Enable console access* deve ficar **desmarcada**. Um usuário de automação
não tem senha nem precisa de tela.

**3. Criar a política.** IAM → *Policies* → *Create policy* → *JSON*.

Use o conteúdo de
`infrastructure/iam/policies/PortfolioDeployerPolicy.json`, que é a versão
atualizada da política. Ela cobre S3, DynamoDB, SNS, Lambda, o papel de
execução da Lambda e a leitura dos logs do CloudWatch, sempre restrita aos
recursos do projeto. O script `infrastructure/iam_setup.sh` aplica esse mesmo
arquivo na conta.

O `Resource` restrito por ARN é o ponto: mesmo que a chave vaze, o alcance
dela fica limitado aos recursos do projeto.

**4. Anexar a política.** IAM → *Users* → `portfolio-deployer` → *Permissions* →
*Attach policies* → `PortfolioDeployerPolicy`.

**5. Criar a access key.** IAM → *Users* → `portfolio-deployer` → *Security
credentials* → *Create access key*. Marque **Command line access** (o console
recusa programático access por padrão) e salve o arquivo `.csv`.

**6. Gravar o perfil.** O `.csv` tem a chave e o segredo. Grave no
`~/.aws/credentials`:

```ini
[portfolio]
aws_access_key_id = AKIA...
aws_secret_access_key = ...
region = us-east-2
```

```bash
chmod 600 ~/.aws/credentials
```

**7. Ativar MFA no root.** IAM → *Security credentials* do usuário root →
*Enable MFA*. Use um app autenticador, não SMS. A conta do portfólio não pode
ficar sem segundo fator.

## Verificação

```bash
aws --profile portfolio sts get-caller-identity
```

Deve retornar o ARN `arn:aws:iam::123456789012:user/portfolio-deployer`.

Teste do escopo: as operações fora dele precisam falhar.

```bash
# deve falhar: não está no escopo da política
aws --profile portfolio dynamodb create-table --table-name teste \
  --attribute-definitions AttributeName=id,AttributeType=S \
  --key-schema AttributeName=id,KeyType=HASH --billing-mode PAY_PER_REQUEST
```

## Detalhe que economiza tempo

Access key recém-criada pode levar alguns segundos para ser aceita pela API.
O `sts get-caller-identity` logo após a criação retorna
`InvalidClientTokenId` mesmo com a chave correta. É propagação do IAM, não
erro de configuração — basta repetir o comando alguns segundos depois.

## Expandir a política

O arquivo versionado já inclui as permissões atuais de S3, DynamoDB, SNS,
Lambda e CloudWatch. À medida que novos recursos entrarem, atualize a política
com as permissões específicas deles e reexecute:

```bash
bash infrastructure/iam_setup.sh
```

Continuar com `AdministratorAccess` seria mais rápido e eliminaria qualquer
chance de bloqueio indevido. Mas entrega um resultado que não demonstra
least privilege, que é justamente o que um avaliador procura ver.
