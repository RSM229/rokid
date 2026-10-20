# -*- coding: utf-8 -*-
"""把监理通说明书 Markdown 转成自包含的阅读用 HTML。

只依赖标准库（本机隔离环境无外网，装不了 markdown 库）。
支持本项目说明书用到的语法：标题 / 表格 / 引用块（含彩色语义）/ 有序与无序列表 /
围栏代码块 / 水平线 / 粗体 / 行内代码 / 链接。
"""
import html
import re
import sys

SRC = r"D:\rokid\rokid_glass\rokid_glass\docs\监理通使用说明书-详细版-2026-10-12.md"
OUT = r"D:\rokid\rokid_glass\rokid_glass\docs\监理通使用说明书-详细版-2026-10-12.html"


def esc(t: str) -> str:
    return html.escape(t, quote=False)


def inline(t: str) -> str:
    """行内语法：行内代码 -> 粗体 -> 链接。行内代码先抽走，避免其中内容被二次解析。"""
    codes = []

    def _grab(m):
        codes.append(m.group(1))
        return "\x00%d\x00" % (len(codes) - 1)

    t = re.sub(r"`([^`]+)`", _grab, t)
    t = esc(t)
    t = re.sub(r"\*\*([^*]+)\*\*", r"<strong>\1</strong>", t)
    t = re.sub(r"\[([^\]]+)\]\(([^)]+)\)", r'<a href="\2">\1</a>', t)
    for i, c in enumerate(codes):
        t = t.replace("\x00%d\x00" % i, "<code>%s</code>" % esc(c))
    return t


def slugify(text: str, used: dict) -> str:
    s = re.sub(r"[^\w\u4e00-\u9fff]+", "-", text).strip("-").lower()
    s = s or "sec"
    base, k = s, 1
    while s in used:
        k += 1
        s = "%s-%d" % (base, k)
    used[s] = 1
    return s


def is_table_sep(line: str) -> bool:
    return bool(re.match(r"^\|[\s:\-|]+\|$", line.strip()))


def split_row(line: str):
    line = line.strip()
    if line.startswith("|"):
        line = line[1:]
    if line.endswith("|"):
        line = line[:-1]
    return [c.strip() for c in line.split("|")]


QUOTE_CLS = (("🔴", "q-danger"), ("⚠️", "q-warn"), ("💡", "q-tip"))


