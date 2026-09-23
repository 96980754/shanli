# AI 知识库 iOS 发布交接

## 工程信息

- 应用名称：AI 知识库
- Bundle ID：`com.yuxi.knowledge`
- Version：`0.7.1`
- Build：`70104`
- 最低系统：iOS 15
- 当前远程前端：`https://39.105.10.183/agent`
- Xcode 工程：`App/App.xcodeproj`

## 甲方在 Mac 上的发布步骤

1. 使用 Xcode 26 或更高版本打开 `App/App.xcodeproj`。
2. 选择 `App` Target，打开 `Signing & Capabilities`。
3. 选择甲方 Apple Developer Team。
4. 确认甲方已经注册 Bundle ID `com.yuxi.knowledge`；如需更换，必须同时修改项目、Apple Developer 和 App Store Connect 中的 Bundle ID。
5. 连接 iPhone 进行一次 Archive 前的真机验证。
6. 选择 `Any iOS Device (arm64)`，执行 `Product → Archive`。
7. 在 Organizer 中执行 `Distribute App → App Store Connect → Upload`。
8. 在 App Store Connect 等待构建处理，然后添加到 TestFlight 或提交审核。

也可以使用仓库中的 `.github/workflows/ios-signed-ipa.yml` 在 GitHub macOS Runner 上签名、导出 IPA 并上传 TestFlight。

## 当前自签名证书说明

工程已内置 `server_39_105_10_183.cer`，并通过 `PinnedCertificatePlugin.swift` 对目标 IP、服务端叶子证书、证书有效期和主机名进行精确验证。证书 SHA-256：

```text
81459EBE46745DB1A52DB167CC546F37169621C42A23B2947CA5A983EE4A629C
```

证书有效期至 2027 年 9 月 1 日。服务器更换地址或证书时必须同步修改：

- 根目录 `web/capacitor.config.json` 的 `server.url`
- `PinnedCertificatePlugin.swift` 的 `pinnedHost`
- `server_39_105_10_183.cer`

正式上架建议改用域名和公开可信 HTTPS 证书，并移除 `Info.plist` 中的 `NSAllowsArbitraryLoadsInWebContent` 及证书固定兼容插件，以降低 ATS 审核风险。

## 发布前必须由甲方填写的内容

- Apple Developer Team
- App Store Connect 应用记录
- 签名证书和 Provisioning Profile
- 隐私政策网址
- 技术支持网址
- App Store 截图、描述、关键词和联系方式
- 审核演示账号

证书、私钥、密码、`.p12`、`.mobileprovision` 和 App Store Connect API Key 不应提交到代码仓库。
