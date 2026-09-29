# CloudRelay 旧版兼容实施报告

日期：2026-09-29。工作目录：E:\code\cloudrelay-mediagen-skill。
发布准备状态（旧轨 v1.5.1 已于 06:36:38 UTC 发布，真实 API digest 与本地包匹配；媒体轨及线上更新演练仍待执行）：MediaGen 2.0.1、ImageGen 1.5.1 已获用户明确授权执行源码/tag 推送及 Release 创建，正在进行最终验证。以下发布前调查和测试是历史状态；实际在线结果将在发布后补充。

更新：用户明确同意后，已于 2026-09-29 06:04:30 UTC（北京时间 14:04:30）创建公开独立仓库 CloudRelay-Code/cloudrelay-imagegen-skill，仓库 ID 1394481012。目前分支与 Release 均为空，旧 /releases/latest 返回 404。未推送代码或 tag，未创建 Release。以下第一阶段记录保留建仓前的真实调查事实；最新状态以第五节及 [建仓验证](repository-creation-2026-09-29.json) 为准。

## 1. 兼容性结论

**当前仅支持一次性手动迁移；双轨自动兼容已完成本地候选及离线验证，但尚未在 GitHub 恢复。**

第一阶段在修改前完成只读调查：初始工作区干净，origin 是 CloudRelay-Code/cloudrelay-mediagen-skill，HEAD 是 7bc30dc013325ce252897c2985ecf295b6e9509b。已读取最近五次提交、标签、两版真实 Release、旧地址重定向，以及原始 v1.5.0 三个 updater 文件和 builder。

- 旧 Web 地址 301 到媒体仓库；旧 latest API 301 到 /repositories/1319256142/releases/latest，最终返回 v2.0.0 媒体包。
- 历史 v1.5.0 asset 名仍是 cloudrelay-imagegen.skill，但 browser_download_url 已变成媒体仓库路径。真实历史 downloader 因固定旧仓库 URL 前缀而拒绝；没有放宽 URL 校验。
- 登录账号为 CloudRelay-Code（User），旧名称无独立仓库占用。A 的只读前提成立，当时具备申请恢复精确旧名称的条件；随后经明确授权，建仓已成功。但独立发布链尚未上线，不能声称自动更新已恢复。
- 本地通过真实冻结 v1.5.0 更新器升级实际构建的 1.5.1 包；metadata 和下载响应为明确标注的离线模拟，不冒充真实 GitHub Release。
- 历史 fixture 的 peeled commit 是 2f2d6a077dc2acda21fff2896f144a075d9a3f72，annotated tag 对象是 1a3d213f841cdb93131dfa42f5803473554f9f39。十二文件逐字节对照 Git，未改写历史代码。

原始旧代码仍有固有限制：旧 apply 不检查目标身份；ZIP 检查只拒绝 symlink，其他特殊模式会落为普通文件。测试如实记录这些限制。原始旧 updater 不得被指向媒体目录；1.5.1 候选已补上互斥和非普通文件检查。

证据：[真实 Release 调查](release-evidence-2026-09-29.json)、[最终发布前复核](prepublication-check.json)、[历史 fixture 溯源](../../tests/fixtures/legacy_v1_5_0/provenance.json)。

## 2. 实施方案

| 轨道 | 目标仓库 | 身份 / asset | 候选版本与状态 |
| --- | --- | --- | --- |
| 旧版 | CloudRelay-Code/cloudrelay-imagegen-skill | cloudrelay-imagegen / cloudrelay-imagegen.skill | 1.5.1，本地完成；独立公开仓库已创建、尚无分支或 Release |
| 媒体 | CloudRelay-Code/cloudrelay-mediagen-skill | cloudrelay-mediagen / cloudrelay-mediagen.skill | 2.0.1，本地完成；线上仍 2.0.0 |

主要文件与行为：

