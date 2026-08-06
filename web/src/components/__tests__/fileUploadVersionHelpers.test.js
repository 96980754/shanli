import assert from 'node:assert/strict'

import {
  buildVersionCandidate,
  findDuplicateVersionTarget,
  pruneVersionCandidates,
  selectVersionTarget
} from '../fileUploadVersionHelpers.js'

const run = () => {
  const upload = { uid: 'upload-1', name: 'sglang-v1.1.docx' }
  const current = { file_id: 'file-v1', filename: 'sglang-v1.0.docx' }

  const noMatch = buildVersionCandidate(upload, {}, true)
  assert.equal(noMatch.action, 'add')
  assert.equal(noMatch.currentFileId, undefined)

  // 去版本号前缀匹配的版本候选：上传 sglang-v1.1 时匹配 sglang-v1.0
  const prefixMatch = buildVersionCandidate(
    upload,
    { version_candidate_files: [{ file_id: 'file-v1', filename: 'sglang-v1.0.docx' }] },
    true
  )
  assert.equal(prefixMatch.action, 'version')
  assert.equal(prefixMatch.currentFileId, 'file-v1')
  assert.equal(prefixMatch.selectedFile.filename, 'sglang-v1.0.docx')

  // 同名优先：version_candidate_files 与 same_name_files 都有时，精确同名在前且不重复
  const mixedMatch = buildVersionCandidate(
    upload,
    {
      same_name_files: [{ file_id: 'file-same', filename: upload.name }],
      version_candidate_files: [{ file_id: 'file-v1', filename: 'sglang-v1.0.docx' }]
    },
    true
  )
  assert.equal(mixedMatch.action, 'version')
  // 多个候选时不自动预选（用户选择目标）
  assert.equal(mixedMatch.currentFileId, undefined)
  assert.equal(mixedMatch.sameNameFiles.length, 2)

  const singleMatch = buildVersionCandidate(
    upload,
    { same_name_files: [{ file_id: 'file-same', filename: upload.name }] },
    true
  )
  assert.equal(singleMatch.action, 'version')
  assert.equal(singleMatch.currentFileId, 'file-same')

  const multipleMatches = buildVersionCandidate(
    upload,
    {
      same_name_files: [
        { file_id: 'file-a', filename: upload.name },
        { file_id: 'file-b', filename: upload.name }
      ]
    },
    true
  )
  assert.equal(multipleMatches.action, 'version')
  assert.equal(multipleMatches.currentFileId, undefined)

  const selected = selectVersionTarget(noMatch, current)
  assert.equal(selected.action, 'version')
  assert.equal(selected.currentFileId, 'file-v1')
  assert.equal(selected.selectedFile.filename, 'sglang-v1.0.docx')

  assert.deepEqual(pruneVersionCandidates([selected], []), [])
  assert.equal(
    findDuplicateVersionTarget([
      selected,
      { ...selected, uid: 'upload-2' }
    ]),
    'file-v1'
  )
  assert.equal(buildVersionCandidate(upload, { same_name_files: [current] }, false).action, 'add')

  console.log('fileUploadVersionHelpers: all assertions passed')
}

run()
