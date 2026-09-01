/**
 * Node 测试专用模块解析钩子
 *
 * 让 `node` 直接运行的 `.test.js` 能解析 Vite 的两类导入：
 *   1. `@/xxx` 别名 → web/src/xxx
 *   2. 无扩展名的相对导入（`./locales/zh-CN` 等，Node 原生不解析，Vite 会）
 *
 * 仅测试用，不影响构建。用法：
 *   node --import ./scripts/test-alias-register.mjs src/utils/__tests__/xxx.test.js
 */
import { statSync } from 'node:fs'
import { dirname, join } from 'node:path'
import { fileURLToPath, pathToFileURL } from 'node:url'

const SRC_ROOT = fileURLToPath(new URL('../src/', import.meta.url))

function isFile(p) {
  try {
    return statSync(p).isFile()
  } catch {
    return false
  }
}

function tryResolve(abs) {
  if (!abs) return null
  if (isFile(abs)) return abs
  if (isFile(abs + '.js')) return abs + '.js'
  if (isFile(join(abs, 'index.js'))) return join(abs, 'index.js')
  return null
}

export async function resolve(specifier, context, nextResolve) {
  let resolved = null
  if (specifier.startsWith('@/')) {
    resolved = tryResolve(join(SRC_ROOT, specifier.slice(2)))
  } else if (/^\.\.?\//.test(specifier) && !/\.[a-z0-9]+$/i.test(specifier)) {
    // 无扩展名的相对导入：Node 默认按文件精确解析，这里补齐 .js / index.js
    const parentDir = dirname(fileURLToPath(context.parentURL))
    resolved = tryResolve(join(parentDir, specifier))
  }
  if (resolved) return { url: pathToFileURL(resolved).href, shortCircuit: true }
  // 无扩展名的包子路径（如 `dayjs/locale/zh-cn`）：原生失败后补 `.js` 重试
  if (
    !/^\.\.?\//.test(specifier) &&
    !specifier.startsWith('node:') &&
    specifier.includes('/') &&
    !/\.[a-z0-9]+$/i.test(specifier)
  ) {
    try {
      return await nextResolve(specifier, context)
    } catch (err) {
      if (err?.code === 'ERR_MODULE_NOT_FOUND') {
        return await nextResolve(specifier + '.js', context)
      }
      throw err
    }
  }
  return nextResolve(specifier, context)
}
