# -*- coding: utf-8 -*-
"""Render the edited prompts and warning text, to catch a missed f-prefix
(which would ship a literal `{total}` to the model) or a stale number."""
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
