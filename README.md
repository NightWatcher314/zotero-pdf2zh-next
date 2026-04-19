# zotero-pdf2zh-next

一个只保留两部分的精简项目：

- `plugin/`: Zotero 插件，负责提交 PDF、查看任务、导入结果
- `server/`: Python 服务，负责接收 PDF、调用 `pdf2zh_next`、返回结果

不再包含 automation、旧版 server、额外 runner 或 Docker 相关遗留。

这是一个基于上游 `guaguastandup/zotero-pdf2zh` 演化出来的精简 fork，当前维护者为 `NightWatcher`。

当前统一版本：

- `5.0.1`

## 环境要求

- Zotero 7
- Python 3.12+
- `uv`
- Node.js 20+
- `pnpm`

## 安装 Zotero 插件

如果你直接使用仓库里已经构建好的插件包：

1. 打开 Zotero。
2. 进入 `工具 -> 插件`。
3. 点击右上角齿轮图标。
4. 选择 `Install Add-on From File...`。
5. 选择 `plugin/build/zotero-pdf2zh-next.xpi`。
6. 重启 Zotero。

如果你要自己重新构建插件：

```bash
cd plugin
pnpm install
pnpm build
```

构建产物在：

- `plugin/build/zotero-pdf2zh-next.xpi`

## 启动服务

服务端依赖统一由 `uv` 管理，`pdf2zh_next` 作为 Python 包直接导入。

首次安装依赖：

```bash
cd server
uv sync
```

启动：

```bash
uv run --directory server zotero-pdf2zh-next
```

默认监听地址：

- `http://127.0.0.1:8890`

翻译任务的工作目录和产物统一写到：

- `server/translates/<task-id>/`

可选参数：

- `--host`: 默认 `127.0.0.1`
- `--port`: 默认 `8890`
- `--log-level`: 默认 `INFO`

可选环境变量：

- `PDF2ZH_HOST`: 默认 `127.0.0.1`
- `PDF2ZH_PORT`: 默认 `8890`
- `PDF2ZH_LOG_LEVEL`: 默认 `INFO`

例如：

```bash
uv run --directory server zotero-pdf2zh-next --host 0.0.0.0 --port 8890
```

或者：

```bash
PDF2ZH_LOG_LEVEL=INFO uv run --directory server zotero-pdf2zh-next
```

## Docker

仓库根目录提供了：

- `server/Dockerfile`
- `compose.yaml`

启动：

```bash
docker compose up --build -d
```

停止：

```bash
docker compose down
```

默认仍然监听：

- `http://127.0.0.1:8890`

如果要改宿主机端口：

```bash
PDF2ZH_PORT=8891 docker compose up --build -d
```

## Homebrew

新的 formula 名称是：

- `zotero-pdf2zh-next`

安装：

```bash
brew tap NightWatcher314/homebrew-formula
brew install zotero-pdf2zh-next
```

直接启动：

```bash
zotero-pdf2zh-next --host 127.0.0.1 --port 8890
```

作为后台服务启动：

```bash
brew services start zotero-pdf2zh-next
```

## Zotero 里怎么用

1. 打开 Zotero 设置里的 `zotero-pdf2zh-next`。
2. 把 `Python Server URL` 设为你的服务地址，比如 `http://127.0.0.1:8890`。
3. 选择翻译服务，并在下方配置对应的 LLM API。
4. 在 `Output PDFs` 里勾选你要的产物，可以选 `Chinese Only`、`Bilingual`，也可以两者同时选。
5. 在条目或 PDF 附件上右键，选择 `zotero-pdf2zh-next: Translate PDF`。

任务提交后，可以在右键菜单里打开 `zotero-pdf2zh-next: Task Manager`。

任务面板会显示：

- 当前有哪些翻译任务
- 当前阶段
- 阶段进度
- 总进度
- 结果导入状态
- 终止单个任务

## 服务接口

健康检查：

- `GET /health`

同步接口：

- `POST /translate`

任务接口：

- `POST /tasks`
- `GET /tasks`
- `GET /tasks/<task_id>`
- `POST /tasks/<task_id>/cancel`
- `GET /tasks/<task_id>/result?mode=mono|dual`

`/translate` 只接受单一 `outputMode`，适合简单调试。

`/tasks` 支持多输出模式，插件默认使用这一套接口。

## 任务请求体示例

```json
{
  "fileName": "paper.pdf",
  "fileContent": "data:application/pdf;base64,...",
  "sourceLang": "en",
  "targetLang": "zh-CN",
  "outputModes": ["mono", "dual"],
  "service": "openai",
  "qps": 10,
  "poolSize": 0,
  "skipLastPages": 0,
  "ocr": false,
  "autoOcr": true,
  "noWatermark": true,
  "fontFamily": "auto",
  "llm_api": {
    "model": "gpt-4o-mini",
    "apiKey": "sk-...",
    "apiUrl": "https://api.openai.com/v1",
    "extraData": {}
  }
}
```

## 日志

服务端会输出任务与翻译阶段日志，包括：

- 任务进入队列
- 当前阶段开始
- 阶段进度与总进度
- 输出文件就绪
- 取消请求
- 任务完成、失败或终止

## 开发验证

插件构建：

```bash
pnpm --dir plugin build
```

服务测试：

```bash
uv run --directory server python -m unittest discover -s tests
```

## License

本项目延续上游许可，采用 `AGPL-3.0-or-later` 发布，见 `LICENSE`。
