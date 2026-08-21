import assert from 'node:assert/strict'

import {
  buildVersionedFilename,
  groupSourceFileIdsByKnowledgeBase,
  normalizeSourceVersions
} from '../knowledgeSourceVersions.js'

const requests = groupSourceFileIdsByKnowledgeBase([
  { kb_id: 'kb-1', file_id: 'file-1' },
  { kb_id: 'kb-1', file_id: 'file-1' },
  { kb_id: 'kb-1', file_id: 'file-2' },
  { kb_id: 'kb-2', file_id: 'file-3' },
  { kb_id: 'kb-2', file_id: '' }
])
assert.deepEqual(requests, [
  { kbId: 'kb-1', fileIds: ['file-1', 'file-2'] },
  { kbId: 'kb-2', fileIds: ['file-3'] }
])

const normalized = normalizeSourceVersions([
  {
    kbId: 'kb-1',
    items: [
      {
        file_id: 'file-3',
        document_version: 3,
        history_versions: [
          { file_id: 'file-1', document_version: 1, updated_at: '2026-08-01T00:00:00Z' },
          { file_id: 'file-2', document_version: 2, updated_at: '2026-08-02T00:00:00Z' },
          { file_id: 'file-2', document_version: 2, updated_at: '2026-08-02T00:00:00Z' }
        ]
      }
    ]
  }
])
assert.equal(normalized.get('kb-1::file-3').document_version, 3)
assert.deepEqual(
  normalized.get('kb-1::file-3').history_versions.map((item) => item.document_version),
  [2, 1]
)

assert.equal(buildVersionedFilename('星河终端.docx', 1), '星河终端_V1.docx')
assert.equal(buildVersionedFilename('README', 2), 'README_V2')

console.log('knowledgeSourceVersions tests passed')
