// =============================================================================
// Support Agent - Azure infrastructure
//
//   Container Apps  ──(HTTPS, key from Key Vault)──▶ OpenAI API
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

@secure()
@description('OpenAI API key. Use a project-scoped key with a budget limit.')
param openAiApiKey string

@description('OpenAI chat model.')
param openAiChatModel string = 'gpt-4.1-mini'

@secure()
@description('Optional Cohere key. Empty = no reranker (hybrid-search order is kept).')
param cohereApiKey string = ''

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

module keyVault 'modules/keyvault.bicep' = {
  name: 'keyvault'
  params: {
    name: 'kv-${appName}-${environmentName}-${suffix}'
    location: location
    tags: tags
    isProd: isProd
    readerPrincipalId: identity.outputs.principalId
    secrets: union(
      {
        'database-url': 'postgresql://${postgresAdminLogin}:${uriComponent(postgresAdminPassword)}@${postgres.outputs.fqdn}:5432/${databaseName}?sslmode=require'
        'client-api-keys': clientApiKeys
        'staff-api-keys': staffApiKeys
        'openai-api-key': openAiApiKey
      },
      empty(cohereApiKey) ? {} : { 'cohere-api-key': cohereApiKey }
    )
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
    registryServer: registry.outputs.loginServer
    logAnalyticsWorkspaceName: monitoring.outputs.workspaceName
    keyVaultUri: keyVault.outputs.uri
    keyVaultSecrets: concat(
      [
        { name: 'database-url', env: 'DATABASE_URL' }
        { name: 'client-api-keys', env: 'CLIENT_API_KEYS' }
        { name: 'staff-api-keys', env: 'STAFF_API_KEYS' }
        { name: 'openai-api-key', env: 'OPENAI_API_KEY' }
      ],
      empty(cohereApiKey) ? [] : [{ name: 'cohere-api-key', env: 'COHERE_API_KEY' }]
    )
    env: [
      { name: 'ENVIRONMENT', value: isProd ? 'production' : 'staging' }
      { name: 'LOG_JSON', value: 'true' }
      { name: 'OPENAI_CHAT_MODEL', value: openAiChatModel }
      { name: 'RERANKER_BACKEND', value: empty(cohereApiKey) ? 'none' : 'cohere' }
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
output keyVaultName string = keyVault.outputs.name
output postgresFqdn string = postgres.outputs.fqdn
