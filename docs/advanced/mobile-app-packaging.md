# 移动端打包与服务器地址配置

Android / iOS 应用由 Capacitor 封装，采用**远程前端直连**模式：安装包本身不包含业务页面，启动后由 WebView 直接加载线上站点。因此服务器地址是**构建期写死的**，地址或证书变化时必须改代码并重新出包。

本文说明服务器地址写在哪些文件里、换地址时怎么改、以及改完如何重新构建。

::: warning 通用前提
- 服务器地址必须使用 HTTPS。前端所有接口都是相对路径 `/api/*`，直连模式下会打到所配置站点，请确认该站点已把 `/api` 反代到后端。
- 当前工程内置 `39.105.10.183` 的自签名证书做定向信任，属于**过渡方案**。正式交付建议改用域名 + 公开 CA 证书，见文末。
:::

## 一、工程基线

```text
应用名称：AI 知识库
Bundle ID：com.yuxi.knowledge
Version：0.7.1
最低系统：iOS 15
Web 产物目录：web/dist

Android 工程：web/android/
iOS 工程：web/ios/App/App.xcodeproj
```

`web/capacitor.config.json` 是两端共用的配置：

```json
{
  "appId": "com.yuxi.knowledge",
  "appName": "AI 知识库",
  "webDir": "dist",
  "server": {
    "url": "https://39.105.10.183",
    "cleartext": false
  }
}
```

`server.url` 可以指向站点根路径，也可以指向一个前端路由。已发布的安装包用的是 `https://39.105.10.183/agent`——`/agent` 是对话页路由，这样启动后直接进入问答页，不再经过 `/` → `/login` 的跳转。

## 二、服务器地址写在哪些地方

换地址时**必须同步改完下面 5 处**，只改 `capacitor.config.json` 会导致 App 连接失败（Android 报证书校验错误，iOS 直接取消认证挑战、页面白屏）。

| # | 文件 | 需要改的内容 |
|---|---|---|
| 1 | `web/capacitor.config.json` | `server.url` |
| 2 | `web/android/app/src/main/res/xml/network_security_config.xml` | `<domain>` 里的主机名 |
| 3 | `web/android/app/src/main/res/raw/server_cert.crt` | 服务器证书（PEM 格式） |
| 4 | `web/ios/App/App/PinnedCertificatePlugin.swift` | `pinnedHost` 常量 |
| 5 | `web/ios/App/App/server_39_105_10_183.cer` | 服务器证书（DER 格式），文件名也带主机名 |

第 2、4 项是「只信任这台主机」，第 3、5 项是「只信任这张证书」。两端都是精确匹配：主机名或证书任一不符即拒绝连接，不会退化成信任任意证书。

当前内置证书信息（Android 与 iOS 完全一致）：

```text
SHA-256：81459EBE46745DB1A52DB167CC546F37169621C42A23B2947CA5A983EE4A629C
有效期至：2027-09-01
```

## 三、换地址的操作步骤

### 情况 A：仍用自签名证书（换 IP 或换一张自签证书）

**1. 导出新证书。** 在任意装有 OpenSSL 的机器上执行：

```bash
# iOS 用（DER）
openssl s_client -connect 新地址:443 -servername 新地址 </dev/null 2>/dev/null \
  | openssl x509 -outform DER -out server_新地址.cer

# Android 用（PEM）
openssl s_client -connect 新地址:443 -servername 新地址 </dev/null 2>/dev/null \
  | openssl x509 -outform PEM -out server_cert.crt
```

**2. 替换证书文件。** 把 `server_新地址.cer` 放到 `web/ios/App/App/`，把 `server_cert.crt` 覆盖到 `web/android/app/src/main/res/raw/`（文件名保持不变）。

**3. 改主机名。** 修改 `capacitor.config.json` 的 `server.url`、`network_security_config.xml` 的 `<domain>`、`PinnedCertificatePlugin.swift` 的 `pinnedHost`，以及 `.cer` 的文件名和插件里读取它的 `forResource` 字符串：

```swift
private let pinnedHost = "新地址"
// ...
guard let url = Bundle.main.url(forResource: "server_新地址", withExtension: "cer") else {
```

**4. 校验。** 确认三个地方写的是同一个主机名，并核对新证书的 SHA-256：

```bash
openssl x509 -in server_cert.crt -outform DER | sha256sum
```

### 情况 B：换成域名 + 公开 CA 证书（推荐）

用公开 CA 签发的证书后，两端都不再需要证书固定，代码反而更简单：

1. 准备一个域名（例如 `ai.example.com`）解析到服务器，在 Nginx 上配置该域名的 HTTPS 证书。
2. 用手机浏览器访问 `https://ai.example.com/agent`，确认没有证书警告。
3. 把 `capacitor.config.json` 的 `server.url` 改成 `https://ai.example.com`。
4. 删除 `network_security_config.xml` 中的 `<domain-config>` 整段，以及 `AndroidManifest.xml` 中的 `android:networkSecurityConfig` 属性。
5. 删除 `PinnedCertificatePlugin.swift`、`server_新地址.cer`，以及 `AppBridgeViewController.swift` 中的插件注册。
6. 移除 `Info.plist` 中的 `NSAppTransportSecurity` / `NSAllowsArbitraryLoadsInWebContent`。

第 4～6 步做掉之后，App Store 审核不再需要解释 ATS 例外，这是正式上架前的推荐终态。

## 四、重新构建

改完地址后必须重新出包，两端都要重新构建。

### Android

```bash
cd web
pnpm install
pnpm run build            # 产出 web/dist
pnpm exec cap sync android
# 用 Android Studio 打开 web/android，Gradle 同步后 Build > Generate Signed Bundle / APK
```

命令行方式（需 JAVA 与 Android SDK，且已配置签名）：

```bash
cd web/android
./gradlew assembleRelease
```

### iOS

```bash
cd web
pnpm install
pnpm run build
pnpm exec cap sync ios
```

随后在 Mac 上用 Xcode 打开 `web/ios/App/App.xcodeproj`，选择甲方 Apple Developer Team，`Product → Archive` 后上传 App Store Connect。

没有 Mac 时可以用仓库中的 GitHub Actions（macOS Runner）完成编译和签名，见 `.github/workflows/` 下的两个工作流：

- `ios-project-check.yml`：无签名模拟器构建，用于验证工程能否编译。
- `ios-signed-ipa.yml`：使用 Apple 签名证书和描述文件生成 IPA，可选用 `altool` 上传 TestFlight。

签名相关的证书、`.p12`、`.mobileprovision` 和 App Store Connect API Key 只能放在 GitHub Actions Secrets 或本地钥匙串，**不要提交到仓库**。

## 五、上架前仍需确认

- **Bundle ID**：当前为 `com.yuxi.knowledge`，App Store 上架后不可更改。若甲方要求使用自己域名反写的包名，必须在第一次上传前改掉（需同步修改 iOS 工程、Android `applicationId`、Apple Developer App ID 与 App Store Connect 记录）。
- **自签名证书有效期**：内置证书 2027-09-01 到期，到期前必须换证书并重新出包，否则已安装的 App 会无法连接。
- **App Store 4.2**：当前形态是远程网站外壳，TestFlight 内测可以先做，正式上架建议补充推送、原生分享、相机上传、离线提示或生物识别等原生能力。
