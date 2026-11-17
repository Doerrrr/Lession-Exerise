# AGENTS.md · 听写本检查协议

这个文件给后续接手本项目的 AI / 开发者看。用户主要用它做英语单词听写，然后让 AI 检查答题纸。

## 项目结构

- `server.py` — 本地 HTTP 服务，默认 `127.0.0.1:8765`
- `dictation/` — 核心：词库、种子抽样、答题纸、匹配、进度派生、检查
- `tools/build_words.py` — 从 ECDICT 构建 `data/words.json`
- `tools/check.py` — 检查 CLI（AI 的主要入口）
- `web/` — 原生 HTML/CSS/JS 前端
- `data/` — 全部用户数据；`data/checks/` 是学习进度的唯一事实来源
- `tests/` — `python -m unittest discover -s tests -v`

## 启动 / 检查服务

```bat
python server.py --open
```

或双击 `start.bat`。默认地址 `http://127.0.0.1:8765`；端口被占用时会顺延，以服务窗口打印的地址为准。启动后验证：

```powershell
Invoke-RestMethod http://127.0.0.1:8765/api/health
```

不要把服务改成监听 `0.0.0.0`，这是本机单用户工具。

## 用户检查一轮的完整流程

1. 用户在听写页可以直接在输入框写中文意思；按 Enter / 下一个会自动把上一题保存到 `data/sessions/<id>-answers.md`，并清空输入框进入下一题。
2. 用户听完后，可以：
   - 点「AI 判卷」让 DeepSeek 直接判分（需要先在设置里配置 API Key）；
   - 或点「保存答题纸」/「导出答题包」，把 `检查 <场次ID>` 发给你。
3. 用户会发：`检查 <场次ID>`，或给你答题纸路径 / 种子 / 直接说「检查刚才那轮」。
4. 先找待检查项：

   ```bat
   python tools/check.py --pending
   ```

   也可以：

   ```bat
   python tools/check.py --session <id>          # 指定场次
   python tools/check.py --seed 483920            # 按种子找最近一轮
   ```

5. **先干跑，不写进度**：

   ```bat
   python tools/check.py --session <id>
   python tools/check.py --session <id> --all     # 想看全部题目时
   ```

   输出会列出错误、未作答和 `待确认`（相似度在 0.5 以上的近似答案）。
   自动匹配规则：命中任一义项（包含关系也算）判对；近似但不确定的判 `uncertain`；其余判错。词典可能缺同义词，所以**必须复核 `待确认` 项**。

6. 需要人工改判时用 `--override`（题号或单词均可，可重复）：

   ```bat
   python tools/check.py --session <id> --override 7=correct --override abandon=wrong
   ```

7. 正式写入（会更新新词本 / 错题本 / 已掌握）：

   ```bat
   python tools/check.py --session <id> --accept-uncertain=wrong --apply
   ```

   - `--accept-uncertain=wrong` 是保守做法；复核后确认对的用 `--override N=correct` 改判。
   - 有未作答时，`--apply` 需要 `--allow-blank`；只有在用户确实留空、且愿意按错题处理时才加。
   - 同一场次重复检查会覆盖 `data/checks/<id>.json`，不会重复累计连对。
   - 检查完成后会生成 `data/checks/<id>.report.md`。

8. 用中文回报用户：
   - 本轮题数、正确 / 错误 / 未作答、正确率
   - 每道错题的「单词 / 你的答案 / 参考释义」
   - 本轮新掌握的词
   - 新词本、错题本、已掌握的数量变化
   - 下一步建议（刷新页面、下一轮、错题本重听）

## 规则与不变量

- **不要提前泄露答案**：`data/sessions/<id>.json` 含答案键；用户还没听写/填写时，不要把释义发到对话里。
- 新词本 = 所有未掌握词；错题本 = 未掌握且 `wrong_count > 0` 的词；连续答对阈值默认 5（`data/settings.json`）。
- 连续答对 5 次 → `mastered`，同时离开新词本和错题本。答错 → `streak=0`、`wrong_count+1`。
- 进度是从 `data/checks/*.json` 按场次创建时间派生出来的，**不要手写增量进度文件**。
- `data/resets.json` 记录单词重置时间；重置只影响此时间之前的检查记录。
- 只有实际播放过的题才应计入（`heard_count`）；答题纸里多出来的题号会被提示并忽略。
- 不要直接修改 `data/words.json`；需要换词库时运行 `tools/build_words.py`。
- 不要删除用户 `data/sessions/` 或 `data/checks/`；「清空学习记录」应通过应用/API 归档。
- 真实音色 / 语音问题交给用户试听；不要声称你听过音频。无英文语音时引导用 Edge 或安装 Windows 英文语音。

## AI 判卷（DeepSeek）

- 应用内可直接调用 DeepSeek 判卷；检查记录会带 `"engine": "deepseek"`、`model` 和每题 `reason`。
- API Key 存在 `data/secrets.json`，**永远不要打印、复制或发送这个文件的内容**；不要把它写进对话、报告或日志。
- 判卷时会把本轮单词、参考释义和用户答案发送到用户配置的 DeepSeek / 兼容接口。
- 如果用户对 AI 判卷有疑问，用常规 `tools/check.py` 复合并用 `--override` 覆盖；检查文件会被安全替换。
- AI 返回 `uncertain` 时，派生进度按错题处理；提醒用户可人工复核。
- 相关接口：`PUT /api/ai/settings`、`POST /api/ai/test`、`POST /api/sessions/<id>/ai-check`。
- 不配置 API Key 也完全可用：导出答题包 + 你运行 CLI 复核。

## 常用测试

```bat
python -m unittest discover -s tests -v
```

覆盖：种子复现、词频前 N、答题纸往返、中文释义匹配、五连对、错题本派生、重置、API、静态页面。

## 注意

- `data/words.json` 由 ECDICT 构建（MIT，见 `THIRD_PARTY_NOTICES.md`），当前 5,802 词（四级 3,846、六级 5,406、六级新增 1,956）。
- 词频是 COCA `frq` 顺序，作为「听力词频」的近似；不是真题听力词频。
- 浏览器 TTS 需要页面保持前台；应用已做后台自动暂停。
- 服务是单用户本地工具，不需要登录和 CORS。
