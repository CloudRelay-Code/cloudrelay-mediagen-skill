# 视频工作流

本地 CLI 依据用户提供的 `image_demo(1).py` 的视频协议实现；接口和参数尚未通过本次真实计费请求验证。每个新任务先使用 Grok 密钥调用 `GET /v1/media/models`，只选择 `kind=video`、`endpoint=grok_videos` 的模型。发现明确禁用 async 时停止。

## 选择模型与输入

视频使用现有 `CLOUDRELAY_GROK_API_KEY`，配置命令为 `python "<skill-directory>/scripts/configure_api_key.py" --provider grok`。不使用 GPT 或 Gemini 密钥作为备用。

默认优先选择实际发现的 `grok-imagine-video`；不存在时，仅在有唯一适用视频模型时采用它。多个候选时报告列表并让用户选择 `--model`。名称包含 `video-1.5` 的模型按参考脚本仅支持图生视频，必须提供首帧，不能用于文生视频、编辑或续写。显式指定的模型不可用时停止，不自动切换。

首帧支持本地 PNG/JPEG/WebP 或公开 HTTP(S) URL，源视频支持本地 MP4 或公开 HTTP(S) URL。本地媒体转成 data URL；公网 URL 交给上游读取，必须是用户授权使用的素材。参考脚本要求视频编码为 H.264/H.265/AV1；CLI 只校验 MP4 文件头，不验证编码。不要把网关受鉴权的成片 URL 当作公开素材传给上游，使用已下载的本地 MP4。

## 命令与参数

文生视频，默认 8 秒、480p、上游默认比例：

```text
python "<skill-directory>/scripts/generate_video.py" --prompt "海面日出，镜头缓慢前推" --duration 8 --resolution 720p --aspect-ratio 16:9 --output-dir "<workspace>/generated-videos"
```

图生视频，在生成命令中加入首帧：

```text
python "<skill-directory>/scripts/generate_video.py" --prompt "让图中的云缓慢飘动" --image "<absolute-path>/frame.png" --duration 5 --output-dir "<workspace>/generated-videos"
```

视频编辑：

```text
python "<skill-directory>/scripts/generate_video.py" --action edit --prompt "将场景改为冬季，保留镜头运动" --video "<absolute-path>/source.mp4" --output-dir "<workspace>/generated-videos"
```

视频续写，duration 是新增秒数，不是最终总时长：

```text
python "<skill-directory>/scripts/generate_video.py" --action extend --prompt "镜头继续向前，露出远处灯塔" --video "<absolute-path>/source.mp4" --duration 6 --output-dir "<workspace>/generated-videos"
```

- 生成时长 1–15 秒，默认 8 秒；续写新增 2–10 秒，默认 6 秒。
- 生成分辨率：480p / 720p / 1080p（1080p 取决于上游账号，未保证可用）。
- 生成比例：1:1 / 16:9 / 9:16 / 4:3 / 3:4 / 3:2 / 2:3；未提供则省略。
- 编辑不发送 duration；编辑和续写不发送 resolution、aspect_ratio、image，避免悄悄忽略用户参数。
- `--poll-timeout` 默认 900 秒，必须为有限正数。
- `--provider` 如提供只接受 grok；`--model` 可指定发现列表中的视频模型。

## 异步、续查和下载

| 操作 | 提交端点 |
|---|---|
| 文生/图生视频 | POST /v1/videos/generations |
| 编辑 | POST /v1/videos/edits |
| 续写 | POST /v1/videos/extensions |

提交取得 `request_id` 或 `id` 后，轮询 `GET /v1/videos/{request_id}`，支持 HTTP 202 进行中及 HTTP 200 终态。只有 `status=done` 且包含 `video.url` 才进入下载。失败、过期、取消均停止。网络或服务端临时轮询错误可继续查询原任务；401/403/429 立即停止。提交只执行一次。

超时、中断或下载失败后保留 ID，续查无需重新提交，也无需重新指定生成参数：

```text
python "<skill-directory>/scripts/generate_video.py" --request-id "<existing-id>" --output-dir "<workspace>/generated-videos" --poll-timeout 900
```

视频 URL 可以是相对网关路径。只向与 `https://cloudrelay.cn` 同源的地址携带 Grok Bearer 密钥；第三方成片地址不携带密钥，下载拒绝重定向，避免鉴权信息外泄。流式下载至临时文件，检查 MP4 文件头和可用的 Content-Length 后落盘。下载失败可用同一 ID 重试。

最后读取 `PARAMETERS_JSON`，报告模型、动作、有效请求参数、是否续写新增时长、ID、usage、保存路径。服务端报告的时长/分辨率与本地验证结果分开表述。CLI 验证了 MP4 容器头，不代表视频可播放或画面符合提示词；有播放/预览工具时检查成片，缺少工具时明确说明未完成视觉验证。恢复任务缺少的原始参数保持未知，不编造。
