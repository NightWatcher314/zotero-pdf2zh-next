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

`server/uv.lock` 是跨机器和 GitHub Actions 共用的可复现输入，registry 必须保持为公共 `https://pypi.org/simple`，不能提交 Host 本地镜像地址。需要重建 lock 时显式使用：

```bash
UV_DEFAULT_INDEX=https://pypi.org/simple uv --directory server lock
```

面向用户的服务端分发优先使用 `uv tool`，命令名和插件名保持一致：

```bash
uv tool install --python 3.13 zotero-pdf2zh-next
zotero-pdf2zh-next
```

服务端 Python 包发布到 PyPI，包名也是 `zotero-pdf2zh-next`。PyPI 发布由统一发版脚本执行；默认通过 GitHub Trusted Publisher，无需仓库 secret。也可以由 direnv 注入 token，token 不写进仓库：

```bash
# .envrc
export UV_PUBLISH_TOKEN="pypi-..."

direnv allow
scripts/release.sh 5.2.4
```

服务端入口在 `server/server.py`，真正把插件参数转换为 `pdf2zh_next` 参数的是 `server/pdf2zh_next_service.py`。

PyPI 包内置固定的 `pdf2zh-next`、BabelDOC 和 RapidOCR 核心快照，避免
PyPI 解析到未验证的新版本，也避免安装上游 Gradio/FastAPI 前端。来源、
版本、SHA 和许可证见 `server/THIRD_PARTY_NOTICES.md`。更新快照时运行：

```bash
uv run python scripts/vendor_pdf2zh_runtime.py
```

生成目录和许可证必须提交。生成后重建 `server/uv.lock`，再跑 wheel/sdist
检查和 fresh-wheel 安装 smoke；不能只改版本字符串。

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
pnpm --dir plugin install --frozen-lockfile
pnpm --dir plugin lint:check
pnpm --dir plugin build
UV_DEFAULT_INDEX="$(awk -F '"' '/^source = \{ registry = / { print $2; exit }' server/uv.lock)" \
  uv run --directory server --locked python -m unittest discover -s tests
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
- 使用本地 token 或 GitHub Trusted Publisher 发布服务端包到 PyPI。
- 确认 PyPI 已可查询该版本后，用 `CHANGELOG.md` 对应章节生成 GitHub Release notes。
- 上传 Zotero `.xpi` 和固定 `release/update.json`。
- 更新 Homebrew tap formula 并跑 brew 验证。

`plugin/pnpm-lock.yaml` 必须提交。CI 和发版都使用 `--frozen-lockfile`，依赖变化后要同步更新 lockfile。

`.github/workflows/publish-pypi.yml` 只接受手动指定的现有 tag，并从该 tag 构建。`scripts/release.sh` 在没有 `UV_PUBLISH_TOKEN` 时负责创建/校验 tag、触发该 workflow、等待 PyPI 可查询；不要直接从 `main` 构建旧版本。

PyPI Trusted Publisher 的绑定必须保持为：owner `NightWatcher314`、repository `zotero-pdf2zh-next`、workflow `publish-pypi.yml`、environment `pypi`。GitHub `pypi` environment 不保存 token；`id-token: write` 只授予发布 job。

同一次 release commit 的 GitHub Release 或 PyPI 其中一端已存在时，脚本会校验 tag/commit 和 PyPI wheel/sdist 后补齐缺失步骤。旧版本 backfill 必须从对应 tag 构建，不能从已前进的 `main` 直接重发。不要恢复单独的 tag 发布 workflow；`scripts/release.sh` 是唯一发版入口。

如果临时不想上传 PyPI，可以传 `--no-pypi`；默认发版会同时发布 XPI、PyPI 和 Homebrew。

Homebrew formula 必须继续 pin `python@3.13`。目前 `pdf2zh_next -> pydantic-core` 依赖链在 Python 3.14 上不应被假定可用。

### Homebrew bottle 发布与瘦身

Formula 的源码回退路径必须忽略用户或 Host 的 uv/pip 镜像配置，并显式使用公共 PyPI。否则 bottle 覆盖范围之外的机器仍可能被锁到私有镜像。

可以从 bottle 中删除依赖包里目录名恰好为 `test` 或 `tests` 的测试数据、Ruff 可执行文件，以及 `cv2/data`、`skimage/data` 示例数据。不要删除名为 `testing` 的目录：SciPy 导入会经过 `numpy.testing`，误删会让运行时 smoke 失败。裁剪后至少验证：

- `pdf2zh_next`、BabelDOC、RapidOCR、OpenCV、scikit-image 和 SciPy 可导入。
- RapidOCR 能完成模型加载 smoke。
- Formula test 确认被裁剪路径不存在。

`v5.3.0` 的等价 locked build 从 692180 KiB 降到 574520 KiB，减少约 114.9 MiB。不要把这个未压缩对比直接和 bottle 下载体积或 `brew` 的安装摘要混用。

CI job 成功不等于该平台一定生成了 bottle。例如 Intel macOS runner 可能因为某个依赖没有 bottle 而正常跳过构建。发布完成需要同时确认：

- workflow artifacts 确实包含目标平台的 `*.bottle.*`。
- tap `main` 的 Formula `bottle do` 已写入对应平台 SHA。
- GitHub bottle release 已有对应资产；本机验证时 `brew info --json=v2` 显示 `poured_from_bottle: true`。

当前发布覆盖 Apple Silicon Sonoma、Apple Silicon Tahoe 和 Linux x86_64。Intel macOS 回退到源码构建。

## README 的边界

README 保持给使用者看的内容：

- 这个项目做什么。
- 和原项目有什么区别。
- 怎么安装插件。
- 怎么启动或更新服务端。
- 在 Zotero 里怎么用。

服务接口、请求体、内部任务结构、发布脚本细节都放到 `docs/` 或源码附近，不放 README。
