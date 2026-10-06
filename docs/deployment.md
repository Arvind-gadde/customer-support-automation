# Deploying to Azure

## What gets created

| Resource | Purpose | Dev SKU | Prod SKU |
|---|---|---|---|
| Container Apps env + app | Runs the API | 0.5 vCPU / 1 GiB, 1–3 replicas | 1 vCPU / 2 GiB, 2–10 replicas |
| PostgreSQL Flexible Server 16 | Checkpoints, pgvector KB, semantic cache, usage, feedback | Burstable B1ms | GP D2ds_v5, zone-redundant HA |
| Key Vault (RBAC) | DB URL, API keys, OpenAI key, optional Cohere key | standard | standard + purge protection |
| Container Registry | Images (admin user off) | Basic | Standard |
| Log Analytics + App Insights | Logs, traces, metrics | 30-day retention | 90-day retention |
| User-assigned managed identity | AcrPull, Key Vault Secrets User | | |

Models are called on the **OpenAI API**, outside Azure. No Azure OpenAI resource is created.

## Prerequisites

- Azure CLI (`az login`), `jq`, `openssl`
- **Owner** or **User Access Administrator** + **Contributor** on the subscription or resource group (the template creates role assignments)
- An **OpenAI project API key** (`sk-proj-...`). In the OpenAI dashboard, create a project for this service, set a **monthly budget**, and limit the key to the models you use. Use a separate project and key for evals.
- Optional: a **Cohere API key** to enable reranking.

## 1. Bootstrap (once per environment)

```bash
export OPENAI_API_KEY=sk-proj-...
export COHERE_API_KEY=...        # optional
RG=rg-support-agent-dev LOCATION=centralindia ENV_NAME=dev ./infra/scripts/bootstrap.sh
```

The script runs three phases. A Container App can't be created before its image exists, which is why there are three:

1. Infra without the app (`deployApp=false`)
2. `az acr build` builds the image *inside* ACR (no local Docker needed)
3. Infra with the app (`deployApp=true containerImage=...`)

It prints the generated `CLIENT_API_KEYS` and `STAFF_API_KEYS` **once**. Store them in your password manager.

> Re-running `bootstrap.sh` generates **new** service secrets unless you export `PG_PASSWORD`, `CLIENT_API_KEYS` and `STAFF_API_KEYS` first. Day-to-day deploys use the GitHub workflow, not this script.

## 2. Ingest the knowledge base <a id="ingest"></a>

The app runs migrations on startup, but ingestion is a separate, deliberate step.

```bash
# Temporarily allow your IP on the Postgres firewall
MYIP=$(curl -s https://api.ipify.org)
az postgres flexible-server firewall-rule create -g $RG -n <postgres-name> \
  --rule-name ingest-$USER --start-ip-address $MYIP --end-ip-address $MYIP

export DATABASE_URL="$(az keyvault secret show --vault-name <kv-name> -n database-url --query value -o tsv)"
export OPENAI_API_KEY=sk-proj-...   # embeddings for the KB
make ingest

az postgres flexible-server firewall-rule delete -g $RG -n <postgres-name> --rule-name ingest-$USER --yes
```

Ingestion is idempotent and incremental: unchanged chunks are not re-embedded, and deleted ones are removed. Any change also **wipes the semantic cache**, because cached answers were grounded in the old content. A production setup would run it as a **Container Apps Job** triggered when `data/knowledge_base/**` changes.

## 3. Wire up CI/CD (GitHub Actions with OIDC, no stored secrets)

```bash
APP_ID=$(az ad app create --display-name gh-support-agent --query appId -o tsv)
az ad sp create --id $APP_ID
az ad app federated-credential create --id $APP_ID --parameters '{
  "name": "main", "issuer": "https://token.actions.githubusercontent.com",
  "subject": "repo:<org>/<repo>:environment:production",
  "audiences": ["api://AzureADTokenExchange"] }'

RG_ID=$(az group show -n $RG --query id -o tsv)
az role assignment create --assignee $APP_ID --role Contributor --scope $RG_ID
az role assignment create --assignee $APP_ID --role AcrPush --scope $RG_ID
```

Set these GitHub **repository variables**: `AZURE_CLIENT_ID`, `AZURE_TENANT_ID`, `AZURE_SUBSCRIPTION_ID`, `AZURE_RESOURCE_GROUP`, `ACR_NAME`, `CONTAINER_APP_NAME`. Create a `production` environment in GitHub, and add required reviewers there if you want a manual gate.

For nightly evals, add a GitHub **secret** `OPENAI_EVALS_API_KEY` (a separate, budget-capped project key) in an `evals` environment, and optionally a variable `OPENAI_CHAT_MODEL`.

## 4. Verify

```bash
URL=<appUrl output>
curl -s $URL/health/ready
curl -s $URL/v1/chat -H "X-API-Key: $CLIENT_KEY" -H 'content-type: application/json' \
  -d '{"customer_id":"cust_123","message":"What is the return window?"}'
```

## Going to production (`ENV_NAME=prod`)

- `ENVIRONMENT=production` **refuses to boot** with in-memory integrations. Pass `orderServiceBaseUrl` and `ticketingBaseUrl` as deployment parameters.
- Swagger/OpenAPI is disabled in production.
- Move to private networking (see README, "Deliberately out of scope").
- Set `OPENAI_FALLBACK_MODEL` (e.g. `gpt-4.1`) for throttling resilience on the primary model.
- Check **data residency**: prompts are processed by OpenAI outside your Azure region (ADR 0006).
