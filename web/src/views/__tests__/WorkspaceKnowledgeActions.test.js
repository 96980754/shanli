import assert from 'node:assert/strict'
import fs from 'node:fs'
import path from 'node:path'
import { fileURLToPath } from 'node:url'

import {
  canManageWorkspaceSource,
  getWorkspaceActionDisabledReason,
  isKnowledgeMountSource
} from '../../utils/workspace_knowledge_policy.js'

const ownedManage = { kb_id: 'kb_owned', created_by: 'current', can_manage: true }
const ownedView = { kb_id: 'kb_owned_view', created_by: 'current', can_manage: false }
const sharedManage = { kb_id: 'kb_shared', created_by: 'other', can_manage: true }
const sharedView = { kb_id: 'kb_shared_view', created_by: 'other', can_manage: false }

assert.equal(isKnowledgeMountSource('database:kb_owned', ownedManage), true)
assert.equal(canManageWorkspaceSource('database:kb_owned', ownedManage), true)
assert.equal(canManageWorkspaceSource('database:kb_owned_view', ownedView), false)
assert.equal(canManageWorkspaceSource('database:kb_shared', sharedManage), true)
assert.equal(canManageWorkspaceSource('database:kb_shared_view', sharedView), false)
assert.equal(canManageWorkspaceSource('personal', null), true)
assert.equal(
  getWorkspaceActionDisabledReason('database:kb_shared_view', sharedView),
  '当前知识库为只读，无法执行此操作'
)
assert.equal(getWorkspaceActionDisabledReason('personal', null), '')

const testDirectory = path.dirname(fileURLToPath(import.meta.url))
const workspaceSource = fs.readFileSync(path.join(testDirectory, '..', 'WorkspaceView.vue'), 'utf8')
const uploadModalSource = fs.readFileSync(
  path.join(testDirectory, '..', '..', 'components', 'FileUploadModal.vue'),
  'utf8'
)

assert.match(workspaceSource, /<FileUploadModal/)
assert.match(workspaceSource, /:kb-id="selectedDatabase\?\.kb_id \|\| ''"/)
assert.match(workspaceSource, /:current-folder-id="knowledgeFileBrowser\.parentId"/)
assert.match(workspaceSource, /documentApi\.createFolder\(/)
assert.match(workspaceSource, /await refreshKnowledgeEntries\(\)/)
assert.match(workspaceSource, /uploadWorkspaceFiles\(currentPath\.value, files\)/)
assert.match(uploadModalSource, /kbId:\s*\{\s*type: String/)
assert.match(uploadModalSource, /props\.kbId \|\| store\.kbId/)

console.log('workspace knowledge actions tests passed')
