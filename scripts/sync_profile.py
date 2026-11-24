#!/usr/bin/env python3
"""同步 GitHub 个人主页的项目卡与统计面板。

数据全部来自 GitHub REST API（公开仓库），不写死任何项目信息。
产物：assets/project-<slug>-(dark|light).svg、assets/stats-(dark|light).svg、
assets/projects.json（清单），并重写 README 中标记区块内的内容。

规则见 scripts/profile_config.json。
"""

from __future__ import annotations

import hashlib
import html
import json
import os
import pathlib
import sys
import urllib.error
import urllib.request
from collections import Counter

ROOT = pathlib.Path(__file__).resolve().parents[1]
ASSETS = ROOT / "assets"
README = ROOT / "README.md"
CONFIG = ROOT / "scripts" / "profile_config.json"
MANIFEST = ASSETS / "projects.json"

PROJECT_START, PROJECT_END = "<!-- PROJECTS:START -->", "<!-- PROJECTS:END -->"
STATS_START, STATS_END = "<!-- STATS:START -->", "<!-- STATS:END -->"

MONO = "'SFMono-Regular', Consolas, 'Liberation Mono', Menlo, monospace"
CN = "'PingFang SC', 'Hiragino Sans GB', 'Microsoft YaHei', sans-serif"

PAL = {
    "dark": dict(
        bg="#060A12", panel="#0A1120", panel2="#0C1524",
        ink="#EAF2FF", body="#B8C8DA", muted="#8FA3B8", dim="#6C8199",
        line="#22D3EE", line2="#8B5CF6", border="#1C2B3D",
    ),
    "light": dict(
        bg="#F4F7FB", panel="#FFFFFF", panel2="#F7FAFE",
        ink="#0B1220", body="#3D4E63", muted="#5A6D84", dim="#5F7188",
        line="#0E7490", line2="#6D28D9", border="#D7E2EF",
    ),
}

CSS = """
.led{animation:led 2.8s ease-in-out infinite}
@keyframes led{0%,100%{opacity:1}50%{opacity:.25}}
"""


def esc(value: object) -> str:
    return html.escape(str(value), quote=True)


def frame(w: float, h: float, c: float = 12, i: float = 0.5) -> str:
    x0, y0, x1, y1 = i, i, w - i, h - i
    c = min(c, (x1 - x0) / 2, (y1 - y0) / 2)
    return (f"M{x0+c:.1f} {y0:.1f}H{x1-c:.1f}L{x1:.1f} {y0+c:.1f}"
            f"V{y1-c:.1f}L{x1-c:.1f} {y1:.1f}H{x0+c:.1f}L{x0:.1f} {y1-c:.1f}"
            f"V{y0+c:.1f}Z")


def svg(w: int, h: int, body: str, title: str, desc: str) -> str:
    return (f'<svg xmlns="http://www.w3.org/2000/svg" width="{w}" height="{h}" '
            f'viewBox="0 0 {w} {h}" fill="none" role="img" aria-labelledby="t d">\n'
            f'  <title id="t">{esc(title)}</title>\n  <desc id="d">{esc(desc)}</desc>\n'
            f'  <style>{CSS}</style>\n{body}\n</svg>\n')


# ------------------------------------------------------------------ projects
def slugify(name: str) -> str:
    slug = "".join(ch if ch.isalnum() else "-" for ch in name.lower())
    return "-".join(part for part in slug.split("-") if part)


def truncate(text: str, max_width: float, size: float) -> str:
    """按估算宽度截断，中文字符按 1em、西文按 0.55em 计算。"""
    width, out = 0.0, []
    for ch in text:
        w = size if ord(ch) > 0x2E80 else size * 0.55
        if width + w > max_width:
            return "".join(out).rstrip() + "…"
        width += w
        out.append(ch)
    return text


def chips_for(repo: dict) -> list[str]:
    chips: list[str] = []
    lang = repo.get("language")
    if lang:
        chips.append(lang.upper())
    for topic in repo.get("topics") or []:
        label = topic.replace("-", " ").upper()[:14]
        if label and label not in chips:
            chips.append(label)
        if len(chips) == 3:
            break
    if len(chips) < 3 and "PUBLIC" not in chips:
        chips.append("PUBLIC")
    return chips[:3]


