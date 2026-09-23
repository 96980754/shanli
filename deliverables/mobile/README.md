# 移动端交付产物

本目录存放移动端**已构建的安装包**，源码工程分别在 `web/android/` 与 `web/ios/`。

```text
AI知识库-Android-0.7.1-beta2.2-debug.apk
  SHA-256  15d3e7d10c47cb5e5a6a3b36ee6fc2aa81a0c6bf3010a50f511bf394640403cd

AI知识库-iOS最终交付包-20260921.zip
  SHA-256  088e8db63237e11207b97ddd1575961309b17dd1a1ae283d5c719263d9757ffd
```

::: warning 两个产物都绑死当前开发地址
`https://39.105.10.183` 及其自签名证书（SHA-256 `81459EBE…A629C`，有效期至 2027-09-01）。换甲方服务器后必须按 [移动端打包与服务器地址配置](../../docs/advanced/mobile-app-packaging.md) 改完 5 处地址再重新出包。
:::

## Android

- 形态：Capacitor 远程前端直连，`server.url = https://39.105.10.183/agent`（`/agent` 是对话页路由，启动直达问答页）
- 内置自签名证书做定向信任：`network_security_config.xml` + `res/raw/server_cert.crt`，只信任该主机 + 该证书
- **debug 签名**（标准 Android Debug 密钥），`android:debuggable="true"`

**可以**：在任意 Android 手机上侧载安装并连接当前服务器，用于验收与试用。
**不可以**：上架应用商店。

## iOS

交付包共 7 项：完整源码 ZIP、原生工程 ZIP、模拟器 `.app`、云端打包说明、Mac 检测手册、自检报告、SHA-256 校验值。

**包里没有签名 IPA，因此不能安装到真实 iPhone。** 真机分发需要 Apple Developer 账号下的证书、Provisioning Profile、Team ID 和 App Store Connect 权限，这些资产归属发布方（甲方）；配置齐全后可用 `.github/workflows/ios-signed-ipa.yml` 在 GitHub macOS Runner 上生成 IPA 并上传 TestFlight。模拟器 `.app` 只能用于 iOS 模拟器和编译验证。

包内「完整源码 ZIP」的 `web/` 部分来自 2026-08-26 从 `13-ye/shanli` 的 `codex/ios-build-check` 分支（提交 `ad10591`）打出的快照，**不是本仓库当前 main**——两者 `web/src` 逐文件比对有 178 个文件不同。**源码请以本仓库为准**；iOS 原生工程已入库到 `web/ios/`。

## 上架前仍需确认

- **Bundle ID** `com.yuxi.knowledge` 上架后不可更改，第一次上传前需确认。
- 当前 IP + 自签名证书是过渡方案，正式上架建议改用域名 + 公开 CA 证书，届时可删掉证书固定逻辑与 `Info.plist` 的 ATS 例外，App Store 审核也不需要再解释 ATS 例外。
- iOS 当前形态是远程网站外壳，App Store 4.2 审核需要额外原生能力。
