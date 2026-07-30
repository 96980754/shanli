export function buildVersionCandidate(file, response, canManage) {
  const sameNameFiles = Array.isArray(response?.same_name_files) ? response.same_name_files : []
  const action = canManage && sameNameFiles.length > 0 ? 'version' : 'add'

  return {
    uid: file.uid,
    filename: file.name,
    action,
    currentFileId: action === 'version' && sameNameFiles.length === 1 ? sameNameFiles[0].file_id : undefined,
    selectedFile: action === 'version' && sameNameFiles.length === 1 ? sameNameFiles[0] : null,
    sameNameFiles
  }
}

export function selectVersionTarget(candidate, file) {
  return {
    ...candidate,
    action: 'version',
    currentFileId: file.file_id,
    selectedFile: file
  }
}

export function pruneVersionCandidates(candidates, files) {
  const validUids = new Set(files.map((file) => file.uid).filter(Boolean))
  return candidates.filter((candidate) => validUids.has(candidate.uid))
}

export function findDuplicateVersionTarget(candidates) {
  const used = new Set()
  for (const candidate of candidates) {
    if (candidate.action !== 'version' || !candidate.currentFileId) continue
    if (used.has(candidate.currentFileId)) return candidate.currentFileId
    used.add(candidate.currentFileId)
  }
  return null
}
