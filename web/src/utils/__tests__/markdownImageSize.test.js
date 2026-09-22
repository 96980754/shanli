import assert from 'node:assert/strict'
import MarkdownIt from 'markdown-it'

import { markdownItImageSize } from '../markdownImageSize.js'

const render = (source) =>
  new MarkdownIt({ html: true, breaks: true, linkify: true })
    .use(markdownItImageSize)
    .render(source)
    .trim()

const run = () => {
  // 1. `=宽度` 生效，宽度从说明文字中摘掉
  assert.equal(render('![产品图=240](u.png)'), '<p><img src="u.png" alt="产品图" width="240"></p>')
  console.log('T1 width suffix applied: PASS')

  // 2. 未写宽度的图片不加 width，说明文字保持完整（回归护栏：覆盖 image 规则不能弄丢 alt）
  assert.equal(render('![产品图](u.png)'), '<p><img src="u.png" alt="产品图"></p>')
  console.log('T2 image without width untouched: PASS')

  // 3. `|` 作为兼容分隔符同样生效
  assert.equal(render('![产品图|240](u.png)'), '<p><img src="u.png" alt="产品图" width="240"></p>')
  console.log('T3 pipe separator accepted: PASS')

  // 4. 表格单元格内使用 `=` 不会拆坏表格列
  const table = render('| 图 | 说明 |\n| --- | --- |\n| ![产品图=240](u.png) | 参数 |')
  assert.ok(
    table.includes('<td><img src="u.png" alt="产品图" width="240"></td>'),
    'sized image should stay inside a single table cell'
  )
  assert.ok(table.includes('<td>参数</td>'), 'table should keep both columns')
  console.log('T4 width suffix inside table: PASS')

  // 5. 宽度不是纯数字时原样渲染，不产生 width，说明文字不丢
  assert.equal(render('![图=abc](u.png)'), '<p><img src="u.png" alt="图=abc"></p>')
  console.log('T5 non-numeric width ignored: PASS')

  // 6. 覆盖 image 规则不能丢掉其它属性（title）
  const titled = render('![产品图=240](u.png "标题")')
  assert.ok(titled.includes('title="标题"'), 'title attribute should survive')
  assert.ok(titled.includes('width="240"'), 'width should still be applied')
  console.log('T6 other attributes preserved: PASS')

  console.log('\nAll 6 tests passed!')
}

run()