- scripts/_update_common.py、update.py、check_update.py：固定媒体 endpoint / repo / asset / identity；HTTPS 每次跳转、SHA-256、tag 与 VERSION 精确匹配（含 build metadata）、路径与普通文件、目标身份和降级检查。SemVer 优先级比较仍忽略 build metadata。
- 旧 local-variant 一律停用更新的逻辑由严格互斥校验取代，测试证明媒体可查询自己的通道，旧目标在网络前拒绝。
- scripts/migrate_from_imagegen.py：标准库、显式 source/target/dry-run/yes；默认不写。未安装目标只提示独立安装，已验证媒体目标在 --yes 下独占创建元数据回执；不会读取或复制 key 内容。
- install.py：检查源与目标身份、预检路径冲突、原子替换管理文件，保留与管理文件 hard link 关联的用户文件；重载提示已按官方文档核对。
- tools/package_skill.py：固定媒体十五文件清单；仅字面 VERSION=2.0.0 的已发布包可以缺少两项后加迁移文件，视频和参考文档始终必需。
- legacy/cloudrelay-imagegen：独立旧身份源码，原始十项 managed/required files；图片生成器、凭据配置器及其他保留运行文件与历史字节一致。其 updater 只接受 1.5.x。
- tools/package_legacy_skill.py：固定旧身份、旧仓库、旧 asset 和原始十文件；独立 source tree、确定性 ZIP、原子输出、固定绑定 AST 校验；失败不回退到媒体包。
- 新增四套兼容测试与 fixture；更新 README、SKILL、CHANGELOG、references/migration.md，同步 Codex/Claude adapters。
- .github/workflows/validate.yml 保留 Ubuntu/Windows × Python 3.10/3.12 矩阵，新增独立旧版编译/构建，分别验证 dist/media 与 dist/legacy；无自动发布步骤。

新旧 archive 不能互相通过身份、版本或下载地址验证；不会把媒体 runtime 冒充旧版包。独立源码和旧 builder 虽在当前开发仓库中准备，发布时只将旧树复制到独立旧仓库。

## 3. 测试结果

| 环境 | 主套件 | 通过 | 跳过 | 失败 / 错误 | 视频专项 |
| --- | ---: | ---: | ---: | ---: | --- |
| Windows，Python 3.12.14，两轮 | 170 / 轮 | 167 / 轮 | 3 / 轮 | 0 / 0 | 14 全通过 |
| 原生 Ubuntu WSL2 /tmp，Python 3.10.12 | 170 | 167 | 3 | 0 / 0 | 14 全通过 |

两平台的跳过项互补：全部 170 个主测试至少在一个平台实际通过，加上 14 个视频测试，去重覆盖 184 项。重复运行不累计成新测试。没有未修复失败。

新增套件：test_legacy_compat.py 28 项，test_migration.py 37 项，test_release_channels.py 30 项，test_dual_track.py 29 项。涵盖真实历史协议、digest 成败、旧 URL 约束、互斥身份、降级、视频清单、并行安装、共享外部 key 名称、dry-run 零写入、无 key 内容读取、路径/链接/junction/FIFO、普通文件、hard link、回滚、确定性双包、build metadata 精确匹配。

Windows 跳过的完整原因（均在 Linux 实际通过）：
1. test_provider_persistence_writes_outside_skill：POSIX secret-file persistence is not used on Windows。
2. test_native_posix_paths：Requires a native POSIX host; forward slashes on Windows are separate。
3. test_apply_rejects_special_destination_file：FIFO special files are POSIX-only。

Linux 跳过的完整原因（均在 Windows 实际通过）：
1. test_legacy_builder_rejects_junction_before_source_read：Windows junction behavior。
2. test_native_windows_backslash_paths：Requires a native Windows host。
3. test_real_windows_junction_is_rejected：A real junction requires Windows。

最终 Windows 核心命令如下；完整可复核的实际命令、退出码、环境与日志路径见 [Windows JSON](validation-summary.json) 和 [POSIX JSON](posix-validation.json)。

