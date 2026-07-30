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
