# 开发维护笔记

这份笔记记录当前维护 `zotero-pdf2zh-next` 时比较容易踩坑的地方。README 面向使用者，这里面向开发和发版。

## 项目边界

当前仓库只保留两个运行面：

- `plugin/`：Zotero 插件，负责偏好页、右键菜单、任务面板、提交 PDF、导入结果。
- `server/`：本地 Python 服务，负责接收插件请求、准备工作目录、调用 `pdf2zh_next`、管理结果文件。

不要轻易恢复旧 runner、旧 server 或额外自动化路径。这个 fork 的维护目标是少入口、少分发差异、少隐式状态。

## Python 和服务端

Python 依赖和运行统一用 `uv`：

```bash
uv run --directory server python -m unittest discover -s tests
uv run --directory server zotero-pdf2zh-next
```

面向用户的服务端分发优先使用 `uv tool`，命令名和插件名保持一致：

```bash
uv tool install --python 3.13 zotero-pdf2zh-next
zotero-pdf2zh-next
```

服务端 Python 包发布到 PyPI，包名也是 `zotero-pdf2zh-next`。PyPI 发布从本地发版脚本执行，token 由 direnv 注入，不写进仓库：

```bash
# .envrc
export UV_PUBLISH_TOKEN="pypi-..."

direnv allow
scripts/publish-server-pypi.sh 5.2.3 --push
```

服务端入口在 `server/server.py`，真正把插件参数转换为 `pdf2zh_next` 参数的是 `server/pdf2zh_next_service.py`。

新增翻译选项时，通常要同时检查这些位置：

- 插件偏好默认值：`plugin/addon/prefs.js`
- 插件偏好类型：`plugin/typings/prefs.d.ts`
- 请求类型与请求体：`plugin/src/modules/pdf2zhTypes.ts`、`plugin/src/modules/pdf2zhHelper.ts`
- 配置检查请求：`plugin/src/modules/preferenceScript.ts`
- 服务端请求解析：`server/server.py`
- `pdf2zh_next` settings 映射：`server/pdf2zh_next_service.py`
- README 或 changelog 是否需要用户可见说明

参数命名可以在插件侧保持用户语义，例如 `disableTermExtraction`；服务端再映射到上游语义，例如 `no_auto_extract_glossary`。这样 UI 和上游参数不会互相污染。

## Zotero 插件偏好页

偏好页是 `plugin/addon/content/preferences.xhtml`，行为在 `plugin/src/modules/preferenceScript.ts`。

维护经验：

- 先保留 Zotero/XUL 能稳定工作的控件，不要为了视觉效果引入新的前端框架。
- 偏好页可能被多次加载，动态插入选项前要清理旧内容。例如语言下拉初始化前要 `replaceChildren()`，否则会重复堆叠。
- Zotero 的 XHTML 会被 Prettier 用比较奇怪的方式格式化，尤其是内联 CSS。优先保证构建和 lint 通过，不要在这个文件里追求普通网页项目的格式体验。
- 偏好页状态可以从 `plugin/package.json` 读取插件版本；服务端版本来自 `/health`。
- 连接检查按钮要防重复点击。失败时也要把页面上的服务端状态更新为不可连接，而不只是弹窗。

## 任务与文件

服务端会为每个任务创建独立 workspace，位置在 `server/translates/<task-id>/`。任务记录由 `server/task_manager.py` 管理。

维护任务逻辑时要注意：

- 重试任务需要清理旧输出，但不能删输入 PDF。
- 删除任务时要避免删除正在运行的 workspace。
- 任务持久化只恢复 completed/failed 这类稳定状态，running/queued 不能盲目恢复。
- 多输出模式下，结果文件必须按 `mono`、`dual` 区分，否则插件导入时会混乱。

## 测试与验证

常规验证：

```bash
pnpm --dir plugin build
uv run --directory server python -m unittest discover -s tests
git diff --check
```

只改插件脚本时，可以额外跑：

```bash
pnpm --dir plugin exec eslint src/modules/preferenceScript.ts
```

只改服务端请求解析或 `pdf2zh_next` 参数映射时，优先补小范围 unittest。不要为了验证翻译流程去依赖真实 LLM 调用；真实调用慢、贵、也不稳定。

## 发布与 changelog

发版前先写 `CHANGELOG.md`。发布脚本会检查是否存在对应版本章节：

```markdown
## v5.2.3 - YYYY-MM-DD

- ...
```

然后运行：

```bash
scripts/release.sh 5.2.3
```

脚本会做这些事：

- 同步 `plugin/package.json`、`server/pyproject.toml`、`server/server.py` 和 `server/uv.lock` 版本。
- 跑服务端测试和插件构建。
- 构建服务端 Python wheel/sdist，确保 PyPI 包可发布。
- 创建主仓库 release commit 并推送。
- 用 `CHANGELOG.md` 对应章节生成 GitHub Release notes。
- 上传 Zotero `.xpi` 和固定 `release/update.json`。
- 更新 Homebrew tap formula 并跑 brew 验证。

只发布 PyPI 服务端包时，使用 `scripts/publish-server-pypi.sh <version> --push`。这条路径只处理 `server/`，不构建也不发布 Zotero 插件。

Homebrew formula 必须继续 pin `python@3.13`。目前 `pdf2zh_next -> pydantic-core` 依赖链在 Python 3.14 上不应被假定可用。

## README 的边界

README 保持给使用者看的内容：

- 这个项目做什么。
- 和原项目有什么区别。
- 怎么安装插件。
- 怎么启动或更新服务端。
- 在 Zotero 里怎么用。

服务接口、请求体、内部任务结构、发布脚本细节都放到 `docs/` 或源码附近，不放 README。
