/**
 * i18n 一致性检查 + 残留中文扫描（进度/回归指标，非硬门禁）
 *
 * 用法：node scripts/check-i18n.mjs [--detail] [--ci]
 *   --detail  列出每个残留中文行的文件与行号
 *   --ci      残留中文行数超过 0 时以非零码退出（供 CI 用，默认不启用）
 *
 * 两个检查：
 *   1. zh-CN.js / en-US.js 的 key 集合必须完全一致（缺翻译直接报错退出）
 *   2. 扫描 web/src 下所有 .vue 与 .js 文件中非注释的 CJK 行，统计残留数量
 *      支持行尾 `// i18n-ignore` 与文件头 `i18n-ignore-file`（块注释内）白名单
 *      （启发式：无法区分后端数据与文案，故定位为指标而非硬门禁）
 */
/* eslint-env node */
import { readFileSync, readdirSync, statSync } from 'node:fs'
import { fileURLToPath } from 'node:url'
import { join, relative, sep } from 'node:path'

const root = fileURLToPath(new URL('..', import.meta.url)) // web/
const srcDir = join(root, 'src')
const detail = process.argv.includes('--detail')
const ci = process.argv.includes('--ci')

/* ---------- 1. locale key 一致性 ---------- */

function collectKeys(obj, prefix = '', out = []) {
  for (const [k, v] of Object.entries(obj)) {
    const path = prefix ? `${prefix}.${k}` : k
    if (v && typeof v === 'object' && !Array.isArray(v)) collectKeys(v, path, out)
    else out.push(path)
  }
  return out
}

const zh = (await import(join(root, 'src/i18n/locales/zh-CN.js'))).default
const en = (await import(join(root, 'src/i18n/locales/en-US.js'))).default
const zhKeys = new Set(collectKeys(zh))
const enKeys = new Set(collectKeys(en))

const missingEn = [...zhKeys].filter((k) => !enKeys.has(k))
const missingZh = [...enKeys].filter((k) => !zhKeys.has(k))

if (missingEn.length || missingZh.length) {
  console.error('❌ locale key 不一致：')
  for (const k of missingEn) console.error(`   zh-CN 有但 en-US 缺: ${k}`)
  for (const k of missingZh) console.error(`   en-US 有但 zh-CN 缺: ${k}`)
  process.exit(1)
}
console.log(`✅ locale key 一致：${zhKeys.size} 条（zh-CN / en-US 完全同步）`)

/* ---------- 2. 残留中文扫描 ---------- */

const CJK = /[一-鿿]/
const IGNORE_DIRS = new Set(['node_modules', 'dist', 'locales', '.vite', '.git', '__tests__'])
const IGNORE_FILES = new Set(['base.css']) // 样式变量注释等

function walk(dir, out = []) {
  for (const name of readdirSync(dir)) {
    if (IGNORE_DIRS.has(name)) continue
    const p = join(dir, name)
    const st = statSync(p)
    if (st.isDirectory()) walk(p, out)
    else if (/(\.vue|\.js)$/.test(name)) out.push(p)
  }
  return out
}

// 粗略去掉注释后判断是否含中文：整行注释（//、*、/*、<!--、#）直接跳过；
// 并跟踪多行块注释（/* */ 与 <!-- -->）的状态，注释块内部的行不计入
function isCommentLine(trimmed) {
  return trimmed.startsWith('//') || trimmed.startsWith('*') || trimmed.startsWith('/*') ||
    trimmed.startsWith('<!--') || trimmed.startsWith('-->') || trimmed.startsWith('#')
}

let total = 0
const perFile = []
for (const file of walk(srcDir)) {
  const rel = relative(root, file).split(sep).join('/')
  if (IGNORE_FILES.has(relative(srcDir, file))) continue
  const raw = readFileSync(file, 'utf-8')
  // 文件级白名单
  if (raw.includes('i18n-ignore-file')) continue
  const lines = raw.split('\n')
  let count = 0
  const hits = []
  let jsBlock = false
  let htmlBlock = false
  lines.forEach((line, idx) => {
    if (line.includes('i18n-ignore')) return
    // 处于多行注释块内：跳过，检测本行是否闭合
    if (jsBlock) {
      if (line.includes('*/')) jsBlock = false
      return
    }
    if (htmlBlock) {
      if (line.includes('-->')) htmlBlock = false
      return
    }
    const trimmed = line.trim()
    if (!trimmed) return
    // console.* 为开发期诊断日志，永不上屏，不计入残留
    if (/^console\.(log|error|warn|debug|info)\(/.test(trimmed)) return
    // 打开未闭合的多行注释块：整行视为注释（<!-- 或 /* 之后无对应闭合标记）
    if (trimmed.startsWith('<!--') && !trimmed.includes('-->')) {
      htmlBlock = true
      return
    }
    if (trimmed.startsWith('/*') && !trimmed.includes('*/')) {
      jsBlock = true
      return
    }
    if (isCommentLine(trimmed)) return
    if (CJK.test(line)) {
      count++
      hits.push({ line: idx + 1, text: trimmed.slice(0, 60) })
    }
  })
  if (count) perFile.push({ file: rel, count, hits })
  total += count
}

perFile.sort((a, b) => b.count - a.count)
console.log(`\n📊 残留中文行数：${total}（${perFile.length} 个文件）`)
if (detail) {
  for (const { file, hits } of perFile) {
    for (const h of hits) console.log(`   ${file}:${h.line}  ${h.text}`)
  }
} else if (total > 0) {
  console.log('   前 20 大文件：')
  for (const { file, count } of perFile.slice(0, 20)) console.log(`   ${count.toString().padStart(5)}  ${file}`)
  console.log('   （加 --detail 查看逐行明细）')
}

if (ci && total > 0) {
  console.error('\n❌ CI 模式下存在残留中文')
  process.exit(1)
}
console.log(total === 0 ? '\n✅ 无残留中文' : `\n⚠️ 残留 ${total} 行（数据/注释/白名单除外后可忽略）`)
