export function buildVersionCandidate(file, response, canManage) {
  // 精确同名文件（重复检测/冲突路径）优先
  const sameNameFiles = Array.isArray(response?.same_name_files) ? response.same_name_files : []
  // 去版本号基础名匹配的版本候选（如上传 sglang-v1.1 时匹配 sglang-v1.0）
  const versionCandidateFiles = Array.isArray(response?.version_candidate_files)
    ? response.version_candidate_files
    : []
  // 合并去重：version_candidate_files 追加在 same_name_files 之后，不覆盖精确同名
  for (const candidate of versionCandidateFiles) {
    if (!sameNameFiles.some((item) => item.file_id === candidate.file_id)) {
      sameNameFiles.push(candidate)
    }
  }
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
