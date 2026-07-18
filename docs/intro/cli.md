# 命令行工具

`ai-kb-cli` 是 AI知识库 的命令行客户端，适合在本地脚本或终端中管理远程实例、登录账号、上传知识库文件，以及运行部分智能体任务。

## 安装

推荐使用 `uv` 或 `pipx` 安装：

```bash
uv tool install ai-kb-cli
```

也可以临时运行：

```bash
uvx --from ai-kb-cli ai-kb --help
```

安装后可通过 `ai-kb --version` 查看当前版本。

## 配置远程实例

先添加一个 AI知识库 实例地址，再设为当前默认 remote：

```bash
ai-kb remote add local http://localhost:5173
ai-kb remote use local
ai-kb remote ping
```

配置会保存在 `~/.ai-kb/config.toml`。如果需要同时管理多个实例，可以继续添加其他 remote，并通过 `ai-kb remote use <name>` 切换。

## 登录

默认使用浏览器授权登录：

```bash
ai-kb login --browser
```

如果已经在 AI知识库 中创建了 API Key，也可以直接导入：

```bash
ai-kb login --api-key yxkey_xxx
```

常用账号状态命令：

```bash
ai-kb whoami
ai-kb status
ai-kb logout
```

## 上传知识库文件

上传目录时，如果不指定 `--kb-id`，CLI 会拉取当前 remote 中可用的知识库并在终端中选择：

```bash
ai-kb kb upload ./docs
```

默认会选择常见文本和 Office 文档类型，可在预览阶段调整文件类型；也可以通过参数直接指定：

```bash
ai-kb kb upload ./docs --kb-id kb_xxx --concurrency 4
ai-kb kb upload ./docs --include-ext md,html,docx
```

CLI 会让每个并发单元完成单个文件的上传和文档记录添加，`--concurrency` 默认 10，允许范围 1-300，用于控制同时处理的文件数。上传会保留目录中的相对路径，便于在知识库文件列表中按原目录结构查看。

## 运行智能体评估

如果实例已配置 Langfuse 数据集，可以用 CLI 触发智能体评估：

```bash
ai-kb agent eval \
  --dataset-name demo-dataset \
  --agent-slug default-agent \
  --experiment-name cli-demo
```

该命令会读取 Langfuse 数据集输入，调用 AI知识库 智能体运行，并把结果回传到对应实验中。
