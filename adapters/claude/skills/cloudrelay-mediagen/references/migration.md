# 从 ImageGen 一次性迁移到 MediaGen

## 当前可用范围

旧身份为 cloudrelay-imagegen，新身份为 cloudrelay-mediagen；两者可以并存。
已有旧版（包括 v1.5.0）保留其目录和 frontmatter，通过旧仓库的 1.5.x 通道更新图片 Skill。
不要把媒体 archive 喂给旧更新器，也不要把旧更新器指向新目录。

旧仓库 CloudRelay-Code/cloudrelay-imagegen-skill 已于 2026-09-29 恢复为独立仓库。
ImageGen 1.5.1 使用 cloudrelay-imagegen.skill；MediaGen 2.0.1 使用新仓库及
cloudrelay-mediagen.skill。两个 latest、下载 URL 和身份各自独立。
早期仓库重命名造成的重定向并不能让旧更新器跨身份升级；切换到媒体功能始终需要单独安装新 Skill。

独立 tools/package_legacy_skill.py 只从 legacy/cloudrelay-imagegen 构建旧版十文件包；
tools/package_skill.py 只构建完整媒体包。两个 builder 固定各自的身份、仓库和文件清单，
不会失败后回退到另一身份。旧版是否有可用更新，以其独立仓库实际 latest metadata 为准。

## 安装和检查

保留原来的 cloudrelay-imagegen 目录，继续按原有方式使用其图片功能。不要运行旧更新器跨身份升级。
在终端中单独安装新身份：

~~~bash
npx --yes skills add "https://github.com/CloudRelay-Code/cloudrelay-mediagen-skill" --skill "cloudrelay-mediagen" --global --yes
~~~

安装器实际输出的目录为准。不同 Agent 或安装工具可能使用独立目录或链接；本迁移脚本拒绝
符号链接、Windows junction、reparse point 和其路径组件，不会自动解析链接后写入另一处。
对于由安装工具建立的链接，请核实后显式提供安全的真实 Skill 目录。

确认媒体副本为 2.0.1 或更新版本，且包含 scripts/migrate_from_imagegen.py。旧媒体 v2.0.0
不含此脚本；若其更新器仍报告 local-variant，请使用上面的独立安装命令获取新媒体版本。
也可从本仓库运行迁移检查。迁移脚本本身不会下载安装任何内容。
示例中的路径必须替换为自己机器上的实际独立目录；含空格路径使用引号：

~~~bash
python scripts/migrate_from_imagegen.py --source-dir "/actual/skills/cloudrelay-imagegen" --target-dir "/actual/skills/cloudrelay-mediagen" --dry-run
python scripts/migrate_from_imagegen.py --source-dir "/actual/skills/cloudrelay-imagegen" --target-dir "/actual/skills/cloudrelay-mediagen" --yes
~~~

~~~powershell
python scripts/migrate_from_imagegen.py --source-dir "C:\Users\me\.codex\skills\cloudrelay-imagegen" --target-dir "C:\Users\me\.codex\skills\cloudrelay-mediagen" --dry-run
~~~

默认行为和 --dry-run 都不写文件；--dry-run 优先于 --yes。
--yes 只在已验证的新身份目录内，以排他创建方式记录 .cloudrelay-imagegen-migration.json。
它只记录源/目标目录、身份、密钥配置存在状态、输出目录存在状态；不会覆盖已有回执或用户文件。
目标尚未安装时只报告下一步安装命令，不创建一个看似已安装的空 Skill。

## 凭据和数据

仍使用 imagegen-api-key-gpt、imagegen-api-key-gemini、imagegen-api-key-grok，
以及 CLOUDRELAY_GPT_IMAGE_API_KEY、CLOUDRELAY_GEMINI_API_KEY、CLOUDRELAY_GROK_API_KEY。
两种 Skill 共享同一用户的外部凭据存储：Windows 用户环境变量；POSIX 的
XDG_CONFIG_HOME/cloudrelay/ 或默认 ~/.config/cloudrelay/。不需要复制密钥。

迁移只检查环境变量名称和文件元数据，不读取、打印、复制、散列或验证 API key 内容。
报告的 configured / unconfigured 表示“已配置 / 未配置”的存在性检测，不代表密钥有效性。
空的环境设置也会被计为存在；Windows 注册表中仅持久化但未进入当前进程的环境设置不会被查询，
可重开终端后再运行检查。Skill 内自放的密钥文件会保留，检测到它不代表运行时会自动使用它。

源目录、新目录已有文件、generated-images、generated-videos 以及其他自定义文件都留在原处。
迁移回执不属于更新器管理文件，因此后续更新也不会覆盖它。此工具不复制或移动生成结果，
新 Skill 如需使用旧图/视频，应显式引用其原始文件路径。

## 重新加载

- Codex：Skill 变化通常自动发现；列表中仍未出现时重启 Codex。
- Claude Code：现有 Skill 目录受监视；新建顶层 Skill 目录后执行 /reload-skills，旧版本不支持时重启会话。
- Gemini CLI：执行 /skills reload，再用 /skills list 确认；不支持该命令的版本重启 CLI。
- OpenClaw：默认 watcher 更新后，下一个 Agent turn 使用新列表；关闭监视时按现有部署方式重启 Gateway。
- Cursor：重启 Cursor 后确认 Skill 被发现；此建议基于官方所述的启动时发现行为。

检查新身份已被发现，且图片与视频参考文档都存在，再使用媒体工作流。

## 发布通道边界

媒体通道固定为 CloudRelay-Code/cloudrelay-mediagen-skill、cloudrelay-mediagen.skill、
cloudrelay-mediagen 和 2.x 及以后版本，不会切换到旧通道。
旧通道固定为 CloudRelay-Code/cloudrelay-imagegen-skill、cloudrelay-imagegen.skill、
cloudrelay-imagegen 和 1.5.x；只管理原有十个文件，不引入视频或迁移运行时。
两个更新器保留版本 build metadata；发布 tag、ReleaseInfo 与归档 VERSION 必须完整一致，
版本先后排序仍忽略 build metadata。两包均校验 GitHub SHA-256 digest、身份、下载路径、
归档路径及普通文件类型；不删除未知用户文件。

旧 v1.5.0 图片生成和密钥配置代码按历史字节保留。其原始更新器的 URL 约束未被放宽；
1.5.1 通过精确旧仓库路径提供兼容包。最新真实 Release、下载摘要与历史更新器验收记录
见媒体源码仓库 docs/compatibility/report.md 和 docs/compatibility/published-release-verification.json。
本地包哈希或离线模拟不能代替发布后真实 metadata、digest 与升级验收。