def card_svg(theme: str, idx: int, repo: dict) -> str:
    """项目卡：1012 宽三栏（身份 / 摘要与标签 / 元数据），与统计面板共用 352·684 分栏。"""
    p = PAL[theme]
    w, h = 1012, 176
    name = truncate(repo["name"], 292, 26)
    raw_desc = (repo.get("description") or "").strip()
    desc = truncate(raw_desc, 284, 13) if raw_desc else "Public repository."
    updated = str(repo.get("pushed_at") or "")[:7].replace("-", ".")
    stars = int(repo.get("stargazers_count") or 0)
    lang = (repo.get("language") or "N/A").upper()
    url = str(repo.get("html_url") or "").replace("https://", "")
    chips = chips_for(repo)

    a = [f'<defs>'
         f'<linearGradient id="cb" x1="0" y1="0" x2="0" y2="{h}" gradientUnits="userSpaceOnUse">'
         f'<stop offset="0" stop-color="{p["panel2"]}"/><stop offset="1" stop-color="{p["panel"]}"/></linearGradient>'
         f'<linearGradient id="ca" x1="0" y1="0" x2="0" y2="{h}" gradientUnits="userSpaceOnUse">'
         f'<stop offset="0" stop-color="{p["line"]}"/><stop offset="1" stop-color="{p["line2"]}"/></linearGradient>'
         f'<clipPath id="cc"><path d="{frame(w, h)}"/></clipPath></defs>']
    a.append('<g clip-path="url(#cc)">')
    a.append(f'<rect width="{w}" height="{h}" fill="url(#cb)"/>')
    a.append(f'<rect x="0" y="32" width="3" height="{h-64}" fill="url(#ca)"/>')
    a.append(f'<path d="M352 32V152" stroke="{p["border"]}"/>')
    a.append(f'<path d="M684 32V152" stroke="{p["border"]}"/>')

    a.append(f'<text x="32" y="44" font-family="{MONO}" font-size="10" letter-spacing="2.4" '
             f'fill="{p["dim"]}">PROJECT // {idx:02d}</text>')
    a.append(f'<text x="32" y="92" font-family="{MONO}" font-size="26" font-weight="700" '
             f'letter-spacing="1.2" fill="{p["ink"]}">{esc(name)}</text>')
    a.append(f'<text x="32" y="126" font-family="{MONO}" font-size="10" letter-spacing=".4" '
             f'fill="{p["dim"]}">{esc(url)}</text>')

    a.append(f'<text x="380" y="92" font-family="{CN}" font-size="13" letter-spacing=".3" '
             f'fill="{p["body"]}">{esc(desc)}</text>')
    x = 380
    for chip in chips:
        cw = len(chip) * 7.2 + 20
        a.append(f'<path d="{frame(cw, 24, 4, 0)}" transform="translate({x} 116)" '
                 f'fill="{p["line"]}" fill-opacity=".05" stroke="{p["line"]}" stroke-opacity=".28"/>')
        a.append(f'<text x="{x + cw/2:.1f}" y="132" font-family="{MONO}" font-size="10" '
                 f'letter-spacing="1.2" fill="{p["ink"]}" fill-opacity=".85" '
                 f'text-anchor="middle">{esc(chip)}</text>')
        x += cw + 8

    rows = [("UPDATED", updated), ("STARS", f"{stars:03d}"), ("LANGUAGE", lang)]
    for i, (label, value) in enumerate(rows):
        y = 92 + i * 28
        a.append(f'<text x="712" y="{y}" font-family="{MONO}" font-size="10" letter-spacing="1.8" '
                 f'fill="{p["dim"]}">{label}</text>')
        a.append(f'<path d="M800 {y}H916" stroke="{p["border"]}" stroke-dasharray="1.5 5"/>')
        a.append(f'<text x="980" y="{y}" font-family="{MONO}" font-size="11" letter-spacing=".8" '
                 f'fill="{p["ink"]}" text-anchor="end">{esc(value)}</text>')
    a.append('</g>')
    a.append(f'<path d="{frame(w, h)}" stroke="{p["border"]}"/>')
    a.append(f'<path d="M16 40V16H40" stroke="{p["line"]}" stroke-opacity=".55" stroke-width="2"/>')
    a.append(f'<path d="{w-16} {h-40}V{h-16}H{w-40}" stroke="{p["line"]}" stroke-opacity=".55" stroke-width="2"/>')
    return svg(w, h, "".join(a), name, raw_desc or "Public repository")