~~~powershell
$env:PATH='C:\Users\Administrator\.cache\codex-runtimes\codex-primary-runtime\dependencies\python;'+$env:PATH
$env:PYTHONDONTWRITEBYTECODE='1'
$env:PYTHONUTF8='1'
$compatDeps=Join-Path $env:TEMP 'cloudrelay-compat-validation-20260929'
python -m pip install --target $compatDeps -r requirements-dev.txt
$env:PYTHONPATH=$compatDeps
python tools/sync_adapters.py
python -m unittest discover -s tests -v
python -m py_compile scripts/configure_api_key.py scripts/generate_image.py scripts/generate_video.py scripts/migrate_from_imagegen.py scripts/_update_common.py scripts/check_update.py scripts/update.py install.py tools/package_skill.py tools/package_legacy_skill.py tools/sync_adapters.py legacy/cloudrelay-imagegen/scripts/configure_api_key.py legacy/cloudrelay-imagegen/scripts/generate_image.py legacy/cloudrelay-imagegen/scripts/_update_common.py legacy/cloudrelay-imagegen/scripts/check_update.py legacy/cloudrelay-imagegen/scripts/update.py
python tools/package_skill.py
python tools/package_legacy_skill.py
python tools/sync_adapters.py
python -m unittest discover -s tests -v
python scripts/test_video.py -v
python install.py --client all --dry-run
~~~

十七脚本编译通过（包括线上验收工具）；实际 bytecode 定向到临时缓存，不污染源码。两包都从最终 ZIP 解压到独立干净临时目录后运行本机 skill-creator/scripts/quick_validate.py，均通过且身份正确。YAML parser 检查通过。Linux 使用已有 PyYAML 6.0.2，无依赖安装或主机配置变更；Windows 使用隔离目录的 PyYAML 6.0.3。

本地包（Windows / Linux 构建字节完全一致；再次构建相同）：

| asset | VERSION | 文件数 | 字节 | SHA-256 |
| --- | --- | ---: | ---: | --- |
| cloudrelay-mediagen.skill | 2.0.1 | 15 | 45139 | df076d9e7cec01028700b962066009a055cf76ff30f7da6a0e8d1244d8d06691 |
| cloudrelay-imagegen.skill | 1.5.1 | 10 | 28933 | 32daf302fa70ce86b42e3444f23603df034a0f320c7a298bd6fb5d7d89507385 |

已真实下载并比对 GitHub API digest：
- 线上 v2.0.0 媒体包：ff81c762460d99843bc9583a3b95931f786d1524ea2d84bca9fa3d6268111ff2。
- 线上历史 v1.5.0 旧包：b791cfe73dd4e7d478e7504d489cdaa595fe1c092d8af4956d42ba90ef5f7d32。

最终新 updater 再次完成真实 v2.0.0 下载、SHA-256、tag/VERSION 和 staging 验证。两个本地候选尚未上传，没有真实 Release API digest；本地 SHA 与模拟 metadata 验证不能冒充已发布证明。

已解决的失败与完整原因：
- 初次缺 PyYAML 导致 ModuleNotFoundError；隔离安装 requirements-dev.txt 后通过。
- 初次以模块形式调用原视频脚本，其同目录 import 导致 ModuleNotFoundError；使用脚本入口并修正 CI 后通过。
- 新双轨测试两处误将生成的 legacy 源码当成由 apply 消费的 staging；finally 清理源码后导致 34 个连带错误。已改为一次性临时副本，并从记录基线和历史字节恢复十四文件。恢复后 runtime 包 SHA 与原候选相同；最终两平台均按十四文件前后 SHA 快照验证无变化。真实 key、用户输出和原始 fixture 未受影响。[完整失败日志](validation/00-test-isolation-failure.log)。
- 官方重载提示修正后，一项测试的三个 subtest 仍共用旧提示；已区分历史 fixture 和新媒体预期。历史代码未变。[完整失败日志](validation/00-reload-hint-expectation-failure.log)。
- 首次 WSL 准备阶段恰逢候选缺失，在产品测试前终止；最终隔离副本验证全通过，详情保留在 POSIX JSON。

## 4. 数据保留结果

