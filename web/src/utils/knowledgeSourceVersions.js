export const groupSourceFileIdsByKnowledgeBase = (fileGroups = []) => {
  const grouped = new Map()
  for (const item of fileGroups) {
    const kbId = String(item?.kb_id || '').trim()
    const fileId = String(item?.file_id || '').trim()
    if (!kbId || !fileId) continue
    if (!grouped.has(kbId)) grouped.set(kbId, new Set())
    grouped.get(kbId).add(fileId)
  }
  return Array.from(grouped, ([kbId, fileIds]) => ({ kbId, fileIds: [...fileIds] }))
}

export const normalizeSourceVersions = (responses = []) => {
  const versions = new Map()
  for (const response of responses) {
    for (const item of response?.items || []) {
      const kbId = String(response?.kbId || '').trim()
      const fileId = String(item?.file_id || '').trim()
      if (!kbId || !fileId) continue
      const historyVersions = Array.isArray(item.history_versions)
        ? [
            ...new Map(
              item.history_versions
                .filter((version) => version?.file_id)
                .map((version) => [version.file_id, version])
            ).values()
          ].sort((a, b) => {
            const versionDifference =
              Number(b?.document_version || 0) - Number(a?.document_version || 0)
            if (versionDifference) return versionDifference
            return String(b?.updated_at || '').localeCompare(String(a?.updated_at || ''))
          })
        : []
      versions.set(`${kbId}::${fileId}`, {
        ...item,
        history_versions: historyVersions
      })
    }
  }
  return versions
}

export const buildVersionedFilename = (filename, version) => {
  const value = String(filename || 'document')
  const suffix = `_V${Number(version) || 1}`
  const dotIndex = value.lastIndexOf('.')
  if (dotIndex <= 0) return `${value}${suffix}`
  return `${value.slice(0, dotIndex)}${suffix}${value.slice(dotIndex)}`
}
