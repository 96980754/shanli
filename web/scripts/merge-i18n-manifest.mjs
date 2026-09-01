/**
 * 合并子代理产出的 i18n key 清单（manifest）到 zh-CN.js / en-US.js
 *
 * 用法：node scripts/merge-i18n-manifest.mjs <manifest.json> [manifest2.json ...]
 *
 * manifest 结构：{ "ns.key": { "zh": "中文", "en": "English" }, ... }
 *   - key 按点号拆分为嵌套结构（ns.key 或 ns.sub.key 均可）
 *   - zh 为组件里的原中文（必须与代码替换前完全一致），en 为英文翻译
 *
 * 规则：
 *   - key 已存在于词表且 zh 值一致 → 跳过（子代理复用了已有 key，安全）
 *   - key 已存在但 zh 值不同 → 报错退出（真正的冲突，需人工处理）
 *   - 同一批 manifest 内重复 key → 报错退出
 *   - 合并后原子重写两个 locale 文件，保持 key 完全一致（check:i18n 强制）
 */
/* eslint-env node */
import { readFileSync, writeFileSync } from 'node:fs'
import { fileURLToPath } from 'node:url'
import { join } from 'node:path'

const root = fileURLToPath(new URL('..', import.meta.url)) // web/
const zhPath = join(root, 'src/i18n/locales/zh-CN.js')
const enPath = join(root, 'src/i18n/locales/en-US.js')

const ZH_HEADER = '// 中文文案（zh-CN）\nexport default '
const EN_HEADER = '// English messages (en-US)\nexport default '

const zh = (await import(zhPath)).default
const en = (await import(enPath)).default

function getByPath(obj, parts) {
  let cur = obj
  for (const p of parts) {
    if (!cur || typeof cur !== 'object') return undefined
    cur = cur[p]
  }
  return cur
}

function setByPath(obj, parts, value) {
  let cur = obj
  for (let i = 0; i < parts.length - 1; i++) {
    const p = parts[i]
    if (!cur[p] || typeof cur[p] !== 'object') cur[p] = {}
    cur = cur[p]
  }
  cur[parts[parts.length - 1]] = value
}

const manifests = process.argv.slice(2)
if (manifests.length === 0) {
  console.error('用法：node scripts/merge-i18n-manifest.mjs <manifest.json> [...]')
  process.exit(1)
}

let added = 0
let skipped = 0
const errors = []

for (const file of manifests) {
  let data
  try {
    data = JSON.parse(readFileSync(file, 'utf-8'))
  } catch (e) {
    console.error(`❌ 无法读取 manifest ${file}: ${e.message}`)
    process.exit(1)
  }

  for (const [key, { zh: zhVal, en: enVal }] of Object.entries(data)) {
    if (typeof zhVal !== 'string' || typeof enVal !== 'string') {
      errors.push(`${file}: key "${key}" 缺 zh/en 字符串`)
      continue
    }
    const parts = key.split('.')
    if (parts.length < 2) {
      errors.push(`${file}: key "${key}" 需要至少两级（命名空间.名称）`)
      continue
    }

    const existingZh = getByPath(zh, parts)
    const existingEn = getByPath(en, parts)
    if (existingZh !== undefined || existingEn !== undefined) {
      if (existingZh === zhVal && existingEn === enVal) {
        skipped++ // 复用已有 key，值一致，安全跳过
        continue
      }
      errors.push(`${file}: key "${key}" 已存在且值不一致（词表 zh="${existingZh}" en="${existingEn}"，manifest zh="${zhVal}" en="${enVal}"）`)
      continue
    }

    setByPath(zh, parts, zhVal)
    setByPath(en, parts, enVal)
    added++
  }
}

if (errors.length) {
  console.error('❌ 合并中止，存在冲突：')
  for (const e of errors) console.error(`   ${e}`)
  console.error('已回滚，locale 文件未修改。')
  process.exit(1)
}

writeFileSync(zhPath, ZH_HEADER + JSON.stringify(zh, null, 2) + '\n')
writeFileSync(enPath, EN_HEADER + JSON.stringify(en, null, 2) + '\n')

console.log(`✅ 合并完成：新增 ${added} 条，跳过 ${skipped} 条（复用），共 ${manifests.length} 个 manifest。`)
