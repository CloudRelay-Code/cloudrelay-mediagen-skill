# 双轨发布执行记录与后续复核

日期：2026-09-29。ImageGen v1.5.1 和 MediaGen v2.0.1 已按用户明确授权完成源码/tag 推送及 Release 创建；真实线上验收已通过。以下发布命令记录已完成动作，不应重复创建仓库、tag 或 Release。

## 1. 已完成：恢复精确旧仓库名

2026-09-29 06:04:30 UTC，在用户授权后创建公开独立旧仓库，ID 为 1394481012，与媒体仓库 ID 1319256142 不同：

~~~powershell
gh repo create CloudRelay-Code/cloudrelay-imagegen-skill --public --description "Legacy CloudRelay ImageGen skill compatibility channel"
~~~

[建仓快照](repository-creation-2026-09-29.json) 中 branches=[]、releases=[]、latest=404 是当时的中间状态；随后独立源码及 v1.5.1 已发布，当前旧 latest 正常返回旧包。没有删除、重命名已有仓库或放宽旧 downloader URL 约束。

## 2. 已完成：旧轨 v1.5.1

独立 checkout 为 E:\code\cloudrelay-imagegen-skill。仅导出经过验证的旧树十四文件，并添加 .gitattributes/.gitignore；未复制媒体 runtime、fixtures、adapters、密钥或输出。旧树与 Git 索引字节核对通过，固定十文件构建包 SHA-256 为 32daf302fa70ce86b42e3444f23603df034a0f320c7a298bd6fb5d7d89507385。

构建和发布实际命令：

~~~powershell
$mediaRoot = 'E:\code\cloudrelay-mediagen-skill'
$legacyRoot = 'E:\code\cloudrelay-imagegen-skill'
python "$mediaRoot\tools\package_legacy_skill.py" --source-root $legacyRoot --output-dir "$mediaRoot\dist\legacy-release"
git -C $legacyRoot commit -m "fix: restore independent ImageGen 1.5.x compatibility channel"
git -C $legacyRoot tag -a v1.5.1 -m "CloudRelay ImageGen v1.5.1"
git -C $legacyRoot push --atomic origin HEAD:refs/heads/main refs/tags/v1.5.1
gh release create v1.5.1 "$mediaRoot\dist\legacy-release\cloudrelay-imagegen.skill" --repo CloudRelay-Code/cloudrelay-imagegen-skill --verify-tag --title "CloudRelay ImageGen Skill v1.5.1" --notes-file "$mediaRoot\docs\compatibility\release-notes-1.5.1.md" --latest
~~~

