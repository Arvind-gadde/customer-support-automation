param name string
param location string
param tags object
param isProd bool
param readerPrincipalId string
@secure()
@description('Map of secret name -> value. (Bicep only allows @secure on strings/objects.)')
param secrets object

var secretsUserRoleId = '4633458b-17de-408a-b874-0445c86b69e6' // Key Vault Secrets User

resource vault 'Microsoft.KeyVault/vaults@2023-07-01' = {
  name: take(name, 24)
  location: location
  tags: tags
  properties: {
    tenantId: subscription().tenantId
    sku: {
      family: 'A'
      name: 'standard'
    }
    enableRbacAuthorization: true // RBAC, not legacy access policies
    enableSoftDelete: true
    softDeleteRetentionInDays: isProd ? 90 : 7
    enablePurgeProtection: isProd ? true : null
  }
}

resource vaultSecrets 'Microsoft.KeyVault/vaults/secrets@2023-07-01' = [
  for secret in items(secrets): {
    parent: vault
    name: secret.key
    properties: {
      value: secret.value
    }
  }
]

resource reader 'Microsoft.Authorization/roleAssignments@2022-04-01' = {
  scope: vault
  name: guid(vault.id, readerPrincipalId, secretsUserRoleId)
  properties: {
    principalId: readerPrincipalId
    principalType: 'ServicePrincipal'
    roleDefinitionId: subscriptionResourceId('Microsoft.Authorization/roleDefinitions', secretsUserRoleId)
  }
}

output name string = vault.name
output uri string = vault.properties.vaultUri