全部验证使用临时安装树与合成凭据，逐字节确认 imagegen-api-key-gpt、imagegen-api-key-gemini、imagegen-api-key-grok、generated-images、generated-videos、自定义文件、外部凭据存储及相邻另一身份目录保持不变。
迁移测试禁止读取 key 内容及环境/注册表值；只根据名称与文件元数据报告 configured/unconfigured（已配置/未配置）的存在状态，不声称验证 key 有效。
无确认和 --dry-run 的完整临时目录树不变，包含普通 Python 调用不产生 pyc 的验证。
真实用户 key 未读取或修改；未移动或删除真实生成目录、用户文件或旧安装。

## 5. 发布状态

已按用户明确授权创建 CloudRelay-Code/cloudrelay-imagegen-skill；没有删除或重命名仓库，没有创建/修改/删除 Release 或 tag，没有 git commit 或 git push。HEAD 仍是初始提交，工作区变化全部未提交，见 [最终状态清单](git-status-final.txt)。
旧名 API 现已返回独立旧仓库，ID 为 1394481012，与媒体仓库 ID 1319256142 不同。旧仓库 branches=[]、releases=[]，latest 为 404；媒体 latest 仍是 v2.0.0 且仅包含媒体 asset。仓库名恢复已核验，但自动更新链仍缺代码和 Release。

用户第八阶段明确要求外部操作先列命令与影响、等待授权。用户回答“同意”后，已成功执行且仅执行以下建仓命令：

~~~powershell
gh repo create CloudRelay-Code/cloudrelay-imagegen-skill --public --description "Legacy CloudRelay ImageGen skill compatibility channel"
~~~

建仓已恢复旧名称；空仓库本身不能恢复更新。最新核验见 [建仓验证](repository-creation-2026-09-29.json)。独立旧树导出、commit、tag、push、双轨 Release 与上线验收仍未执行，具体命令见 [发布计划](release-plan.md)。随后用户已明确同意两轨源码/tag 推送及旧版 v1.5.1、新版 v2.0.1 Release 创建，无需再次申请该范围授权。独立旧源码 checkout、两份发行说明和迁移文档已准备；最终构建验证及上线验收记录会在执行后更新。

## 6. 用户侧迁移命令

旧 Skill 保留原目录、身份及图片用法，不把原始旧 updater 指向媒体目录。新 Skill 独立安装：

~~~bash
npx --yes skills add "https://github.com/CloudRelay-Code/cloudrelay-mediagen-skill" --skill "cloudrelay-mediagen" --global --yes
~~~

本地补丁尚未推送，远程命令当前安装线上内容。迁移检查可从本地已验证仓库直接运行；将 source/target 替换为实际、非链接、互不嵌套的 Skill 目录：

~~~powershell
python "E:\code\cloudrelay-mediagen-skill\scripts\migrate_from_imagegen.py" --source-dir "<实际旧目录>" --target-dir "<实际新目录>" --dry-run
python "E:\code\cloudrelay-mediagen-skill\scripts\migrate_from_imagegen.py" --source-dir "<实际旧目录>" --target-dir "<实际新目录>" --yes
~~~

如需检查本地新候选安装路径，可运行 python install.py --client codex --scope user --dry-run；不盲目使用 --force。已有外部 key 存储由两个 Skill 共用，不需要复制密钥；生成结果仍留原目录。

| Agent | reload / restart |
| --- | --- |
| Codex | 自动发现 Skill 变化；仍未出现时重启 Codex，不保证仅新建任务即可刷新 |
| Claude Code | 现有目录受监视；新增顶层目录后 /reload-skills，旧版本不支持时重启会话 |
| Gemini CLI | /skills reload，再用 /skills list 确认；不支持时重启 CLI |
| OpenClaw | watcher 更新后下一次 Agent turn 生效；关闭 watcher 时按现有部署方式重启 Gateway |
| Cursor | 重启 Cursor 后确认；依据官方启动时发现行为给出的操作建议 |

官方核对页面（2026-09-29）：[Codex](https://developers.openai.com/codex/skills/)、[Claude Code](https://code.claude.com/docs/en/skills)、[Gemini CLI](https://geminicli.com/docs/cli/skills/)、[OpenClaw](https://docs.openclaw.ai/tools/skills)、[Cursor](https://cursor.com/docs/context/skills)。未实际重启这些 Agent 或修改其配置。
