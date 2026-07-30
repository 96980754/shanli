export function resolveDocumentParentId(parentId, currentParentId, recursive) {
  if (recursive) return null
  return parentId !== undefined ? parentId : currentParentId
}
