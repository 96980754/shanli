import assert from 'node:assert/strict'

import { rewriteMinioImageUrls } from '../minioUrl.js'

const run = () => {
  // 1. localhost:9000 的解析器图片 URL 重写为站内相对路径
  const rewritten = rewriteMinioImageUrls(
    '架构图：![架构](http://localhost:9000/public/kb1/kb-images/1712_img.png)'
  )
  assert.ok(
    rewritten.includes('![架构](/minio/public/kb1/kb-images/1712_img.png)'),
    'localhost:9000 URL should be rewritten to /minio/public/'
  )
  console.log('T1 localhost:9000 rewrite: PASS')

  // 2. 局域网 IP:9000 同样重写
  const lan = rewriteMinioImageUrls('http://192.168.1.10:9000/public/kb1/kb-images/a.jpg')
  assert.equal(lan, '/minio/public/kb1/kb-images/a.jpg')
  console.log('T2 LAN IP rewrite: PASS')

  // 3. 无端口号的 HOST 也重写
  const noPort = rewriteMinioImageUrls('http://minio/public/kb1/kb-images/b.png')
  assert.equal(noPort, '/minio/public/kb1/kb-images/b.png')
  console.log('T3 host without port rewrite: PASS')

  // 4. 外部普通链接不受影响
  const external = rewriteMinioImageUrls('![外链](https://example.com/a.png)')
  assert.ok(external.includes('https://example.com/a.png'), 'external URL should be untouched')
  console.log('T4 external URL untouched: PASS')

  // 5. 普通文本/相对路径不受影响
  const plain = rewriteMinioImageUrls('回答正文，无图片链接 /minio/public/x.png')
  assert.equal(plain, '回答正文，无图片链接 /minio/public/x.png')
  console.log('T5 plain text untouched: PASS')

  console.log('\nAll 5 tests passed!')
}

run()
