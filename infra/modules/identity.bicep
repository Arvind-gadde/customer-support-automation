// One user-assigned identity for the app. User-assigned (not system-assigned) so
// role assignments exist BEFORE the Container App is created - no first-boot races.
param name string
param location string
param tags object

resource identity 'Microsoft.ManagedIdentity/userAssignedIdentities@2023-01-31' = {
  name: name
  location: location
  tags: tags
}

output id string = identity.id
output principalId string = identity.properties.principalId
output clientId string = identity.properties.clientId
