/**
 * Markdown 图片宽度：`![说明=宽度](图片URL)` 渲染为带 width 属性的图片
 *
 * 宽度写在说明文字末尾，URL 保持原样不动——回答里的图片 URL 由检索结果给出，
 * 让它停留在原处可以避免模型重写 URL 导致裂图。
 *
 * `|` 是兼容的替代分隔符（模型更习惯这种写法，正文里同样有效），
 * 但表格单元格内未转义的 `|` 会被 markdown-it 当作列分隔符拆坏表格，故提示词一律约定 `=`。
 */
const IMAGE_WIDTH_RE = /[|=](\d{1,4})$/

/**
 * 注册 markdown-it 的 image 渲染规则
 *
 * 宽度超出列宽时由外层 CSS 的 `max-width: 100%` 兜底等比缩放，此处不做取值限制。
 */
export const markdownItImageSize = (md) => {
  md.renderer.rules.image = (tokens, idx, options, env, self) => {
    const token = tokens[idx]
    const alt = self.renderInlineAsText(token.children, options, env)
    const matched = IMAGE_WIDTH_RE.exec(alt)

    token.attrSet('alt', matched ? alt.slice(0, matched.index) : alt)
    if (matched) {
      token.attrSet('width', matched[1])
    }

    return self.renderToken(tokens, idx, options)
  }
}
