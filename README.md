# Parrot

把电脑正在播放的课程、视频或音频转成带时间戳的中英文文字，校对后导出 Markdown。第一版聚焦可靠的声音采集和转写。

前端：Vue 3 + TypeScript + Ant Design Vue。后端：Python + FastAPI + SQLite。识别在本地执行，无需 API Key。

## 已实现

- macOS 13+ 原生系统声音采集，耳机可用，无需虚拟声卡或麦克风。
- 导入本地音频或视频（如 MP3、WAV、M4A、FLAC、MP4、WebM；最大 2GB），通过 PyAV 解码。
- Whisper 中英文转写，Apple 芯片自动选择 MLX，其他设备选择 faster-whisper CPU int8。
- 静音过滤、专业术语提示、低置信度段落标记、边界上下文和重叠去除。
- 分段结果持续显示，停止后继续处理剩余音频。
- SQLite 持久保存、历史记录、逐段编辑、保留识别原文、点击时间戳回听。
- Markdown 预览、复制、导出，保存原始录音，服务重启后保留已写入的文字。

## 启动

需要 Python 3.9+（建议 3.11/3.12）、Node.js 20.19+ 或 22.12+。Mac 系统声音采集还需要 Xcode Command Line Tools（`xcode-select --install`）。Python 与 Node 均需本机安装。

首次安装：

```bash
bash scripts/setup.sh
```

启动：

```bash
bash scripts/start.sh
```

打开 <http://127.0.0.1:8765>。macOS 上也可双击 `start.command` 启动已安装的程序。按 Ctrl+C 停止服务。

1. 可以先点击「加载所选模型」下载模型。Large v3 约 3GB，首次下载依赖 Hugging Face 可访问，后端终端会显示进度。
2. 点击「开始聆听」，按 macOS 提示授权。在「系统设置 → 隐私与安全性 → 屏幕与系统音频录制」中允许启动后端的终端或应用，必要时重启该应用和后端。组件使用显示器过滤器来获取音频，**不保存屏幕画面**。
3. 等状态变为「正在聆听」后再播放课程。所有应用的系统声音都会被采集，学习时可关闭无关声音。
4. 点击停止，等剩余转写完成，再回听、校对并导出 Markdown。

浏览器刷新或关闭不会停止后台采集，重新打开页面可回到活动记录；请在界面点击停止，或在终端按 Ctrl+C。单次系统采集最长 8 小时。

Windows/Linux 当前支持文件导入，尚未实现系统回环采集。带 DRM 保护的媒体可能禁止采集。无音轨或不支持的文件会显示失败原因。

## 模型选择与准确率

默认采用 Whisper **large-v3**，优先识别质量。Apple 芯片使用 `mlx-community/whisper-large-v3-mlx`；CPU 使用 faster-whisper 的同系列模型。Turbo 更快，Small 更省内存，建议先用一小段真实课程比较。

默认语言为中文，可保留英文专业术语；英文课程选英语，不确定时选自动检测。人名和术语可填在专业术语框中。

Whisper 是分段识别，不是逐字实时字幕。通常积累 8–24 秒音频后开始输出一批文字，实际延迟还取决于模型、设备与课程内容；识别速度跟不上播放时会积压，停止采集后继续转写。长课程用磁盘保存待处理片段，避免将全部音频堆在内存。大模型需要数 GB 可用内存，建议 16GB 或以上设备。

没有模型可以保证零错误。背景音乐、口音、连读、专有名词和中英切换都可能影响准确率。「建议回听校对」是根据模型平均对数概率的启发式提示，**不是经过校准的准确率分数**，未标记的段落也可能有错误。当前未在你的实际课程上测定字错率。

## 数据与隐私

数据默认在项目 `.data/` 下：`soundscribe.db` 保存记录与文字，`<记录ID>/audio.wav` 保存 16kHz 单声道录音，`models/` 缓存模型。首次下载模型会联网，识别过程不上传音频。可通过 `PARROT_DATA` 环境变量更换目录，通过 `PARROT_PORT` 更换启动端口。请备份 `.data/` 以保留记录。

项目已更名为 Parrot。数据库沿用原文件名以保留历史记录，原有 `SOUNDSCRIBE_DATA` 和 `SOUNDSCRIBE_PORT` 环境变量继续兼容；同时设置时优先使用 `PARROT_` 配置。

服务仅绑定 `127.0.0.1`，无登录体系，供本机单用户使用；拒绝外站 Origin 和非本机 Host。不要直接暴露到公网。

## 开发与测试

```bash
.venv/bin/python -m pip install -r backend/requirements-dev.txt
.venv/bin/python -m pytest
.venv/bin/ruff check backend tests
```

开发时分两个终端运行：

```bash
.venv/bin/python -m uvicorn backend.main:app --host 127.0.0.1 --port 8765 --reload
```

```bash
cd frontend
npm run dev
```

前端访问 <http://127.0.0.1:5173>，Vite 代理 `/api` 到后端。生产构建：`cd frontend && npm run build`，重启后端后由 FastAPI 提供构建结果。接口文档：<http://127.0.0.1:8765/docs>。

仅预下载模型或验证一个文件：

```bash
.venv/bin/python -m backend.warmup --model large-v3
.venv/bin/python -m backend.warmup --audio /absolute/path/to/lesson.wav --language zh
```

自动测试覆盖真实解码与重采样、分段尾部、上下文去重、文件上传到持久化与导出的链路、文字校对、音频范围请求和异常。测试中的识别引擎使用替身，避免测试依赖大模型下载；真实识别质量须用课程样本另测。

系统音频权限和 Metal GPU 必须在普通本机进程中验证，某些受限沙箱无法访问 GPU 或系统录音权限。

前端构建工具的 `picomatch` 传递依赖目前有未提供补丁的 [ReDoS 公告](https://github.com/advisories/GHSA-fc5q-39xc-vjpf)，`npm audit` 仍会报告相关开发依赖。生产启动由 FastAPI 提供静态文件，不启动 Vite，也不接收用户提供的 glob 模式。TypeScript 固定在与 Vue 类型检查器兼容的 5.9 系列。

## 后续范围

当前导出的是可校对的转写原文。自动章节、关键截图、知识点总结与复习卡片尚未实现，后续可以在时间戳和原始录音基础上继续增加。

参考：[MLX Whisper](https://github.com/ml-explore/mlx-examples/tree/main/whisper)、[faster-whisper](https://github.com/SYSTRAN/faster-whisper)、[Apple ScreenCaptureKit](https://developer.apple.com/documentation/screencapturekit)。
