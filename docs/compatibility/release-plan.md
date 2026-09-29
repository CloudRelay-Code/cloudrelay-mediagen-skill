# 双轨发布执行记录：源码、tag 与 Release 已获授权

截至 2026-09-29，本地候选为 MediaGen 2.0.1 和 ImageGen 1.5.1。独立旧仓库已于 2026-09-29 06:04:30 UTC 创建；目前无分支或 Release，本地测试成功不代表线上双轨已恢复。
建仓已完成。2026-09-29，用户另行明确同意两轨源码和对应 tag 推送，以及旧版 v1.5.1、新版 v2.0.1 Release 创建；该授权记录见 publication-authorization.json。以下步骤在最终测试通过后执行，不修改现有 Release/tag，不使用 force push。

## 1. 已完成：恢复精确旧仓库名

~~~powershell
gh repo create CloudRelay-Code/cloudrelay-imagegen-skill --public --description "Legacy CloudRelay ImageGen skill compatibility channel"
~~~

影响：创建公开仓库，占用旧名称，改变目前指向媒体仓库的重定向。空仓库没有 releases/latest，因此仅建仓不能修复自动更新。已有旧链接可能转为新仓库中的不存在资源，须在发布并验证旧通道后才宣称恢复。
这一步已获用户“同意”并执行成功；无需重复建仓。仓库 ID 1394481012，branches=[]、releases=[]、latest=404，见 repository-creation-2026-09-29.json。

## 2. 已授权：旧轨 main/tag 推送与 1.5.1 Release

独立 checkout 已从精确旧仓库创建于 E:\code\cloudrelay-imagegen-skill；十四文件与 legacy 源一致，另含安全 .gitignore。以下 clone/copy 命令仅记录已完成的准备，不重复执行；从最终测试和 commit 步骤继续。

~~~powershell
$mediaRoot = 'E:\code\cloudrelay-mediagen-skill'
$legacyRoot = 'E:\code\cloudrelay-imagegen-skill'
if (Test-Path -LiteralPath $legacyRoot) { throw 'Review the existing destination before continuing.' }
gh repo clone CloudRelay-Code/cloudrelay-imagegen-skill $legacyRoot
~~~

仅将已验证独立 legacy 源中的以下十四文件复制到新 checkout 对应相对路径；不复制媒体根目录、fixtures、adapters、key 或生成结果：

~~~powershell
$legacyFiles = @('SKILL.md','VERSION','agents/openai.yaml','scripts/configure_api_key.py','scripts/generate_image.py','scripts/_update_common.py','scripts/check_update.py','scripts/update.py','scripts/update.ps1','scripts/update.sh','LICENSE','README.md','CHANGELOG.md','PROVENANCE.json')
foreach ($relative in $legacyFiles) {
    $sourcePath = Join-Path (Join-Path $mediaRoot 'legacy/cloudrelay-imagegen') $relative
    $destinationPath = Join-Path $legacyRoot $relative
    New-Item -ItemType Directory -Path (Split-Path -Parent $destinationPath) -Force | Out-Null
    Copy-Item -LiteralPath $sourcePath -Destination $destinationPath
}
python "$mediaRoot\tools\package_legacy_skill.py" --source-root $legacyRoot --output-dir "$mediaRoot\dist\legacy-release"
Get-FileHash -Algorithm SHA256 "$mediaRoot\dist\legacy-release\cloudrelay-imagegen.skill"
git -C $legacyRoot status --short
git -C $legacyRoot remote -v
git -C $legacyRoot add -- .gitignore SKILL.md VERSION agents scripts LICENSE README.md CHANGELOG.md PROVENANCE.json
git -C $legacyRoot commit -m "fix: restore independent ImageGen 1.5.x compatibility channel"
git -C $legacyRoot tag -a v1.5.1 -m "CloudRelay ImageGen v1.5.1"
git -C $legacyRoot push origin HEAD:refs/heads/main
git -C $legacyRoot push origin v1.5.1
gh release create v1.5.1 "$mediaRoot\dist\legacy-release\cloudrelay-imagegen.skill" --repo CloudRelay-Code/cloudrelay-imagegen-skill --verify-tag --title "CloudRelay ImageGen Skill v1.5.1" --notes-file "$mediaRoot\docs\compatibility\release-notes-1.5.1.md" --latest
~~~

发布说明已改成发行版本描述，并清楚区分离线测试与待执行的线上验收；不会预先声称线上测试成功。核对导出包 SHA-256 与最终 validation-summary.json 的旧包相同。

## 3. 已授权：媒体轨 commit/push/tag/Release

在 E:\code\cloudrelay-mediagen-skill 审阅 git-status-final.txt 中所有变化，再执行下面已授权命令。GitHub Actions 只验证，不自动发布。

~~~powershell
git add -- .github/workflows/validate.yml CHANGELOG.md README.md SKILL.md VERSION install.py scripts/_update_common.py scripts/check_update.py scripts/update.py scripts/migrate_from_imagegen.py scripts/test_video.py references/migration.md tools/package_skill.py tools/package_legacy_skill.py tools/verify_release_channels.py tools/sync_adapters.py tests legacy adapters docs/compatibility
git commit -m "fix: isolate legacy and media release channels and add safe migration"
git tag -a v2.0.1 -m "CloudRelay MediaGen v2.0.1"
git push origin HEAD
git push origin v2.0.1
gh release create v2.0.1 dist/cloudrelay-mediagen.skill --repo CloudRelay-Code/cloudrelay-mediagen-skill --verify-tag --title "CloudRelay MediaGen Skill v2.0.1" --notes-file docs/compatibility/release-notes-2.0.1.md --latest
~~~

推送前核实实际分支和 remote，不使用 force，不修改现有 v2.0.0 或 v1.5.0 Release/tag。

## 4. 何时才能声称完整自动兼容

~~~powershell
gh api repos/CloudRelay-Code/cloudrelay-imagegen-skill --jq .full_name
gh release list --repo CloudRelay-Code/cloudrelay-imagegen-skill
gh api repos/CloudRelay-Code/cloudrelay-imagegen-skill/releases/latest
gh api repos/CloudRelay-Code/cloudrelay-mediagen-skill/releases/latest
gh release view v1.5.1 --repo CloudRelay-Code/cloudrelay-imagegen-skill --json tagName,assets,url
gh release view v2.0.1 --repo CloudRelay-Code/cloudrelay-mediagen-skill --json tagName,assets,url
~~~

旧 full_name 必须精确是旧仓库；两个 latest 分别返回自己的身份、asset 和版本通道。分别下载实际 asset，校验真实 API digest、VERSION/tag、路径与 frontmatter。最后用固定未改写的 v1.5.0 更新器对真实旧 latest 完成临时目录升级，并再次逐字节核验 key、生成目录、自定义文件和相邻媒体目录保留。这里的真实上线验证尚未执行。
