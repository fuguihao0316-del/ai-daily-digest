# tools/

放**有副作用**的运维脚本。只读的测试与诊断在 `tests/`（见 `tests/README.md`）。

从仓库根目录跑，并设 `$env:PYTHONIOENCODING = "utf-8"`。

| 脚本 | 用途 | 副作用 |
|---|---|---|
| `check_creator.py` | 量 creator 页面 show notes 的真实体积：重建站点后解析 `docs/creator.html`，再算一个「30 条正文全部撑到 400 字」的最坏情况 | **会重建 `docs/`**（内部调 `generate_site(root=Path("."))`）|

## check_creator.py

```powershell
$env:PYTHONIOENCODING = "utf-8"; python tools/check_creator.py
```

它读的是**重建后**的 `docs/creator.html`，不是仓库里那份 —— 所以：

- 跑之前先确认工作区的 `daily/` 是你想量的内容；
- 跑完 `git status` 会看到 `docs/` 一片改动，**别顺手提交**（除非你本来就要重建站点）；
- **不要放进 CI / pre-commit**，它会让 `docs/` 每次都被重写。
