# CloudRelay ImageGen

This repository is versioned through the root `VERSION` file. The installed skill can check for a newer release without changing files, and it can apply a verified release when explicitly authorized.

Check the installed copy:

```bash
python scripts/check_update.py
```

Apply an update with an interactive confirmation:

```bash
python scripts/update.py --apply
```

For a trusted scheduled job that has been explicitly configured for unattended updates:

```bash
python scripts/update.py --auto
```

To opt in to applying verified updates automatically when the skill activates, set `CLOUDRELAY_IMAGEGEN_AUTO_UPDATE=1` in the host environment. Without that explicit opt-in, activation performs a read-only check and reports an available update instead of changing files.

The updater only trusts the GitHub Releases endpoint for this repository, requires the release asset SHA-256 digest, validates the downloaded archive before replacement, and updates only managed runtime files. The digest verifies transfer/content integrity against the GitHub API response; it is not a publisher signature, so GitHub and repository-release access remain the trust root. It never reads or writes the CloudRelay API key, generated images, or unrelated files. Network failures during the normal skill activation check are non-fatal.

For each release, make the Git tag, root `VERSION`, adapter manifest versions, and packaged asset version identical. Upload the asset as `cloudrelay-imagegen.skill`; the updater rejects a missing digest, a tag/archive version mismatch, a downgrade, or an archive that omits the updater runtime files.

