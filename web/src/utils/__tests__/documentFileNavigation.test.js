import assert from 'node:assert/strict'

import { resolveDocumentParentId } from '../documentFileNavigation.js'

const run = () => {
  assert.equal(resolveDocumentParentId(null, 'folder-mdm', false), null)
  assert.equal(resolveDocumentParentId(undefined, 'folder-mdm', false), 'folder-mdm')
  assert.equal(resolveDocumentParentId('folder-other', 'folder-mdm', false), 'folder-other')
  assert.equal(resolveDocumentParentId('folder-other', 'folder-mdm', true), null)

  console.log('documentFileNavigation: all assertions passed')
}

run()
