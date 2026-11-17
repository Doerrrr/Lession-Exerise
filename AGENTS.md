# AGENTS.md · 听写本维护说明

这个文件给后续接手本项目的 AI / 开发者看。

## 最重要的规则：全部判卷走 API

- **从 v0.3.0 起，所有判卷都必须走应用内的「AI 判卷」**，调用 DeepSeek 或兼容的 `/chat/completions` API。
- **不再提供本地判卷**：没有本地匹配 CLI、没有 `tools/check.py`、AI 返回不完整时也不会偷偷改用本地匹配。
- 答题纸/答题包只作为备份；不要根据答题纸自行给出对错，也不要用本地规则写 `data/checks/`。
- 历史记录里旧的本地检查结果只作为旧数据保留；新结果一律由 API 产生。

## 项目结构

- `server.py` — 本地 HTTP 服务，默认 `127.0.0.1:8765`
- `dictation/` — 词库、种子抽样、答题纸、进度派生、AI 判卷
- `tools/build_words.py` — 从 ECDICT 构建 `data/words.json`
- `web/` — 原生 HTML/CSS/JS 前端
- `data/` — 全部用户数据；`data/checks/` 是学习进度的唯一事实来源
- `tests/` — `python -m unittest discover -s tests -v`

## 启动 / 检查服务

```bat
python server.py --open
```

或双击 `start.bat`。默认地址 `http://127.0.0.1:8765`；端口被占用时会顺延，以服务窗口打印的地址为准。

```powershell
Invoke-RestMethod http://127.0.0.1:8765/api/health
```

不要把服务改成监听 `0.0.0.0`，这是本机单用户工具。

## 用户一轮的流程

1. 用户在听写页输入中文意思；Enter/下一个会保存上一题并清空输入框。
2. 听写结束后，用户在答题页填写答案，点「AI 判卷」。
3. 应用调用 DeepSeek/兼容 API；检查记录写入 `data/checks/<id>.json`，进度由它派生。
4. 结果页可以每词发音、重听本轮、重听本轮错题。
5. 如果 API Key 未配置、网络失败或 AI 返回不完整，应用会报错；引导用户检查设置/重试，**不要本地判卷**。

## 规则与不变量

- **不要提前泄露答案**：`data/sessions/<id>.json` 含答案键；用户还没听写/填写时，不要把释义发到对话里。
- 新词本 = 所有未掌握词；错题本 = 未掌握且 `wrong_count > 0` 的词；连续答对阈值默认 5。
- 连续答对 5 次 → `mastered`，同时离开新词本和错题本。答错 → `streak=0`、`wrong_count+1`。
- 进度是从 `data/checks/*.json` 按场次创建时间派生出来的，**不要手写增量进度文件**。
- `data/resets.json` 记录单词重置时间；重置只影响此时间之前的检查记录。
- 只有实际播放过的题才应计入（`heard_count`）；答题纸里多出来的题号会被提示并忽略。
- 不要直接修改 `data/words.json`；需要换词库时运行 `tools/build_words.py`。
- 不要删除用户 `data/sessions/`、`data/checks/`、`data/wordbooks/`；「清空学习记录」应通过应用/API 归档。
- 真实音色 / 语音问题交给用户试听；不要声称你听过音频。无英文语音时引导用 Edge 或安装 Windows 英文语音。

## 自定义词本

- 固定格式见 `docs/WORDBOOK_FORMAT.md`；JSON 是主格式，CSV 是导入便利格式。
- 导入后保存在 `data/wordbooks/<id>.json`，属于个人数据；`.gitignore` 已排除，不要提交。
- 每个词本生成两个来源：`custom_new:<id>`（未掌握）和 `custom_all:<id>`（含已掌握）。
- 自定义词与内置词共用进度 / 错题本 / 已掌握；`data/books` 的 all 视图和全局错题本会包含自定义词。
- 导入接口：`POST /api/wordbooks/import`；删除：`POST /api/wordbooks/delete`；模板：`GET /api/wordbooks/template?format=json|csv`。
- 导入/删除后后端会 `reload_custom`；如果手工改文件，需要重启服务。
- 词本最多 10,000 词、2 MB；只收单个英文单词。

## AI 判卷（DeepSeek）

- 应用内点「AI 判卷」；检查记录带 `"engine": "deepseek"`、`model` 和每题 `reason`。
- API Key 存在 `data/secrets.json`，**永远不要打印、复制或发送这个文件的内容**。
- 判卷时会把本轮单词、参考释义和用户答案发送到用户配置的 DeepSeek / 兼容接口。
- **AI 没有返回完整结果时直接报错，不 fallback 到本地匹配**；由用户重试。
- 相关接口：`PUT /api/ai/settings`、`POST /api/ai/test`、`POST /api/sessions/<id>/ai-check`。
- 结果页「重听本轮错题」：`POST /api/sessions/<id>/retry-wrong`，从该轮检查记录里取 wrong/blank/uncertain 的词新建一轮 `source: retry_wrong` 的会话；不会改动原轮次。

## 常用测试

```bat
python -m unittest discover -s tests -v
```

覆盖：种子复现、词频前 N、答题纸往返、五连对、错题本派生、重置、API、AI 判卷、自定义词本。

## 注意

- `data/words.json` 由 ECDICT 构建（MIT，见 `THIRD_PARTY_NOTICES.md`），当前 5,802 词（四级 3,846、六级 5,406、六级新增 1,956）。
- 词频是 COCA `frq` 顺序，作为「听力词频」的近似；不是真题听力词频。
- 浏览器 TTS 需要页面保持前台；应用已做后台自动暂停。
- 蓝牙耳机第一词前可能还在激活：用户可打开「耳机保活（蓝牙预热）」，应用用极低音量静音音频保持通道；系统静音也有效。
- 服务是单用户本地工具，不需要登录和 CORS。