通过 [CloudRelay](https://cloudrelay.cn) 统一异步媒体 API 生成图片的跨客户端 Agent Skill。同一份 `SKILL.md` 原生兼容 Codex、Claude Code、Gemini CLI、OpenClaw 和 Cursor，不需要维护客户端分叉版本。

## 为什么使用异步接口

原生同步生图接口需要在图片生成期间持续保持 HTTP 连接。当生成耗时超过约 120 秒时，请求容易触发 Cloudflare 超时并被中断，即使后端仍在继续生成，客户端也可能无法取得最终结果。

CloudRelay 为此开发了异步生图接口：客户端只需提交一次生成任务并保存返回的任务 ID，随后通过独立的轮询请求查询状态，任务完成后再获取并保存图片。这样可以避免让一次长连接贯穿整个生成过程，降低长耗时任务被 Cloudflare 超时掐断的风险。

本 Skill 的目的，是让 Codex、Claude Code、Gemini CLI、OpenClaw、Cursor 等 Agent 客户端快速掌握并可靠执行这套异步调用流程，包括安全配置凭据、提交任务、保存任务 ID、持续轮询、处理终态、下载结果和报告异常。

## 功能

- 使用 `gpt-image-2` 等 CloudRelay 支持的模型生成图片
- GPT 默认使用 `gpt-image-2`
- Grok 默认使用 `grok-imagine-image-2.0`
- Gemini 使用 `gemini-3.1-flash-image-preview` 或 `gemini-3-pro-image-preview`，每次调用前必须由用户确认
- GPT、Gemini、Grok 共用模型发现、异步提交和任务轮询接口
- 支持 `1024x1024`、`1536x1024`、`1024x1536`、`2048x2048` 和自动尺寸
- 支持一次生成 1 至 4 张图片
- 自动提交异步任务、轮询状态并保存 base64 或 URL 结果
- 自动识别 PNG、JPEG、GIF 和 WebP，并在结束后报告实际生成参数
- 三个渠道分别使用独立环境变量和密钥分组，不跨渠道回退
- 运行脚本只依赖 Python 标准库

## 兼容性

本仓库遵循通用的 `SKILL.md + scripts/ + references/ + assets/` AgentSkills 结构，frontmatter 只使用五个客户端共同支持的 `name` 和 `description`。

| 客户端 | 用户级目录 | 项目级目录 | 加载方式 |
|---|---|---|---|
| Codex | `~/.codex/skills/cloudrelay-imagegen` | `.agents/skills/cloudrelay-imagegen` | 重启或新建任务 |
| Claude Code | `~/.claude/skills/cloudrelay-imagegen` | `.claude/skills/cloudrelay-imagegen` | 新建会话 |
| Gemini CLI | `~/.gemini/skills/cloudrelay-imagegen` | `.gemini/skills/cloudrelay-imagegen` | `/skills reload` |
| OpenClaw | `~/.openclaw/skills/cloudrelay-imagegen` | `skills/cloudrelay-imagegen` | 新会话或文件监视器刷新 |
| Cursor | `~/.cursor/skills/cloudrelay-imagegen` | `.cursor/skills/cloudrelay-imagegen` | 新建 Agent 对话 |

Gemini CLI、OpenClaw 和 Cursor 也支持项目中的 `.agents/skills/` 兼容目录。本仓库的安装器默认选择每个客户端自己的主目录，以免同一台机器上的发现优先级产生歧义。

## 环境要求

- Python 3.10 或更高版本
- 可以访问 `https://cloudrelay.cn` 的网络环境
- GPT：`CLOUDRELAY_GPT_IMAGE_API_KEY`，密钥分组必须是 `生图专用`
- Gemini：`CLOUDRELAY_GEMINI_API_KEY`，密钥使用 `gemini` 分组
- Grok：`CLOUDRELAY_GROK_API_KEY`，密钥使用 `grok` 分组
- 至少安装一个受支持的 Agent 客户端

## 统一安装器

先下载仓库：

```bash
git clone https://github.com/CloudRelay-Code/cloudrelay-imagegen-skill.git
cd cloudrelay-imagegen-skill
```

安装到单个客户端的用户目录：

```bash
python install.py --client claude-code
python install.py --client gemini-cli
python install.py --client openclaw
python install.py --client cursor
python install.py --client codex
```

一次安装到全部客户端：

```bash
python install.py --client all
```

一次安装到多个指定客户端：

```bash
python install.py --client claude-code --client cursor
```

安装到项目目录：

```bash
python install.py --client cursor --scope project --project-dir /path/to/project
```

先查看目标目录但不写文件：

```bash
python install.py --client all --dry-run
```

更新已经安装的运行文件：

```bash
python install.py --client all --force
```

`--force` 只覆盖本 Skill 已知的运行文件，不会删除目标目录中的其他文件。

## 客户端原生命令

### Gemini CLI

Gemini CLI 可以直接从 GitHub 安装仓库根目录中的 Skill：

```bash
gemini skills install https://github.com/CloudRelay-Code/cloudrelay-imagegen-skill
```

项目级安装：

```bash
gemini skills install https://github.com/CloudRelay-Code/cloudrelay-imagegen-skill --scope workspace
```

安装后在交互会话中执行：

```text
/skills reload
/skills list
```

也可以从 [Releases](https://github.com/CloudRelay-Code/cloudrelay-imagegen-skill/releases/latest) 下载 `cloudrelay-imagegen.skill`，然后运行：

```bash
gemini skills install /path/to/cloudrelay-imagegen.skill
```

### OpenClaw

全局安装：

```bash
openclaw skills install git:CloudRelay-Code/cloudrelay-imagegen-skill@main --global
```

安装到当前 OpenClaw workspace：

```bash
openclaw skills install git:CloudRelay-Code/cloudrelay-imagegen-skill@main
```

### Claude Code、Cursor 和 Codex

使用上面的统一安装器。它会将纯运行文件复制到各客户端的官方 Skill 目录，并给出对应的重新加载提示。

## 配置 API Key

在 [CloudRelay](https://cloudrelay.cn) 创建对应分组的 API Key。三个渠道必须分别配置，不能用其他渠道的 Key 代替：

| 渠道 | 环境变量 | 必需分组 | 配置命令 |
|---|---|---|---|
| GPT | `CLOUDRELAY_GPT_IMAGE_API_KEY` | `生图专用` | `python scripts/configure_api_key.py --provider gpt` |
| Gemini | `CLOUDRELAY_GEMINI_API_KEY` | `gemini` | `python scripts/configure_api_key.py --provider gemini` |
| Grok | `CLOUDRELAY_GROK_API_KEY` | `grok` | `python scripts/configure_api_key.py --provider grok` |

脚本使用隐藏输入，Key 不会显示在终端中。Windows 保存为用户级环境变量，macOS/Linux 保存到 `${XDG_CONFIG_HOME:-~/.config}/cloudrelay/` 下对应的独立文件。只检查是否已配置、不显示 Key：

```bash
python scripts/configure_api_key.py --provider gpt --check
python scripts/configure_api_key.py --provider gemini --check
python scripts/configure_api_key.py --provider grok --check
```

## 使用

可以让客户端根据描述自动激活 Skill：

```text
通过 CloudRelay 生成一张 1536x1024、电影感的雪山日出图片，保存到 generated-images。
```

指定渠道时，在请求中说明 `GPT`、`Gemini` 或 `Grok`。使用 Gemini 时，必须先确认使用 `gemini-3.1-flash-image-preview` 还是 `gemini-3-pro-image-preview`。

Codex 和 OpenClaw 也可以显式引用：

```text
使用 $cloudrelay-imagegen 生成一张产品主图。
```

Claude Code 可以使用对应的 Skill 命令：

```text
/cloudrelay-imagegen 生成一张产品主图
```

Gemini CLI 首次自动激活 Skill 时会请求用户确认。Cursor 会根据 `description` 在 Agent 对话中自动发现并使用 Skill。

## 直接调用脚本

生成图片（GPT 示例）：

```bash
python scripts/generate_image.py \
  --provider "gpt" \
  --prompt "a cinematic sunrise over snowy mountains" \
  --model "gpt-image-2" \
  --size "1536x1024" \
  --quality "high" \
  --count 1 \
  --output-dir ./generated-images
```

Windows PowerShell 请使用反引号替代示例中的反斜杠续行符，或将参数写在同一行。

查看全部参数：

```bash
python scripts/generate_image.py --help
```

## 安全边界

- 仓库和 `.skill` 包不包含 API Key 或生成结果。
- 生成脚本不接受命令行 Key 参数，避免凭据进入 Shell 历史。
- Skill 不会主动索要 Key；如果用户自愿通过聊天提供，脚本会通过 stdin 安全配置且不会回显。
- 请求目标固定为 `https://cloudrelay.cn`。
- 三个渠道统一使用 `/v1/media/models`、`/v1/images/generations/async` 和 `/v1/images/tasks/{task_id}`，所有请求均使用对应渠道的 Bearer Key。
- 安装第三方 Skill 前仍应检查 `SKILL.md` 和脚本，并根据客户端设置网络、Shell 和文件权限。

## 验证

```bash
python -m unittest discover -s tests -v
python -m py_compile scripts/configure_api_key.py scripts/generate_image.py install.py
python tools/package_skill.py
```

发布前还会使用 Codex `skill-creator` 的 `quick_validate.py` 对一个名为 `cloudrelay-imagegen` 的干净运行副本进行验证。

## 官方规范参考

- [Agent Skills specification](https://agentskills.io)
- [Claude Code Skills](https://code.claude.com/docs/en/skills)
- [Gemini CLI Agent Skills](https://github.com/google-gemini/gemini-cli/blob/main/docs/cli/using-agent-skills.md)
- [OpenClaw Skills](https://docs.openclaw.ai/tools/skills)
- [Cursor Skills](https://cursor.com/docs/skills)

## 许可证

[MIT License](LICENSE)
