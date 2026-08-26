# 移动端接入（Capacitor）——Android 出包与 iOS 交接

日期：2026-08-26
状态：Android 原生工程已生成，APK 构建待 Android Studio；iOS 交接给甲方。

## 背景与目标

现有知识库问答平台是 Web 应用（Vue + Vite，产物 `web/dist`）。甲方希望有手机端。本方案采用 **Capacitor（WebView 封装）**：一个工程同时产出 Android / iOS 原生壳，壳内加载同一份 Web 前端，两端业务代码完全共享。

已确定分工：**Android 由我们出包，iOS 交给甲方自行配置**（iOS 无法侧载，必须用甲方的 Apple 开发者账号经 TestFlight/App Store/企业签名分发，且需要 Mac + Xcode + 每年 $99，我们不持有这些资产）。

## 已做的工作

- `web/` 新增 Capacitor 依赖：`@capacitor/core` / `@capacitor/android`（dependencies）、`@capacitor/cli`（devDependencies），版本 `^8.5.0`。
- `web/capacitor.config.json`：`appId=com.yuxi.knowledge`、`appName=AI 知识库`、`webDir=dist`。
  - **上线前必须把 `appId` 换成甲方域名的反写包名**（如 `com.甲方.comain.xxx`），应用商店上架后不能改。
- `web/android/`：`npx cap add android` 生成的原生 Gradle 工程（已提交；`app/src/main/assets/public` 等构建产物已被 android/.gitignore 忽略，不提交）。

## Android 出包步骤（我们侧）

```bash
cd web
pnpm install
pnpm build            # 产出 web/dist
npx cap sync android  # 把最新 dist 同步进 android 工程
# 用 Android Studio 打开 web/android，等待 Gradle 同步完成后：
#   Build > Generate Signed Bundle / APK 打出 APK（首次需创建 keystore）
```

无 Android Studio 也可以用命令行：

```bash
cd web/android
./gradlew assembleRelease   # 需要 JAVA + Android SDK（ANDROID_HOME），并先配置签名
```

## 加载模式说明（重要）

当前配置为「**本地打包**」：把 `web/dist` 打进 APK。**前端每次发版都需要重新打 APK**。

生产环境强烈建议改用「**在线加载**」：部署好 HTTPS 域名后，编辑 `web/capacitor.config.json` 增加：

```json
{
  "server": {
    "url": "https://your-knowledge-base.example.com",
    "cleartext": false
  }
}
```

之后 WebView 直接加载线上站点，前端发版无需重打 APK，且站内相对路径 `/api` 天然走网关。注意：本应用前端所有 API 都是相对路径 `/api/*`，**本地打包模式下 APK 内的 `/api` 会打到 WebView 自己的 localhost 而不可用**，因此要么用在线加载、要么在构建时把 API base 指到公网地址。

前提：目前 nginx 只监听 80 端口（无 TLS），PWA / Capacitor 在线加载都要求 HTTPS。上线前需配好 TLS 证书。

## iOS 交接给甲方（5 行命令，甲方在自己 Mac 上执行）

```bash
git clone <仓库地址>
cd web
pnpm install
npx cap sync ios
npx cap open ios
# Xcode 打开后：选择 Team（甲方的 Apple Developer 账号）→ 设置 Bundle ID → Run/Archive
```

先决条件（已就绪/待办）：
- [x] 后端语音转写已兼容 iOS 录制的 MP4 录音（`audio/mp4` → 转发时派生 `.m4a` 后缀）。
- [ ] HTTPS 上线（见上），iOS 在线加载或正式打包都依赖它。

## 验收 checklist

- [ ] `pnpm install && pnpm build && npx cap sync android` 在干净环境可跑通
- [ ] `web/android` 可用 Android Studio 打开、Gradle 同步通过、可打出 debug APK
- [ ] APK 内能加载登录页与问答主流程（本地打包需按上文解决 `/api` 指向）
- [ ] `appId` 已在正式上架前替换为甲方包名
- [ ] iOS 交接文档已随代码仓库交付，甲方按 5 行命令可自行出 iOS 包