def main() -> int:
    with open(SRC, encoding="utf-8") as f:
        lines = f.read().split("\n")

    out = []
    toc = []
    used = {}
    i, n = 0, len(lines)
    in_list = None  # 'ul' | 'ol' | None

    def close_list():
        nonlocal in_list
        if in_list:
            out.append("</%s>" % in_list)
            in_list = None

    while i < n:
        raw = lines[i]
        s = raw.strip()

        # ---- 围栏代码块 ----
        if s.startswith("```"):
            close_list()
            lang = s[3:].strip()
            buf = []
            i += 1
            while i < n and not lines[i].strip().startswith("```"):
                buf.append(lines[i])
                i += 1
            i += 1
            cls = ' class="lang-%s"' % esc(lang) if lang else ""
            out.append("<pre><code%s>%s</code></pre>" % (cls, esc("\n".join(buf))))
            continue

        # ---- 空行 ----
        if not s:
            close_list()
            i += 1
            continue

        # ---- 水平线 ----
        if re.match(r"^-{3,}$", s):
            close_list()
            out.append("<hr>")
            i += 1
            continue

        # ---- 标题 ----
        m = re.match(r"^(#{1,6})\s+(.*)$", s)
        if m:
            close_list()
            level = len(m.group(1))
            text = m.group(2).strip()
            sid = slugify(text, used)
            out.append('<h%d id="%s">%s</h%d>' % (level, sid, inline(text), level))
            if level <= 2:
                toc.append((level, sid, re.sub(r"[*`]", "", text)))
            i += 1
            continue

        # ---- 表格 ----
        if s.startswith("|") and i + 1 < n and is_table_sep(lines[i + 1]):
            close_list()
            head = split_row(s)
            i += 2
            rows = []
            while i < n and lines[i].strip().startswith("|"):
                rows.append(split_row(lines[i]))
                i += 1
            t = ["<table><thead><tr>"]
            t += ["<th>%s</th>" % inline(c) for c in head]
            t.append("</tr></thead><tbody>")
            for r in rows:
                t.append("<tr>" + "".join("<td>%s</td>" % inline(c) for c in r) + "</tr>")
            t.append("</tbody></table>")
            out.append("".join(t))
            continue

        # ---- 引用块 ----
        if s.startswith(">"):
            close_list()
            buf = []
            while i < n and lines[i].strip().startswith(">"):
                buf.append(re.sub(r"^>\s?", "", lines[i].strip()))
                i += 1
            body = "\n".join(buf).strip()
            cls = ""
            for mark, c in QUOTE_CLS:
                if mark in body:
                    cls = " " + c
                    break
            inner = "".join("<p>%s</p>" % inline(x) for x in body.split("\n") if x.strip())
            out.append('<blockquote class="quote%s">%s</blockquote>' % (cls, inner))
            continue

        # ---- 列表 ----
        mo = re.match(r"^(\d+)\.\s+(.*)$", s)
        mu = re.match(r"^[-*]\s+(.*)$", s)
        if mo or mu:
            want = "ol" if mo else "ul"
            if in_list != want:
                close_list()
                out.append("<%s>" % want)
                in_list = want
            out.append("<li>%s</li>" % inline(mo.group(2) if mo else mu.group(1)))
            i += 1
            continue

        # ---- 段落 ----
        close_list()
        buf = [s]
        i += 1
        while i < n:
            nxt = lines[i].strip()
            if (not nxt or nxt.startswith("|") or nxt.startswith(">")
                    or nxt.startswith("#") or nxt.startswith("```")
                    or re.match(r"^[-*]\s", nxt) or re.match(r"^\d+\.\s", nxt)
                    or re.match(r"^-{3,}$", nxt)):
                break
            buf.append(nxt)
            i += 1
        out.append("<p>%s</p>" % inline("".join(buf)))

    close_list()

    # ---- 侧边目录 ----
    tparts = ['<nav class="toc"><div class="toc-title">目录</div><ul>']
    for level, sid, text in toc:
        tparts.append('<li class="l%d"><a href="#%s">%s</a></li>' % (level, sid, esc(text)))
    tparts.append("</ul></nav>")
    toc_html = "".join(tparts)

    page = TEMPLATE.replace("<!--TOC-->", toc_html).replace("<!--BODY-->", "\n".join(out))
    with open(OUT, "w", encoding="utf-8") as f:
        f.write(page)
    print("OK ->", OUT)
    print("headings:", len(toc), "blocks:", len(out))
    return 0


