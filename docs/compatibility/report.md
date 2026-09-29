# CloudRelay 旧版兼容实施报告

日期：2026-09-29。工作目录：E:\code\cloudrelay-mediagen-skill。
ImageGen v1.5.1 与 MediaGen v2.0.1 均已发布；真实双通道验收于 06:40:41 UTC（北京时间 14:40:41）通过。本报告记录已执行结果，完整发布、验收与 CI 证据保存在本目录。

## 1. 兼容性结论

**旧版同身份更新已实现完整自动兼容：未经改写的历史 v1.5.0 更新器，已通过真实旧仓库 latest、精确旧 asset URL 和 GitHub digest，成功升级到 v1.5.1。** 新媒体通道独立发布 v2.0.1。ImageGen 到 MediaGen 的身份切换仍采用一次性显式安装与迁移，两种 Skill 可以并存。

上线验收确认旧仓库 ID 为 1394481012、媒体仓库 ID 为 1319256142；两个仓库的 latest 分别为 v1.5.1 和 v2.0.1，各自只含正确 asset。两包真实下载字节与 GitHub API SHA-256、最终本地包均一致。完整证据见 [线上验收](published-release-verification.json)。

验证边界：媒体更新演练使用已发布 v2.0.1 的运行代码，在临时副本中仅把 VERSION 标记设为 2.0.0，再从真实媒体通道更新到 2.0.1；没有执行原始历史 v2.0.0 更新器。因此仍携带 local-variant 禁用逻辑的原始媒体 2.0.0 副本，需要按第六节单独安装当前 MediaGen。不能把这次演练解释为历史媒体 2.0.0 自动升级已获证明。

第一阶段的只读调查发生在任何代码修改和建仓之前：初始工作区干净，HEAD 为 7bc30dc013325ce252897c2985ecf295b6e9509b；已读取状态、remote、最近五次提交、标签、真实 Release、旧地址重定向，以及原始 v1.5.0 三个 updater 文件和 builder。当时旧 Web 地址 301 到媒体仓库，旧 latest API 最终返回媒体 v2.0.0；历史 v1.5.0 asset 的 browser_download_url 也已变成媒体路径，真实历史 downloader 因固定旧仓库前缀而拒绝。调查确认恢复精确旧名称的前提成立，随后在用户授权后建仓、发布并验证，最终采用结论 A 的双轨架构。这些早期事实见 [调查记录](release-evidence-2026-09-29.json) 和 [建仓时快照](repository-creation-2026-09-29.json)，不代表当前仍有重定向或空 Release 状态。

历史 fixture 的 peeled commit 为 2f2d6a077dc2acda21fff2896f144a075d9a3f72，annotated tag 对象为 1a3d213f841cdb93131dfa42f5803473554f9f39；十二文件保持原始字节。旧 apply 不检查目标身份、旧 ZIP 只排除 symlink 等固有限制已如实测试记录，发布的 1.5.1 已补上相关保护。不要把原始旧更新器指向媒体目录。

## 2. 实施方案

