# -*- coding: utf-8 -*-
"""Render the edited prompts and warning text, to catch a missed f-prefix
(which would ship a literal `{total}` to the model) or a stale number — then
assert the alternate-length contract still holds. Non-zero exit on failure."""
import re
import sys

sys.stdout.reconfigure(encoding="utf-8")
sys.path.insert(0, "scripts")
import summarize as S  # noqa: E402

print("=== SYSTEM_PROMPT_OBSERVATION ===")
print(S.SYSTEM_PROMPT_OBSERVATION)
print(f"\nOBSERVATION_MAX_CHARS = {S.OBSERVATION_MAX_CHARS}")

cls = next(c for c in S.CLASSES if c["key"] == "papers")
blocks = [("候选：论文", [])]
prompt = S._class_user_prompt(cls, "（候选清单占位）", "2026-09-27", 15, 10, [])
print("\n=== _class_user_prompt（本板块要求段） ===")
print(prompt.split("【本板块要求】")[1].strip())

print("\n=== _retry_suffix（含 deficit） ===")
print(S._retry_suffix(["条目数为 9，少于下限 10"], 15, 10, produced=9))

print("\n=== 备选超长告警文案 ===")
items = [{"title": "t", "body": "正文" * 100, "stars": "", "source_line": "",
          "deprecated": False, "split_out": False}]
items = items * 15  # item 11-15 are alternates; body is 200 chars > 140
problems, warns = S._validate_class(items, 15, 10)
print(f"ALTERNATE_BODY = {S.ALTERNATE_BODY}")
for w in warns:
    if "备选" in w:
        print(f"  {w}")

bad = [p for p in (prompt + S.SYSTEM_PROMPT_OBSERVATION) if False]
print("\n=== 字面量检查 ===")
for label, text in (("_class_user_prompt", prompt),
                    ("SYSTEM_PROMPT_OBSERVATION", S.SYSTEM_PROMPT_OBSERVATION)):
    literal = [tok for tok in ("{total}", "{positives}", "{cls", "{alternates}")
               if tok in text]
    print(f"  {label}: 残留未插值占位符 = {literal or '无'}")

# ── 契约断言 ────────────────────────────────────────────────────────────────────
# The e2e stubs pin the model's *historical* output (321-343-char alternates), so
# they pass whatever the prompt says — they cannot guard this contract. These
# checks are what ties the prompt's stated ceiling to ALTERNATE_BODY.
print("\n=== 契约断言 ===")
FAILS = []


def check(cond, label):
    print(f"  {'ok  ' if cond else 'FAIL'} {label}")
    if not cond:
        FAILS.append(label)


check("最多不超过 120 字" in prompt, "备选规则写明「最多不超过 120 字」")
ceiling = re.search(r"最多不超过 (\d+) 字", prompt)
check(ceiling is not None and int(ceiling.group(1)) == S.ALTERNATE_BODY,
      f"提示词上限 == ALTERNATE_BODY（{S.ALTERNATE_BODY}）")
check(f"最多 {S.ALTERNATE_BODY} 字" in S._retry_suffix([], 15, 10),
      "重试后缀带同一上限（改一处漏一处就红）")

if FAILS:
    print(f"\n{len(FAILS)} 条契约断言失败")
    sys.exit(1)
print("\nCONTRACT CHECKS PASSED")
