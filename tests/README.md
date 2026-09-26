# tests/

2026-09-24 ～ 09-27「日报改造」（阶段 2/3 + F/G + 粘连修复）期间写的验证与诊断脚本，
原先散在 `%TEMP%\stage3`，收进仓库以免随临时目录被清掉。

## 怎么跑

**从仓库根目录跑**，不要 `cd tests` —— 脚本里的 `scripts` / `data/` / `daily` / `samples`
一律相对仓库根解析。

```powershell
$env:PYTHONIOENCODING = "utf-8"    # 不设的话，Windows 上打印 emoji/中文会 UnicodeEncodeError
python tests/test_glue.py
```

不需要 `PYTHONPATH`：每个脚本自己做了 `sys.path.insert(0, "scripts")`。

**夹具走 `git show <rev>:<path>`，不读工作区文件。** 所以需要完整克隆
（`git clone` 默认就是；CI 用 `actions/checkout` 时要设 `fetch-depth: 0`）。
原因见下面「为什么钉 rev」。判定用退出码：非零 = 失败。

---

## Tests（5 个）

可重复跑、无副作用、与当前工作区内容无关。改完 `scripts/` 跑这一组。

| 脚本 | 覆盖什么 | 夹具 |
|---|---|---|
| `test_fg.py` | F1 索引式 show notes（`_gist` 截断/省略号/边界）+ G3 分级来源定位（`_finalize` 记账、30% 门限的 `_run_class` 行为） | `samples/digest-format-example.md` |
| `test_format.py` | 拆分往返（逐字节）+ 硬换行回流（宽 120/60/40）+ 对抗性模型输出（回声标题、模型自带备选标题、代码围栏、过时字段、行内加粗、超长标题、粘连）+ 星级解析 + 组装与解析器自检 + HTML 转义 + 音频兜底稿长度 | `samples/`（+ `_chat` 打桩） |
| `test_glue.py` | 拿 09-26 **真实粘连产物**验证行内拆分：恢复 15 条、4 条 `split_out`、标题精确回池、分级门限不被粘连误伤、坏粘连整行不动 | git `f246ecf6` |
| `test_preselect.py` | 预选确定性：论文池上限 `PAPER_CANDIDATES`、`news+projects == 25`、单源配额、实质内容下限、项目按今日 star 取前 5、HF 顺序保持、同输入字节一致 | git `f246ecf6`（6 天 raw.json）|
| `test_summarize_e2e.py` | `summarize()` 全链路，`_chat` 是唯一 HTTP 边界故被打桩：提示词构造、拆分、来源定位、组装、解析器自检、重试循环、账本。无网络、无 API key | `samples/` + git `f246ecf6` |

## Diagnostics（11 个）

只读诊断，不改仓库状态。**这些是历史脚本**：阈值、条数、rev 都写死在当时那次事件上。
**诊断新问题时请基于当前 rev 重写一份**，不要去放宽这里的夹具或参数 —— 夹具有意钉死，
放宽就等于把当时那个 bug 的证据删掉。

| 脚本 | 用途 | 夹具 / 跑法 |
|---|---|---|
| `verify_run.py` | 一次云运行的 5 项验收：条目数、是否还有粘连、拆出条目的下场、观察超限 N、来源定位失败率（阈值 30%） | **REV 必填**：`python tests/verify_run.py <DATE> <REV>` |
| `show_run_detail.py` | dump 某次运行的 warnings + 候选池账本 + `daily/*.md` 结构 + 备选段原文 | **REV 必填**：`python tests/show_run_detail.py <REV> [DATE]` |
| `merged_items.py` | 数「模型写了但 parser 没看见」的条数（每个正文里的 `- **` 算一条被吞） | 已钉 `4bda296c` / `f246ecf6`；可覆盖：`[DATE] [REV_BEFORE] [REV_AFTER]` |
| `report_run.py` | 改造前后对照：正文长度分布直方图、未定位来源率、候选池账本与告警 | 已钉 `4bda296c` → `f246ecf6` |
| `split_glued.py` | 把粘连正文按 `- **` 拆开，还原真实长度分布 | 已钉 `f246ecf6`；可覆盖：`[REV] [DATE]` |
| `diag_seg.py` | 逐段拆 09-26 论文段的粘连行，看每段能否 `_ITEM_RE` 命中、标题是否超 `MAX_TITLE_CHARS` | 钉 `f246ecf6` |
| `diag_titles.py` | 真实候选池标题长度分布（定 `MAX_TITLE_CHARS` 的依据），以及模型实际写出的标题长度 | 钉 `f246ecf6`（两个 raw.json + daily 全钉）|
| `check_prompts.py` | 渲染提示词与告警文案，抓「漏 f 前缀导致 `{total}` 原样发给模型」或数字没跟着改 | 无夹具，直接 import `summarize` |
| `dump_schema.py` | 打印 raw.json 的结构骨架（键/类型/大小），不 dump 内容 | `python tests/dump_schema.py [PATH]`，默认 `data/2026-09-26.raw.json` |
| `measure_shownotes.py` | show notes 长度：新格式样例 vs 历史各期 | `samples/` + **`daily/*.md`** —— 会随 `daily/` 内容变化而漂移 |
| `verify_commit_msg.py` | 一次性：检查某条 commit message 过 PowerShell 后没被搞坏（探针字符串写死） | 读 `git log -1` |

**没有写副作用**：这里没有会改 `docs/` / `daily/` / `data/` 的脚本。
唯一有写副作用的是 `tools/check_creator.py`（会重建 `docs/`），所以它不在 `tests/`。

## 为什么钉 rev

`data/*.raw.json` 是流水线的**实时输出**，`daily/*.md` 同理 —— 任何一次重跑都会覆盖它们。
2026-09-26 就踩过：云端重跑（`b3f4a7a2`）把粘连版换成了干净版，于是
「读工作区 `daily/2026-09-26.md`」的检查脚本会**静默失效** —— 断言仍通过，
但测的是一个从来没出过 bug 的文档。比直接报错更糟。

所以凡是拿历史产物当夹具的，一律 `git show <rev>:<path>`，rev 写死在脚本里。
要注意的推论：**夹具 rev 必须存在于克隆里**，浅克隆会直接报错（这是有意的，好过静默跑错）。