| 轨道 | 独立仓库 | Skill 身份 / asset | 当前 latest |
| --- | --- | --- | --- |
| 旧图片 | [cloudrelay-imagegen-skill](https://github.com/CloudRelay-Code/cloudrelay-imagegen-skill) | cloudrelay-imagegen / cloudrelay-imagegen.skill | [v1.5.1](https://github.com/CloudRelay-Code/cloudrelay-imagegen-skill/releases/tag/v1.5.1) |
| 新媒体 | [cloudrelay-mediagen-skill](https://github.com/CloudRelay-Code/cloudrelay-mediagen-skill) | cloudrelay-mediagen / cloudrelay-mediagen.skill | [v2.0.1](https://github.com/CloudRelay-Code/cloudrelay-mediagen-skill/releases/tag/v2.0.1) |

主要文件及结果：

- scripts/_update_common.py、update.py、check_update.py：固定媒体仓库、asset 和身份；逐跳 HTTPS 检查、GitHub SHA-256、tag/VERSION 完整匹配（含 build metadata）、路径与普通文件检查、目标身份和降级校验。严格互斥保护替代旧 local-variant 一律停用逻辑，测试覆盖媒体通道正常查询及旧目标在网络前拒绝。
- scripts/migrate_from_imagegen.py：标准库、显式 source/target/dry-run/yes；默认不写，--dry-run 优先于 --yes。目标未安装时仅提示独立安装，合法媒体目标在 --yes 下独占创建元数据回执；不读取、复制或打印 key 内容。
- install.py：检查源/目标身份、预检路径冲突、原子替换管理文件，保留与管理文件存在 hard link 关系的用户文件。
- tools/package_skill.py：固定媒体十五文件清单；stage 校验仅对确切 VERSION=2.0.0 的已发布媒体包允许缺两项后加迁移文件，视频及其参考文档始终必需。
- legacy/cloudrelay-imagegen 与 tools/package_legacy_skill.py：独立旧身份源码、固定旧仓库与原始十文件清单，updater 只接受 1.5.x；图片生成器、凭据配置器等保留文件与历史字节一致。旧树已导出并提交到独立旧仓库，builder 不依赖动态身份切换或媒体 fallback。
- tools/verify_release_channels.py：真实公开 GET、独立临时安装、两个 latest、asset、digest、身份、版本及数据保留验收；不会调用生成 API、读取真实 key 或发布内容。
- 四套兼容测试与历史 fixture、README、SKILL、CHANGELOG、references/migration.md、Codex/Claude adapters 已完成；CI 使用 Ubuntu/Windows × Python 3.10/3.12，分别编译、构建并验证双包，无自动发布步骤。
- .gitattributes 固定 runtime、adapter 副本、旧源码和历史 fixture 的 Git 字节行为；媒体 update.sh 的物理换行单独修正为 LF。索引重建包、Windows 包与 Linux 包哈希一致，十二 fixture 保持原始字节，见 [Git 字节验证](git-byte-preservation.json)。

两个 archive 无法互相通过版本、身份或下载地址校验；没有用新身份伪装旧包，也没有放宽 URL 检查。

## 3. 测试结果

| 环境 | 主套件 | 通过 | 跳过 | 失败 / 错误 | 视频专项 |
| --- | ---: | ---: | ---: | ---: | --- |
| Windows，Python 3.12.14，两轮 | 170 / 轮 | 167 / 轮 | 3 / 轮 | 0 / 0 | 14 全通过 |
| 原生 Ubuntu WSL2 /tmp，Python 3.10.12 | 170 | 167 | 3 | 0 / 0 | 14 全通过 |

平台跳过项互补，全部 170 个主测试至少在一个平台实际通过，加上 14 个视频测试，去重覆盖 184 项。重复运行不累计为新测试。新增 test_legacy_compat.py 28 项、test_migration.py 37 项、test_release_channels.py 30 项、test_dual_track.py 29 项；覆盖真实历史协议、SHA-256 成败、精确旧 URL、身份互斥、版本、降级、视频清单、并存安装、共享外部 key、零写入 dry-run、凭据内容禁读、路径/链接/junction/FIFO、hard link、回滚、确定性双包及 build metadata。

Windows 三项跳过原因（均在 Linux 通过）：

1. test_provider_persistence_writes_outside_skill：POSIX secret-file persistence is not used on Windows。
2. test_native_posix_paths：Requires a native POSIX host; forward slashes on Windows are separate。
3. test_apply_rejects_special_destination_file：FIFO special files are POSIX-only。

Linux 三项跳过原因（均在 Windows 通过）：

1. test_legacy_builder_rejects_junction_before_source_read：Windows junction behavior。
2. test_native_windows_backslash_paths：Requires a native Windows host。
3. test_real_windows_junction_is_rejected：A real junction requires Windows。

完整实际命令、退出码和日志见 [Windows 验证](validation-summary.json) 与 [POSIX 验证](posix-validation.json)。核心复现命令在媒体仓库执行：

~~~powershell
$env:PATH='C:\Users\Administrator\.cache\codex-runtimes\codex-primary-runtime\dependencies\python;'+$env:PATH
$env:PYTHONDONTWRITEBYTECODE='1'
$env:PYTHONUTF8='1'
$compatDeps=Join-Path $env:TEMP 'cloudrelay-compat-validation-20260929'
python -m pip install --target $compatDeps -r requirements-dev.txt
$env:PYTHONPATH=$compatDeps
python tools/sync_adapters.py
python -m unittest discover -s tests -v
python -m py_compile scripts/configure_api_key.py scripts/generate_image.py scripts/generate_video.py scripts/migrate_from_imagegen.py scripts/_update_common.py scripts/check_update.py scripts/update.py install.py tools/package_skill.py tools/package_legacy_skill.py tools/sync_adapters.py tools/verify_release_channels.py legacy/cloudrelay-imagegen/scripts/configure_api_key.py legacy/cloudrelay-imagegen/scripts/generate_image.py legacy/cloudrelay-imagegen/scripts/_update_common.py legacy/cloudrelay-imagegen/scripts/check_update.py legacy/cloudrelay-imagegen/scripts/update.py
python tools/package_skill.py
python tools/package_legacy_skill.py
python tools/sync_adapters.py
python -m unittest discover -s tests -v
python scripts/test_video.py -v
python install.py --client all --dry-run
~~~

十七脚本编译成功；两包解压到独立干净临时目录后运行本机 skill-creator/scripts/quick_validate.py，均通过且身份正确，实际完整路径保存在上述 JSON。Windows bytecode 使用临时缓存；Linux 全部工作在隔离 /tmp 副本，已有 PyYAML 6.0.2，无主机配置变更。两个 `sh scripts/update.sh --help`（媒体与旧树）实际执行均退出 0。

GitHub Actions 的发布提交 main run [36532153745](https://github.com/CloudRelay-Code/cloudrelay-mediagen-skill/actions/runs/36532153745) 和 v2.0.1 tag run [36532153783](https://github.com/CloudRelay-Code/cloudrelay-mediagen-skill/actions/runs/36532153783) 均成功，覆盖四种系统/Python 组合。详情见 [CI 验证](ci-verification.json)。

最终包（本地、索引重建、Windows/Linux 构建、真实下载及 GitHub API digest 一致）：

| asset | VERSION | 文件数 | 字节 | SHA-256 |
| --- | --- | ---: | ---: | --- |
| cloudrelay-mediagen.skill | 2.0.1 | 15 | 45139 | df076d9e7cec01028700b962066009a055cf76ff30f7da6a0e8d1244d8d06691 |
| cloudrelay-imagegen.skill | 1.5.1 | 10 | 28933 | 32daf302fa70ce86b42e3444f23603df034a0f320c7a298bd6fb5d7d89507385 |

真实线上验收命令及证据：

~~~powershell
python tools/verify_release_channels.py --output docs/compatibility/published-release-verification.json --legacy-tag v1.5.1 --media-tag v2.0.1
~~~

该次验收全部通过。未经改写的 v1.5.0 updater 实际请求旧 latest 与 `https://github.com/CloudRelay-Code/cloudrelay-imagegen-skill/releases/download/v1.5.1/cloudrelay-imagegen.skill`，下载、校验、安装到 1.5.1 成功；媒体 updater 从独立新通道完成 2.0.1 安装。两条链均使用真实 HTTPS，未模拟 metadata 或 asset；媒体基线方法的限制见第一节。调查阶段另已核验历史 v2.0.0 digest `ff81c762460d99843bc9583a3b95931f786d1524ea2d84bca9fa3d6268111ff2` 与 v1.5.0 digest `b791cfe73dd4e7d478e7504d489cdaa595fe1c092d8af4956d42ba90ef5f7d32`。

已解决的失败与完整原因：

- 初次缺 PyYAML 导致 ModuleNotFoundError；在隔离目录安装 requirements-dev.txt 后通过。
- 初次按模块入口调用原视频测试，其同目录 import 导致 ModuleNotFoundError；改用脚本入口并修正 CI 后通过。
- 双轨测试两处误把生成的 legacy 源码当成 apply 消费的 stage，finally 清理源码后导致 34 个连带错误。已改为一次性测试副本，从记录基线和历史字节恢复十四文件；恢复后旧包 SHA 与原候选相同。最终两平台前后 SHA 快照均确认旧源未变。真实 key、用户输出和冻结 fixture 未受影响。[原始失败日志](validation/00-test-isolation-failure.log)。
- 官方 reload 提示修正后，一个测试的三个 subtest 仍共用旧提示；已分开历史 fixture 与当前媒体预期，历史字节未变。[原始失败日志](validation/00-reload-hint-expectation-failure.log)。
- 首次 WSL 复制恰逢上述候选缺失，在产品测试启动前停止；恢复后最终隔离验证全部通过，详细记录保留在 POSIX JSON。

没有未修复测试失败。

## 4. 数据保留结果

离线测试及真实线上升级均在临时安装树使用合成凭据，通过前后字节对比证明三个 imagegen-api-key-* 文件、generated-images、generated-videos、自定义文件、外部凭据存储及相邻另一身份目录保持不变。线上验收还限制写入临时目录、禁止凭据文件和注册表读取；旧版、新版 updater 均满足保留断言。

迁移仅检查环境变量名称和文件元数据，不读取、复制、打印或验证 key 内容；configured/unconfigured 只表示配置存在状态。无确认和 --dry-run 的整个测试树不变，包括普通 Python CLI 不创建 pyc。真实用户 key、生成目录、自定义文件和旧安装均未被读取、迁移、覆盖或删除。

## 5. 发布状态

用户已分别明确授权恢复旧仓库，以及两轨源码/tag 推送和 Release 创建。建仓、两次实现提交、两个 annotated tag、两次 main/tag 原子推送及两个 Release 创建均已成功。没有删除或重命名已有仓库，没有修改历史 v1.5.0/v2.0.0 Release 或 tag，也没有 force push。

| 项目 | ImageGen | MediaGen |
| --- | --- | --- |
| 仓库 ID | 1394481012 | 1319256142 |
| 已推送实现 commit | 6bffec0a23f8c00dd0ab7207940757d67906d530 | e9212fc30f7f1e08eb8dfbccbb4bd48ab31981d7 |
| tag | v1.5.1 | v2.0.1 |
| annotated tag 对象 | f7b6c6c0df77c22c8286d25aa9f3e1077c3dd688 | bde12ce991ede639c8f356667971cf347f8eda4e |
| Release ID | 398878638 | 398879814 |
| 发布时间 UTC | 2026-09-29 06:36:38 | 2026-09-29 06:38:53 |

实际发布命令、退出码、授权和时间见 [发布动作](publication-actions.json)、[授权记录](publication-authorization.json)、[执行记录与复核命令](release-plan.md)。两条真实 latest 均已返回自己的版本/asset，旧 latest 不再指向媒体包。

Release tag 固定在上述已经验证的实现提交。发布动作、线上验收和 CI 记录与本报告一并保存在 docs/compatibility；文档证据更新不改变已发布 runtime、tag 或 asset，也不需要重新打包。

## 6. 用户侧迁移命令

继续使用图片功能的旧用户保留 cloudrelay-imagegen 原目录，在自己的旧安装中更新；该历史路径已通过真实线上验收：

~~~powershell
python "<实际旧 Skill 目录>\scripts\update.py" --apply --yes
~~~

这会在旧身份内更新到 1.5.1。需要图片、视频、编辑及续写工作流时，单独安装已发布的新身份，两者可并存：

~~~bash
npx --yes skills add "https://github.com/CloudRelay-Code/cloudrelay-mediagen-skill" --skill "cloudrelay-mediagen" --global --yes
~~~

原始媒体 2.0.0 的 updater 若报告 local-variant，也使用上述安装命令获取当前媒体版，不依赖旧 updater 跨过其禁用逻辑。确认实际媒体目录为 2.0.1 或更新版本且包含迁移脚本，再用真实、互不嵌套且无链接/junction 的目录运行：

~~~powershell
python "<实际新 Skill 目录>\scripts\migrate_from_imagegen.py" --source-dir "<实际旧 Skill 目录>" --target-dir "<实际新 Skill 目录>" --dry-run
python "<实际新 Skill 目录>\scripts\migrate_from_imagegen.py" --source-dir "<实际旧 Skill 目录>" --target-dir "<实际新 Skill 目录>" --yes
~~~

安装工具若建立链接，先核实真实目录后显式提供安全路径。迁移只记录状态，不复制 key 或移动生成结果；两个 Skill 继续共用现有外部 key 存储。完整细节见 [迁移指南](../../references/migration.md)。

| Agent | reload / restart |
| --- | --- |
| Codex | 自动发现 Skill 变化；仍未出现时重启 Codex，不保证仅新建任务即可刷新 |
| Claude Code | 现有目录受监视；新增顶层目录后 /reload-skills，旧版本不支持时重启会话 |
| Gemini CLI | /skills reload，再用 /skills list 确认；不支持时重启 CLI |
| OpenClaw | watcher 更新后下一次 Agent turn 生效；关闭 watcher 时按现有部署方式重启 Gateway |
| Cursor | 重启 Cursor 后确认；依据官方启动时发现行为给出的操作建议 |

官方核对页面（2026-09-29）：[Codex](https://developers.openai.com/codex/skills/)、[Claude Code](https://code.claude.com/docs/en/skills)、[Gemini CLI](https://geminicli.com/docs/cli/skills/)、[OpenClaw](https://docs.openclaw.ai/tools/skills)、[Cursor](https://cursor.com/docs/context/skills)。未实际重启用户 Agent 或修改其配置。