结果：实现 commit 6bffec0a23f8c00dd0ab7207940757d67906d530；annotated tag 对象 f7b6c6c0df77c22c8286d25aa9f3e1077c3dd688；Release ID 398878638；发布时间 06:36:38 UTC。[ImageGen v1.5.1](https://github.com/CloudRelay-Code/cloudrelay-imagegen-skill/releases/tag/v1.5.1) 仅含 cloudrelay-imagegen.skill，GitHub API digest 与上传前/真实下载哈希一致。

## 3. 已完成：媒体轨 v2.0.1

媒体仓库的所有实现、测试、adapters、CI 和发布前证据审阅后提交；Git 字节固定规则与索引重建包校验已通过。媒体 shell launcher 单独修正为 LF，并在原生 Ubuntu 执行 --help 成功。发布命令：

~~~powershell
git -C E:\code\cloudrelay-mediagen-skill commit -m "fix: isolate legacy and media release channels and add safe migration"
git -C E:\code\cloudrelay-mediagen-skill tag -a v2.0.1 -m "CloudRelay MediaGen v2.0.1"
git -C E:\code\cloudrelay-mediagen-skill push --atomic origin HEAD:refs/heads/main refs/tags/v2.0.1
gh release create v2.0.1 "E:\code\cloudrelay-mediagen-skill\dist\cloudrelay-mediagen.skill" --repo CloudRelay-Code/cloudrelay-mediagen-skill --verify-tag --title "CloudRelay MediaGen Skill v2.0.1" --notes-file "E:\code\cloudrelay-mediagen-skill\docs\compatibility\release-notes-2.0.1.md" --latest
~~~

结果：实现 commit e9212fc30f7f1e08eb8dfbccbb4bd48ab31981d7；annotated tag 对象 bde12ce991ede639c8f356667971cf347f8eda4e；Release ID 398879814；发布时间 06:38:53 UTC。[MediaGen v2.0.1](https://github.com/CloudRelay-Code/cloudrelay-mediagen-skill/releases/tag/v2.0.1) 仅含 cloudrelay-mediagen.skill，SHA-256 为 df076d9e7cec01028700b962066009a055cf76ff30f7da6a0e8d1244d8d06691。

没有 force push，没有移动或删除旧 tag，也没有修改历史 v1.5.0/v2.0.0 Release。完整命令、退出码与授权依据见 [发布动作](publication-actions.json) 和 [授权记录](publication-authorization.json)。

## 4. 已完成：真实线上验收与 CI

发布前 Windows 两轮主套件各 170/167 通过/3 平台跳过；原生 Linux 170/167/3，两平台跳过互补；视频 14 全通过；十七脚本编译、双包确定性构建、干净副本 quick_validate、Git 索引重建与双方 shell launcher 帮助均通过。

实际线上验收命令在媒体仓库执行：

~~~powershell
python tools/verify_release_channels.py --output docs/compatibility/published-release-verification.json --legacy-tag v1.5.1 --media-tag v2.0.1
~~~

[线上证据](published-release-verification.json) 的 status 为 passed，完成于 06:40:41 UTC。核验内容：独立仓库 ID、各自 latest、精确 asset URL、API digest/真实下载 SHA-256、VERSION/tag、frontmatter、文件清单，以及临时安装升级和数据保留。冻结的原始 v1.5.0 updater 实际访问旧 latest 和旧下载 URL，成功升级到 1.5.1；未模拟 metadata 或响应字节。

媒体演练使用发布的 2.0.1 runtime，在临时目录仅把 VERSION 设为 2.0.0 后执行更新。它证明当前媒体 updater 与真实通道兼容，不能证明原始历史媒体 2.0.0 updater 跨过 local-variant 保护；这类旧媒体安装应按迁移指南一次性重新安装当前 Skill。两条演练仅使用合成用户文件/凭据及临时目录，未读取真实密钥或调用生成 API。

发布提交的 main CI [36532153745](https://github.com/CloudRelay-Code/cloudrelay-mediagen-skill/actions/runs/36532153745) 和 tag CI [36532153783](https://github.com/CloudRelay-Code/cloudrelay-mediagen-skill/actions/runs/36532153783) 均成功，覆盖 Ubuntu/Windows × Python 3.10/3.12。详见 [CI 验证](ci-verification.json)。

日后只读复核可运行下列命令；它们无需再次发布：

~~~powershell
gh api repos/CloudRelay-Code/cloudrelay-imagegen-skill --jq .full_name
gh api repos/CloudRelay-Code/cloudrelay-imagegen-skill/releases/latest
gh api repos/CloudRelay-Code/cloudrelay-mediagen-skill/releases/latest
gh release view v1.5.1 --repo CloudRelay-Code/cloudrelay-imagegen-skill --json tagName,assets,url
gh release view v2.0.1 --repo CloudRelay-Code/cloudrelay-mediagen-skill --json tagName,assets,url
gh run view 36532153745 --repo CloudRelay-Code/cloudrelay-mediagen-skill
gh run view 36532153783 --repo CloudRelay-Code/cloudrelay-mediagen-skill
~~~

## 5. 证据归档

两个实现 commit、tag、Release 和线上验收均已完成。发布前后状态、操作命令、真实下载与升级验收、CI 记录均与本报告保存在 docs/compatibility。上述发布 commit 固定对应运行源码；后续文档证据更新不改变 Release tag 或包内容。无需重新打包、移动已发布 tag 或替换 Release asset。完整六部分用户报告见 [report.md](report.md)。
