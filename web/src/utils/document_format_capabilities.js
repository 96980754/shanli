const normalizeExtension = (value) => {
  if (typeof value !== 'string') return ''
  const extension = value.trim().toLowerCase()
  if (!extension) return ''
  return extension.startsWith('.') ? extension : `.${extension}`
}

export const normalizeDocumentFormatCapabilities = (capabilities) => {
  if (!Array.isArray(capabilities)) return []
  const normalized = []
  const seen = new Set()
  for (const capability of capabilities) {
    const extension = normalizeExtension(capability?.extension)
    if (!extension || seen.has(extension)) continue
    seen.add(extension)
    normalized.push({
      extension,
      enabled: capability?.enabled === true,
      requires_converter: capability?.requires_converter === true,
      availability: String(capability?.availability || ''),
      reason: typeof capability?.reason === 'string' ? capability.reason : ''
    })
  }
  return normalized
}

export const getUnavailableFormatMessage = (capabilities) =>
  normalizeDocumentFormatCapabilities(capabilities)
    .filter((capability) => !capability.enabled)
    .map(
      (capability) =>
        `${capability.extension.toUpperCase()}：${capability.reason || '当前运行环境不可用'}`
    )
    .join('；')
