#!/usr/bin/env bash
# =============================================================================
# One-time environment bootstrap. After this, CI/CD (.github/workflows/deploy.yml)
# ships every change. Re-running is safe for infra, BUT it generates new secrets:
# pass existing ones via env vars (PG_PASSWORD, CLIENT_API_KEYS, STAFF_API_KEYS)
# if the environment already exists.
#
#   export OPENAI_API_KEY=sk-proj-...        # required
#   export COHERE_API_KEY=...                # optional: enables the reranker
#   RG=rg-support-agent-dev LOCATION=centralindia ./infra/scripts/bootstrap.sh
# =============================================================================
set -euo pipefail

: "${RG:?Set RG (resource group name)}"
: "${OPENAI_API_KEY:?Set OPENAI_API_KEY (a project-scoped key with a budget limit)}"
LOCATION="${LOCATION:-centralindia}"
COHERE_API_KEY="${COHERE_API_KEY:-}"
ENV_NAME="${ENV_NAME:-dev}"
ROOT="$(cd "$(dirname "$0")/../.." && pwd)"

gen() { openssl rand -base64 48 | tr -dc 'A-Za-z0-9' | head -c "$1"; }
PG_PASSWORD="${PG_PASSWORD:-$(gen 32)}"
CLIENT_API_KEYS="${CLIENT_API_KEYS:-ck_$(gen 40)}"
STAFF_API_KEYS="${STAFF_API_KEYS:-sk_$(gen 40)}"

deploy() {
  az deployment group create \
    --resource-group "$RG" \
    --name "support-agent-$(date +%Y%m%d%H%M%S)" \
    --template-file "$ROOT/infra/main.bicep" \
    --parameters environmentName="$ENV_NAME" \
                 postgresAdminPassword="$PG_PASSWORD" \
                 clientApiKeys="$CLIENT_API_KEYS" staffApiKeys="$STAFF_API_KEYS" \
                 openAiApiKey="$OPENAI_API_KEY" cohereApiKey="$COHERE_API_KEY" "$@" \
    --query properties.outputs -o json
}

echo ">> Resource group $RG ($LOCATION)"
az group create --name "$RG" --location "$LOCATION" -o none

echo ">> Phase 1/3: infrastructure (no app yet)"
OUTPUTS="$(deploy deployApp=false)"
ACR="$(echo "$OUTPUTS" | jq -r .acrName.value)"

echo ">> Phase 2/3: build image in ACR ($ACR)"
TAG="bootstrap-$(git -C "$ROOT" rev-parse --short HEAD 2>/dev/null || date +%s)"
az acr build --registry "$ACR" --image "support-agent:$TAG" --file "$ROOT/Dockerfile" "$ROOT" -o none

echo ">> Phase 3/3: container app"
OUTPUTS="$(deploy deployApp=true containerImage="$ACR.azurecr.io/support-agent:$TAG")"

cat <<SUMMARY

============================================================================
 Deployed.
   App URL        : $(echo "$OUTPUTS" | jq -r .appUrl.value)
   Postgres       : $(echo "$OUTPUTS" | jq -r .postgresFqdn.value)
   Key Vault      : $(echo "$OUTPUTS" | jq -r .keyVaultName.value)

 Secrets were stored in Key Vault. Save these somewhere safe NOW:
   CLIENT_API_KEYS=$CLIENT_API_KEYS
   STAFF_API_KEYS=$STAFF_API_KEYS

 Next:
   1. Ingest the knowledge base (see docs/deployment.md#ingest)
   2. Set GitHub repo variables for deploy.yml:
        ACR_NAME=$ACR
        CONTAINER_APP_NAME=$(echo "$OUTPUTS" | jq -r .containerAppName.value)
        AZURE_RESOURCE_GROUP=$RG
============================================================================
SUMMARY
