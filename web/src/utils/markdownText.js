/**
 * Convert Markdown content to a compact plain-text preview.
 * Full content should still be rendered with MarkdownPreview in detail views.
 */
export const stripMarkdown = (value, maxLength = 160) => {
  if (!value) return ''

  const text = String(value)
    .replace(/```[\s\S]*?```/g, ' ')
    .replace(/`([^`]+)`/g, '$1')
    .replace(/!\[([^\]]*)\]\([^)]*\)/g, '$1')
    .replace(/\[([^\]]+)\]\([^)]*\)/g, '$1')
    .replace(/^\s{0,3}#{1,6}\s+/gm, '')
    .replace(/^\s{0,3}>\s?/gm, '')
    .replace(/^\s*[-*+]\s+/gm, '')
    .replace(/^\s*\d+[.)]\s+/gm, '')
    .replace(/[*_~]/g, '')
    .replace(/\s+/g, ' ')
    .trim()

  return text.length > maxLength ? `${text.slice(0, maxLength)}…` : text
}
