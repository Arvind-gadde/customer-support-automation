// =============================================================================
// Support Agent - Azure infrastructure
//
//   Container Apps  ──(managed identity)──▶ Azure OpenAI   (key auth DISABLED)
//        │   └──────(managed identity)──▶ Key Vault       (secrets as refs)
//        │   └──────(managed identity)──▶ ACR             (AcrPull)
//        └──▶ PostgreSQL Flexible Server (pgvector + LangGraph checkpoints)
//        └──▶ Log Analytics + Application Insights (OpenTelemetry)
//
// Deploy in two phases (infra/scripts/bootstrap.sh does this for you):
//   1. deployApp=false  -> everything except the Container App (needs an image)
//   2. push an image to ACR, then deployApp=true
// =============================================================================
targetScope = 'resourceGroup'

@description('Environment name. "prod" turns on HA, larger SKUs, purge protection.')
@allowed(['dev', 'staging', 'prod'])
param environmentName string = 'dev'

@description('Region for everything except Azure OpenAI.')
param location string = resourceGroup().location

@description('Region for Azure OpenAI. Model/SKU availability differs per region - check before deploying.')
param openAiLocation string = location

@minLength(3)
@maxLength(10)
@description('Short lowercase alphanumeric app name used in resource names.')
param appName string = 'supportagt'

@description('Create the Container App. False on first run, before an image exists in ACR.')
param deployApp bool = true

@description('Full image reference, e.g. myacr.azurecr.io/support-agent:<sha>.')
param containerImage string = ''

@secure()
param postgresAdminPassword string

@secure()
@description('Comma-separated API keys for client applications (BFFs).')
param clientApiKeys string

@secure()
@description('Comma-separated API keys for the internal staff console.')
param staffApiKeys string

param chatModelName string = 'gpt-4.1-mini'
param chatModelVersion string = '2025-04-14'
@description('Chat deployment capacity in thousands of tokens per minute.')
param chatCapacity int = 50
param embeddingModelName string = 'text-embedding-3-small'
param embeddingModelVersion string = '1'
param embeddingCapacity int = 50

@description('Optional Entra object ID of a developer/CI principal that also needs OpenAI access (for local dev + evals).')
param developerPrincipalId string = ''

@description('Order service base URL. Required for prod (in-memory fakes are refused in production).')
param orderServiceBaseUrl string = ''
param ticketingBaseUrl string = ''

var isProd = environmentName == 'prod'
var suffix = take(uniqueString(resourceGroup().id, environmentName), 8)
var prefix = '${appName}-${environmentName}'
var tags = {
  app: appName
  environment: environmentName
  managedBy: 'bicep'
}
var postgresAdminLogin = 'pgadmin'
var databaseName = 'support_agent'

module monitoring 'modules/monitoring.bicep' = {
  name: 'monitoring'
  params: {
    name: prefix
    location: location
    tags: tags
    retentionInDays: isProd ? 90 : 30
  }
}

module identity 'modules/identity.bicep' = {
  name: 'identity'
  params: {
    name: 'id-${prefix}'
    location: location
    tags: tags
  }
}

module registry 'modules/registry.bicep' = {
  name: 'registry'
  params: {
    name: 'acr${appName}${environmentName}${suffix}'
    location: location
    tags: tags
    sku: isProd ? 'Standard' : 'Basic'
    pullPrincipalId: identity.outputs.principalId
  }
}

module postgres 'modules/postgres.bicep' = {
  name: 'postgres'
  params: {
    name: 'psql-${prefix}-${suffix}'
    location: location
    tags: tags
    isProd: isProd
    adminLogin: postgresAdminLogin
    adminPassword: postgresAdminPassword
    databaseName: databaseName
  }
}

module openai 'modules/openai.bicep' = {
  name: 'openai'
  params: {
    name: 'oai-${prefix}-${suffix}'
    location: openAiLocation
    tags: tags
    chatModelName: chatModelName
    chatModelVersion: chatModelVersion
    chatCapacity: chatCapacity
    embeddingModelName: embeddingModelName
    embeddingModelVersion: embeddingModelVersion
    embeddingCapacity: embeddingCapacity
    userPrincipalIds: empty(developerPrincipalId) ? [] : [developerPrincipalId]
    servicePrincipalId: identity.outputs.principalId
  }
}

module keyVault 'modules/keyvault.bicep' = {
  name: 'keyvault'
  params: {
    name: 'kv-${appName}-${environmentName}-${suffix}'
    location: location
    tags: tags
    isProd: isProd
    readerPrincipalId: identity.outputs.principalId
    secrets: {
      'database-url': 'postgresql://${postgresAdminLogin}:${uriComponent(postgresAdminPassword)}@${postgres.outputs.fqdn}:5432/${databaseName}?sslmode=require'
      'client-api-keys': clientApiKeys
      'staff-api-keys': staffApiKeys
    }
  }
}

module app 'modules/containerapp.bicep' = if (deployApp) {
  name: 'containerapp'
  params: {
    name: 'ca-${prefix}'
    environmentName: 'cae-${prefix}'
    location: location
    tags: tags
    isProd: isProd
    image: containerImage
    identityId: identity.outputs.id
    identityClientId: identity.outputs.clientId
    registryServer: registry.outputs.loginServer
    logAnalyticsWorkspaceName: monitoring.outputs.workspaceName
    keyVaultUri: keyVault.outputs.uri
    env: [
      { name: 'ENVIRONMENT', value: isProd ? 'production' : 'staging' }
      { name: 'LOG_JSON', value: 'true' }
      { name: 'AZURE_OPENAI_ENDPOINT', value: openai.outputs.endpoint }
      { name: 'AZURE_OPENAI_CHAT_DEPLOYMENT', value: openai.outputs.chatDeployment }
      { name: 'AZURE_OPENAI_EMBEDDING_DEPLOYMENT', value: openai.outputs.embeddingDeployment }
      { name: 'APPLICATIONINSIGHTS_CONNECTION_STRING', value: monitoring.outputs.appInsightsConnectionString }
      { name: 'ORDER_SERVICE_BASE_URL', value: orderServiceBaseUrl }
      { name: 'TICKETING_BASE_URL', value: ticketingBaseUrl }
      { name: 'OTEL_SERVICE_NAME', value: 'support-agent' }
    ]
  }
}

output acrName string = registry.outputs.name
output acrLoginServer string = registry.outputs.loginServer
output containerAppName string = deployApp ? app!.outputs.name : ''
output appUrl string = deployApp ? 'https://${app!.outputs.fqdn}' : ''
output openAiEndpoint string = openai.outputs.endpoint
output keyVaultName string = keyVault.outputs.name
output postgresFqdn string = postgres.outputs.fqdn