TEMPLATE = r"""<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>监理通（白泽）使用说明书 · 详细版</title>
<style>
:root{
  --ink:#1f2733; --ink-2:#4a5568; --line:#e3e8ef; --bg:#eef1f6; --card:#fff;
  --brand:#0a2e5c; --brand-2:#1a5fb4; --accent:#1890ff;
}
*{box-sizing:border-box}
body{
  margin:0; background:var(--bg); color:var(--ink);
  font-family:"Microsoft YaHei","PingFang SC","Hiragino Sans GB",system-ui,sans-serif;
  font-size:15px; line-height:1.85;
}
.wrap{display:flex; align-items:flex-start; max-width:1400px; margin:0 auto; padding:0 20px}
/* ---- 侧边目录 ---- */
.toc{
  position:sticky; top:0; align-self:flex-start; flex:0 0 268px;
  max-height:100vh; overflow-y:auto; padding:26px 14px 40px 0; font-size:13px;
}
.toc-title{font-weight:700; color:var(--brand); letter-spacing:.12em; margin:0 0 10px 10px; font-size:12px}
.toc ul{list-style:none; margin:0; padding:0; border-left:2px solid var(--line)}
.toc li a{
  display:block; padding:3px 10px; color:var(--ink-2); text-decoration:none;
  border-left:2px solid transparent; margin-left:-2px; line-height:1.5;
}
.toc li a:hover{color:var(--accent); background:#ffffffa8}
.toc li.l1 a{font-weight:700; color:var(--brand); margin-top:10px; font-size:13.5px}
.toc li.l2 a{padding-left:20px}
/* ---- 正文 ---- */
main{flex:1 1 auto; min-width:0; padding:26px 0 80px}
main>.hero, main>*{max-width:960px}
.card{background:var(--card); border:1px solid var(--line); border-radius:12px;
  padding:44px 52px; box-shadow:0 2px 18px #0a2e5c0f}
h1{font-size:27px; color:var(--brand); margin:52px 0 18px; padding-bottom:12px;
  border-bottom:3px solid var(--brand); letter-spacing:.01em}
h1:first-child{margin-top:6px}
h2{font-size:20px; color:var(--brand-2); margin:38px 0 14px; padding-left:12px;
  border-left:5px solid var(--accent)}
h3{font-size:17px; color:#243b53; margin:28px 0 10px}
h4{font-size:15.5px; color:#334e68; margin:22px 0 8px}
p{margin:10px 0}
strong{color:#0b2545}
a{color:var(--brand-2)}
code{background:#eef3fa; color:#b02a37; padding:1.5px 6px; border-radius:4px;
  font-family:Consolas,"Courier New",monospace; font-size:13px}
pre{background:#0f1c2e; color:#dbe6f4; padding:16px 18px; border-radius:9px; overflow:auto; margin:14px 0}
pre code{background:none; color:inherit; padding:0; font-size:13px; line-height:1.7}
hr{border:0; border-top:1px dashed #c3cdda; margin:34px 0}
/* ---- 表格 ---- */
table{width:100%; border-collapse:collapse; margin:16px 0; font-size:14px; background:#fff}
th{background:var(--brand); color:#fff; font-weight:600; text-align:left; padding:10px 12px;
  border:1px solid #0a2e5c; white-space:nowrap}
td{padding:9px 12px; border:1px solid var(--line); vertical-align:top}
tbody tr:nth-child(even){background:#f7f9fc}
tbody tr:hover{background:#eef5ff}
td code,th code{font-size:12.5px}
/* ---- 列表 ---- */
ul,ol{margin:10px 0; padding-left:26px}
li{margin:5px 0}
li>strong{color:#0b2545}
/* ---- 引用块（语义着色） ---- */
blockquote{margin:16px 0; padding:12px 18px; border-left:5px solid #94a3b8;
  background:#f6f8fb; border-radius:0 8px 8px 0}
blockquote p{margin:4px 0}
blockquote.q-danger{border-left-color:#e5484d; background:#fef2f2}
blockquote.q-warn{border-left-color:#f5a524; background:#fffaf0}
blockquote.q-tip{border-left-color:var(--accent); background:#f0f7ff}
/* ---- 响应式 ---- */
@media (max-width:920px){
  .wrap{display:block; padding:0 14px}
  .toc{position:static; max-height:none; flex:none; padding:20px 0 0}
  .toc ul{border-left:none; columns:2}
  .card{padding:26px 20px}
  h1{font-size:22px}
}
@media print{
  body{background:#fff; font-size:11.5pt}
  .toc{display:none}
  .card{border:0; box-shadow:none; padding:0}
  h1{page-break-before:auto}
  table,pre,blockquote{page-break-inside:avoid}
}
</style>
</head>
<body>
<div class="wrap">
<!--TOC-->
<main><div class="card">
<!--BODY-->
</div></main>
</div>
</body>
</html>
"""

if __name__ == "__main__":
    sys.exit(main())
