param name string
param environmentName string
param location string
param tags object
param isProd bool
param image string
param identityId string
param registryServer string
param logAnalyticsWorkspaceName string
param keyVaultUri string
@description('Key Vault secrets to expose as env vars: [{ name, env }].')
param keyVaultSecrets array
@description('Plain (non-secret) environment variables.')
param env array

resource workspace 'Microsoft.OperationalInsights/workspaces@2023-09-01' existing = {
  name: logAnalyticsWorkspaceName
}

resource managedEnv 'Microsoft.App/managedEnvironments@2024-03-01' = {
  name: environmentName
  location: location
  tags: tags
  properties: {
    appLogsConfiguration: {
      destination: 'log-analytics'
      logAnalyticsConfiguration: {
        customerId: workspace.properties.customerId
        sharedKey: workspace.listKeys().primarySharedKey
      }
    }
    zoneRedundant: false // true requires VNet integration
  }
}

// Secrets are Key Vault REFERENCES resolved by the platform via managed identity.
// Rotating a secret in Key Vault + restarting the revision picks up the new value.
var kvSecrets = keyVaultSecrets
var secretEnv = [for s in kvSecrets: { name: s.env, secretRef: s.name }]

resource app 'Microsoft.App/containerApps@2024-03-01' = {
  name: name
  location: location
  tags: tags
  identity: {
    type: 'UserAssigned'
    userAssignedIdentities: {
      '${identityId}': {}
    }
  }
  properties: {
    managedEnvironmentId: managedEnv.id
    configuration: {
      activeRevisionsMode: 'Single'
      ingress: {
        external: true
        targetPort: 8000
        transport: 'auto'
        allowInsecure: false
      }
      registries: [
        {
          server: registryServer
          identity: identityId
        }
      ]
      secrets: [
        for s in kvSecrets: {
          name: s.name
          keyVaultUrl: '${keyVaultUri}secrets/${s.name}'
          identity: identityId
        }
      ]
    }
    template: {
      containers: [
        {
          name: 'api'
          image: image
          resources: {
            cpu: json(isProd ? '1.0' : '0.5')
            memory: isProd ? '2Gi' : '1Gi'
          }
          env: concat(env, secretEnv)
          probes: [
            {
              type: 'Startup'
              httpGet: { path: '/health/live', port: 8000 }
              periodSeconds: 5
              failureThreshold: 24 // up to 2 min for migrations on first boot
            }
            {
              type: 'Liveness'
              httpGet: { path: '/health/live', port: 8000 }
              periodSeconds: 15
              failureThreshold: 3
            }
            {
              type: 'Readiness'
              httpGet: { path: '/health/ready', port: 8000 }
              periodSeconds: 10
              failureThreshold: 3
            }
          ]
        }
      ]
      scale: {
        minReplicas: isProd ? 2 : 1 // >=2 in prod: no cold starts, survives a replica crash
        maxReplicas: isProd ? 10 : 3
        rules: [
          {
            name: 'http-concurrency'
            http: {
              metadata: {
                concurrentRequests: '20' // LLM calls are I/O-bound and slow
              }
            }
          }
        ]
      }
    }
  }
}

output name string = app.name
output fqdn string = app.properties.configuration.ingress.fqdn
