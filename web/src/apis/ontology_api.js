import { apiAdminGet, apiSuperAdminPost, apiSuperAdminPut } from './base'

const BASE_URL = '/api/system/ontology-registries'

export const ontologyRegistryApi = {
  list: async () => apiAdminGet(BASE_URL),

  create: async (definition) => apiSuperAdminPost(BASE_URL, definition),

  detail: async (item) => {
    const registryId = encodeURIComponent(item.registry_id)
    const version = encodeURIComponent(item.version)
    const digest = encodeURIComponent(item.digest)
    return apiAdminGet(`${BASE_URL}/${registryId}/versions/${version}?digest=${digest}`)
  },

  overwrite: async (item, definition) => {
    const registryId = encodeURIComponent(item.registry_id)
    const version = encodeURIComponent(item.version)
    return apiSuperAdminPut(`${BASE_URL}/${registryId}/versions/${version}`, definition)
  },

  upload: async (file) => {
    const formData = new FormData()
    formData.append('file', file)
    return apiSuperAdminPost(`${BASE_URL}/upload`, formData)
  }
}
