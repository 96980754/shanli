// MinIO 图片 URL 重写：把解析器写入 chunk 的 `http://<host>[:9000]/public/...`
// 重写为站内相对路径 `/minio/public/...`，经 nginx(prod)/vite(dev) 代理访问，
// 避免直连 MinIO 在不同机器/端口下裂图。
const MINIO_PUBLIC_URL_RE = /https?:\/\/[^/\s)"']+?\/public\//g

export const rewriteMinioImageUrls = (text) => text.replace(MINIO_PUBLIC_URL_RE, '/minio/public/')
