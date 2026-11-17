# 词本导入格式 v1

听写本支持导入自己的单词表。格式固定为下面的 **JSON**，同时接受同样字段的 **CSV**。导入后词本会出现在「听写 → 词库来源 → 自定义词本」。

设置页可以下载 JSON / CSV 模板，也可以直接照着本文件写。

## JSON（推荐，完整格式）

```json
{
  "format": "dictation-wordbook",
  "version": 1,
  "name": "我的新词本",
  "description": "可选说明，例如：考研核心词",
  "words": [
    {
      "word": "abandon",
      "translation": "放弃；抛弃",
      "phonetic": "ə'bændən",
      "frequency": 1000
    },
    {
      "word": "ability",
      "translation": "能力；才干",
      "phonetic": "ə'biliti",
      "frequency": 800
    }
  ]
}
```

### 字段说明

| 字段 | 必填 | 说明 |
|------|------|------|
| `format` | 是 | 固定为 `dictation-wordbook` |
| `version` | 是 | 固定为 `1` |
| `name` | 是 | 词本名称，最多 60 字 |
| `description` | 否 | 说明文字 |
| `words` | 是 | 非空数组，最多 10,000 个词 |
| `words[].word` | 是 | 单个英文单词；只允许字母、`-`、`'`、`’`；不接受词组 |
| `words[].translation` | 是 | 中文释义；多个义项用 `；` / `;` / `,` 分隔 |
| `words[].phonetic` | 否 | 音标或读音提示，仅展示 |
| `words[].frequency` | 否 | 词频，越小越常用；省略或 0 表示未知，排序时放最后 |

规则：

- 文件必须是 UTF-8，最多 2 MB。
- 同一个单词（不区分大小写）只保留最后一条。
- 至少要有 1 个有效单词。
- 只做单词听写，不收多词短语；短语请拆成单词或另作自定义扩展。
- `name` 也可以留空，改用设置页「词本名称」或文件名。

## CSV（方便 Excel 编辑）

```csv
word,translation,phonetic,frequency
abandon,放弃；抛弃,ə'bændən,1000
ability,能力；才干,ə'biliti,800
apple,苹果；苹果树,'æpl,1500
```

- 必须有表头 `word,translation`；`phonetic`、`frequency` 可选。
- 逗号分隔；释义里有逗号时用双引号包起来，例如 `"放弃, 抛弃"`。
- 编码 UTF-8；最多 2 MB；最多 10,000 行。
- CSV 没有 `name` 字段，名称用设置页的「词本名称」或文件名。

## 导入后

- 词本会保存到 `data/wordbooks/<词本ID>.json`（个人数据，不进入 Git）。
- 听写来源里会出现两个入口：
  - `自定义·<名称>·新词本`：只含未掌握的词；
  - `自定义·<名称>·全部`：包含已掌握的词，用于总复习。
- 自定义词和内置词共用连续答对 / 错题本 / 已掌握规则；答错会进入全局错题本。
- 删除词本不会删除历史听写记录，但该词本会从来源和浏览中移除。

## 版本

- 当前格式版本：`1`。
- 以后如果改格式，会提升 `version` 并在本文件说明兼容规则；旧版本导入会被拒绝并提示原因。
