# Deploying to Azure

## What gets created

| Resource | Purpose | Dev SKU | Prod SKU |
|---|---|---|---|
| Container Apps env + app | Runs the API | 0.5 vCPU / 1 GiB, 1–3 replicas | 1 vCPU / 2 GiB, 2–10 replicas |
| Azure OpenAI | Chat + embeddings, **key auth disabled** | GlobalStandard | GlobalStandard |
| PostgreSQL Flexible Server 16 | Checkpoints + pgvector | Burstable B1ms | GP D2ds_v5, zone-redundant HA |
| Key Vault (RBAC) | DB URL + API keys | standard | standard + purge protection |
| Container Registry | Images (admin user off) | Basic | Standard |
| Log Analytics + App Insights | Logs, traces, metrics | 30-day retention | 90-day retention |
| User-assigned managed identity | AcrPull, Key Vault Secrets User, OpenAI User | | |

## Prerequisites

- Azure CLI (`az login`), `jq`, `openssl`
- **Owner** or **User Access Administrator** + **Contributor** on the subscription or resource group, because the template creates role assignments
- Model quota: check that `gpt-4.1-mini` and `text-embedding-3-small` are available with **GlobalStandard** in your OpenAI region:
  ```bash
  az cognitiveservices model list -l centralindia \
    --query "[?model.name=='gpt-4.1-mini'].{v:model.version, skus:model.skus[].name}" -o table
  ```
  If they aren't available there, set `OPENAI_LOCATION` (e.g. `swedencentral`, `eastus2`) and keep everything else in India. Note this is a **data-residency decision**: prompts would be processed in that region.

## 1. Bootstrap (once per environment)

```bash
RG=rg-support-agent-dev LOCATION=centralindia ENV_NAME=dev ./infra/scripts/bootstrap.sh
```

The script runs three phases. A Container App can't be created before its image exists, which is why there are three:

1. Infra without the app (`deployApp=false`)
2. `az acr build` builds the image *inside* ACR (no local Docker needed)
3. Infra with the app (`deployApp=true containerImage=...`)

It prints the generated `CLIENT_API_KEYS` and `STAFF_API_KEYS` **once**. Store them in your password manager.

> Re-running `bootstrap.sh` generates **new** secrets unless you export `PG_PASSWORD`, `CLIENT_API_KEYS` and `STAFF_API_KEYS` first. Day-to-day deploys use the GitHub workflow, not this script.

## 2. Ingest the knowledge base <a id="ingest"></a>

The app runs migrations on startup, but ingestion is a separate, deliberate step.

```bash
# Temporarily allow your IP on the Postgres firewall
MYIP=$(curl -s https://api.ipify.org)
az postgres flexible-server firewall-rule create -g $RG -n <postgres-name> \
  --rule-name ingest-$USER --start-ip-address $MYIP --end-ip-address $MYIP

export DATABASE_URL="$(az keyvault secret show --vault-name <kv-name> -n database-url --query value -o tsv)"
export AZURE_OPENAI_ENDPOINT=<openAiEndpoint output>
make ingest     # uses your `az login` identity (bootstrap granted it OpenAI User)

az postgres flexible-server firewall-rule delete -g $RG -n <postgres-name> --rule-name ingest-$USER --yes
```

Ingestion is idempotent and incremental: unchanged chunks are not re-embedded, and deleted ones are removed. A production setup would run it as a **Container Apps Job** triggered when `data/knowledge_base/**` changes.

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

For nightly evals, also add `AZURE_OPENAI_ENDPOINT` and `AZURE_OPENAI_CHAT_DEPLOYMENT`, a federated credential for `environment:evals`, and the **Cognitive Services OpenAI User** role on the OpenAI account.

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
- Set a `AZURE_OPENAI_FALLBACK_DEPLOYMENT` (a second deployment, ideally in another region) for throttling resilience.