# ------------------------------------------------------------------ stats
def stats_svg(theme: str, user: dict, repos: list[dict], top_n: int) -> str:
    """统计面板：1012 宽三栏，数值 / 语言 / 账号信号。"""
    p = PAL[theme]
    w, h = 1012, 192

    public = [r for r in repos
              if not r.get("fork") and not r.get("archived")
              and str(r.get("name", "")).lower() != str((r.get("owner") or {}).get("login", "")).lower()]
    stars = sum(int(r.get("stargazers_count") or 0) for r in public)
    langs = Counter(r["language"] for r in public if r.get("language"))
    top = langs.most_common(top_n)
    since = str(user.get("created_at") or "")[:4] or "----"

    a = [f'<defs>'
         f'<linearGradient id="sb" x1="0" y1="0" x2="0" y2="{h}" gradientUnits="userSpaceOnUse">'
         f'<stop offset="0" stop-color="{p["panel2"]}"/><stop offset="1" stop-color="{p["panel"]}"/></linearGradient>'
         f'<linearGradient id="ca" x1="0" y1="0" x2="0" y2="{h}" gradientUnits="userSpaceOnUse">'
         f'<stop offset="0" stop-color="{p["line"]}"/><stop offset="1" stop-color="{p["line2"]}"/></linearGradient>'
         f'<clipPath id="sc"><path d="{frame(w, h)}"/></clipPath></defs>']
    a.append('<g clip-path="url(#sc)">')
    a.append(f'<rect width="{w}" height="{h}" fill="url(#sb)"/>')
    a.append(f'<rect x="0" y="32" width="3" height="{h-64}" fill="url(#ca)"/>')
    a.append(f'<text x="32" y="44" font-family="{MONO}" font-size="10" letter-spacing="3" '
             f'fill="{p["muted"]}">TELEMETRY // PUBLIC DATA</text>')
    a.append(f'<text x="980" y="44" font-family="{MONO}" font-size="10" letter-spacing="1.6" '
             f'fill="{p["dim"]}" text-anchor="end">SOURCE · GITHUB API</text>')
    a.append(f'<path d="M32 64H980" stroke="{p["line"]}" stroke-opacity=".16"/>')
    a.append(f'<path d="M352 80V168" stroke="{p["border"]}"/>')
    a.append(f'<path d="M684 80V168" stroke="{p["border"]}"/>')

    cores = [("REPOS", len(public)), ("STARS", stars), ("FOLLOWERS", int(user.get("followers") or 0))]
    for i, (label, value) in enumerate(cores):
        x = 32 + i * 120
        a.append(f'<text x="{x}" y="128" font-family="{MONO}" font-size="40" font-weight="700" '
                 f'letter-spacing="1" fill="{p["ink"]}">{value:03d}</text>')
        a.append(f'<text x="{x}" y="152" font-family="{MONO}" font-size="10" letter-spacing="1.8" '
                 f'fill="{p["muted"]}">{label}</text>')

    a.append(f'<text x="384" y="88" font-family="{MONO}" font-size="10" letter-spacing="2.2" '
             f'fill="{p["dim"]}">LANGUAGES</text>')
    if top:
        for i, (lang, count) in enumerate(top[:4]):
            cx = 384 + (i % 2) * 136
            cy = 112 + (i // 2) * 32
            a.append(f'<text x="{cx}" y="{cy}" font-family="{MONO}" font-size="10.5" letter-spacing="1" '
                     f'fill="{p["ink"]}">{esc(lang.upper())}</text>')
            a.append(f'<text x="{cx + 116}" y="{cy}" font-family="{MONO}" font-size="10" letter-spacing=".4" '
                     f'fill="{p["dim"]}" text-anchor="end">{count} REPO{"" if count == 1 else "S"}</text>')
    else:
        a.append(f'<text x="384" y="112" font-family="{MONO}" font-size="10.5" fill="{p["dim"]}">NO DATA</text>')

    top_lang = esc(top[0][0].upper()) if top else "N/A"
    rows = [("SINCE", since), ("FOLLOWING", f"{int(user.get('following') or 0):02d}"),
            ("TOP LANG", top_lang)]
    a.append(f'<text x="716" y="88" font-family="{MONO}" font-size="10" letter-spacing="2.2" '
             f'fill="{p["dim"]}">SIGNAL</text>')
    for i, (label, value) in enumerate(rows):
        y = 112 + i * 24
        a.append(f'<text x="716" y="{y}" font-family="{MONO}" font-size="10" letter-spacing="1.6" '
                 f'fill="{p["dim"]}">{label}</text>')
        a.append(f'<path d="M800 {y}H916" stroke="{p["border"]}" stroke-dasharray="1.5 5"/>')
        a.append(f'<text x="980" y="{y}" font-family="{MONO}" font-size="10.5" letter-spacing=".8" '
                 f'fill="{p["ink"]}" text-anchor="end">{value}</text>')
    a.append('</g>')
    a.append(f'<path d="{frame(w, h)}" stroke="{p["border"]}"/>')
    a.append(f'<path d="M16 40V16H40" stroke="{p["line"]}" stroke-opacity=".55" stroke-width="2"/>')
    a.append(f'<path d="{w-16} {h-40}V{h-16}H{w-40}" stroke="{p["line"]}" stroke-opacity=".55" stroke-width="2"/>')
    return svg(w, h, "".join(a), f"{user.get('login','')} — GitHub telemetry",
               "公开仓库数量、Star、关注者与语言分布的自动统计")


# ------------------------------------------------------------------ data
def api_get(path: str) -> object:
    token = os.environ.get("GITHUB_TOKEN") or os.environ.get("GH_TOKEN")
    req = urllib.request.Request(
        "https://api.github.com" + path,
        headers={
            "Accept": "application/vnd.github+json",
            "User-Agent": "oliverbuddy-profile-sync",
            "X-GitHub-Api-Version": "2022-11-28",
            **({"Authorization": f"Bearer {token}"} if token else {}),
        },
    )
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            return json.load(resp)
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", "replace")[:300]
        raise SystemExit(f"GitHub API {path} failed: {exc.code} {detail}") from exc


def load_repos(username: str) -> list[dict]:
    repos: list[dict] = []
    for page in range(1, 6):
        batch = api_get(f"/users/{username}/repos?per_page=100&type=owner&page={page}")
        if not isinstance(batch, list):
            raise SystemExit("unexpected repos payload")
        repos.extend(batch)
        if len(batch) < 100:
            break
    return repos


def select_projects(repos: list[dict], cfg: dict) -> list[dict]:
    rules = cfg.get("filters", {})
    excluded = {name.lower() for name in cfg.get("exclude_repos", [])}
    picked = []
    for repo in repos:
        name = str(repo.get("name", ""))
        owner = str((repo.get("owner") or {}).get("login", "")).lower()
        if repo.get("private"):
            continue
        if rules.get("exclude_forks", True) and repo.get("fork"):
            continue
        if rules.get("exclude_archived", True) and repo.get("archived"):
            continue
        if repo.get("disabled"):
            continue
        if rules.get("exclude_profile_repo", True) and name.lower() == owner:
            continue
        if rules.get("exclude_without_description", True) and not (repo.get("description") or "").strip():
            continue
        if rules.get("exclude_empty_repos", True) and int(repo.get("size") or 0) <= 0:
            continue
        if name.lower() in excluded:
            continue
        if rules.get("require_topics") and not repo.get("topics"):
            continue
        picked.append(repo)
    key = cfg.get("sort", "pushed_at")
    if key == "stars":
        picked.sort(key=lambda r: (int(r.get("stargazers_count") or 0), str(r.get("pushed_at") or "")), reverse=True)
    else:
        picked.sort(key=lambda r: str(r.get("pushed_at") or ""), reverse=True)
    return picked[: int(cfg.get("max_projects", 3))]


# ------------------------------------------------------------------ render
def sha(path: pathlib.Path) -> str:
    return hashlib.sha1(path.read_bytes()).hexdigest()[:8]


def replace_block(text: str, start: str, end: str, content: str) -> str:
    if start not in text or end not in text:
        raise SystemExit(f"README markers missing: {start} / {end}")
    head, rest = text.split(start, 1)
    _, tail = rest.split(end, 1)
    return f"{head}{start}\n{content.strip()}\n{end}{tail}"


def main() -> None:
    cfg = json.loads(CONFIG.read_text(encoding="utf-8"))
    username = cfg.get("username") or "oliverbuddy"
    text = README.read_text(encoding="utf-8")
    for marker in (PROJECT_START, PROJECT_END, STATS_START, STATS_END):
        if marker not in text:
            raise SystemExit(f"README marker missing: {marker}")

    user = api_get(f"/users/{username}")
    repos = load_repos(username)
    projects = select_projects(repos, cfg)

    ASSETS.mkdir(exist_ok=True)
    old_manifest = {}
    if MANIFEST.exists():
        try:
            old_manifest = json.loads(MANIFEST.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            old_manifest = {}

    new_files: set[str] = set()
    blocks: list[str] = []
    manifest_projects = []
    for idx, repo in enumerate(projects, start=1):
        slug = slugify(repo["name"])
        dark = ASSETS / f"project-{slug}-dark.svg"
        light = ASSETS / f"project-{slug}-light.svg"
        dark.write_text(card_svg("dark", idx, repo), encoding="utf-8")
        light.write_text(card_svg("light", idx, repo), encoding="utf-8")
        new_files.update({dark.name, light.name})
        manifest_projects.append({"name": repo["name"], "slug": slug,
                                  "files": [dark.name, light.name]})
        d_url = f"assets/{dark.name}?v={sha(dark)}"
        l_url = f"assets/{light.name}?v={sha(light)}"
        alt = f'{repo["name"]} — {repo.get("description") or "public repository"}'
        blocks.append(
            '<p align="center">\n'
            f'  <a href="{esc(repo["html_url"])}">\n'
            '    <picture>\n'
            f'      <source media="(prefers-color-scheme: dark)" srcset="{d_url}">\n'
            f'      <source media="(prefers-color-scheme: light)" srcset="{l_url}">\n'
            f'      <img src="{l_url}" alt="{esc(alt)}" width="1012">\n'
            '    </picture>\n'
            '  </a>\n'
            '</p>'
        )

    if not blocks:
        blocks.append('<p align="center"><sub>暂无符合规则（非 fork / 非归档 / 有描述 / 非空）的公开仓库。</sub></p>')

    rules = cfg.get("filters", {})
    rule_bits = []
    if rules.get("exclude_forks", True):
        rule_bits.append("非 fork")
    if rules.get("exclude_archived", True):
        rule_bits.append("非归档")
    if rules.get("exclude_without_description", True):
        rule_bits.append("有描述")
    if rules.get("exclude_empty_repos", True):
        rule_bits.append("非空仓库")
    rule_bits.append(f"最近更新前 {cfg.get('max_projects', 3)} 个")
    blocks.append('<p align="center">\n  <sub>AUTO-SYNC · '
                  + " / ".join(rule_bits)
                  + f' · <a href="https://github.com/{username}/{username}/blob/main/scripts/profile_config.json">规则</a>'
                  + f' · <a href="https://github.com/{username}?tab=repositories">全部仓库 →</a></sub>\n</p>')

    # 统计面板
    stats_files = {}
    for theme in ("dark", "light"):
        path = ASSETS / f"stats-{theme}.svg"
        path.write_text(stats_svg(theme, user, repos, int(cfg.get("stats", {}).get("top_languages", 4))),
                        encoding="utf-8")
        new_files.add(path.name)
        stats_files[theme] = f"assets/{path.name}?v={sha(path)}"
    stats_block = (
        '<p align="center">\n'
        '  <picture>\n'
        f'    <source media="(prefers-color-scheme: dark)" srcset="{stats_files["dark"]}">\n'
        f'    <source media="(prefers-color-scheme: light)" srcset="{stats_files["light"]}">\n'
        f'    <img src="{stats_files["light"]}" alt="{esc(username)} 的公开仓库统计" width="1012">\n'
        '  </picture>\n'
        '</p>'
    )

    # 清理不再生成的卡片：旧清单里记录的 + assets 目录中的历史孤儿文件
    stale = {name for entry in old_manifest.get("projects", []) for name in entry.get("files", [])}
    stale.update(p.name for p in ASSETS.glob("project-*.svg"))
    for name in sorted(stale):
        if name not in new_files and (ASSETS / name).exists():
            (ASSETS / name).unlink()
            print(f"removed stale card: {name}")

    text = replace_block(text, PROJECT_START, PROJECT_END, "\n\n".join(blocks))
    text = replace_block(text, STATS_START, STATS_END, stats_block)
    README.write_text(text, encoding="utf-8")

    MANIFEST.write_text(json.dumps({"projects": manifest_projects}, ensure_ascii=False, indent=2) + "\n",
                        encoding="utf-8")
    print(f"synced {len(projects)} project(s): {', '.join(r['name'] for r in projects) or 'none'}")
    print(f"files: {len(new_files)} generated")


if __name__ == "__main__":
    main()
