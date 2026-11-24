#!/usr/bin/env python3
"""从 GitHub API 同步公开仓库信息，重写 README 的项目与数据区块。

只读取公开数据，只改写 README 中被标记注释包裹的区块，便于工作流重复运行。
项目信息以 Markdown 文本输出，便于搜索引擎与读屏软件识别。
"""
from __future__ import annotations

import json
import os
import sys
import urllib.error
import urllib.request
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
README = ROOT / "README.md"
CONFIG = ROOT / "scripts" / "profile_config.json"

PROJECT_START = "<!-- PROJECTS:START -->"
PROJECT_END = "<!-- PROJECTS:END -->"
STATS_START = "<!-- STATS:START -->"
STATS_END = "<!-- STATS:END -->"

API = "https://api.github.com"


def api_get(path: str) -> dict | list:
    token = os.environ.get("GITHUB_TOKEN") or os.environ.get("GH_TOKEN")
    headers = {
        "Accept": "application/vnd.github+json",
        "User-Agent": "oliverbuddy-profile-sync",
    }
    if token:
        headers["Authorization"] = f"Bearer {token}"
    req = urllib.request.Request(API + path, headers=headers)
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            return json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        print(f"API {path} -> HTTP {exc.code}", file=sys.stderr)
        raise SystemExit(1)
    except Exception as exc:  # 网络错误统一失败退出，避免写入半成品
        print(f"API {path} -> {exc}", file=sys.stderr)
        raise SystemExit(1)


def load_repos(username: str) -> list[dict]:
    repos: list[dict] = []
    for page in range(1, 6):
        batch = api_get(f"/users/{username}/repos?per_page=100&type=owner&page={page}")
        if not isinstance(batch, list) or not batch:
            break
        repos.extend(batch)
        if len(batch) < 100:
            break
    return repos


def select_projects(repos: list[dict], cfg: dict) -> list[dict]:
    rules = cfg.get("filters") or {}
    blocked = {str(name).lower() for name in cfg.get("exclude_repos") or []}
    picked = []
    for repo in repos:
        name = str(repo.get("name") or "")
        if repo.get("private"):
            continue
        if rules.get("exclude_forks", True) and repo.get("fork"):
            continue
        if rules.get("exclude_archived", True) and repo.get("archived"):
            continue
        if rules.get("exclude_empty_repos", True) and int(repo.get("size") or 0) <= 0:
            continue
        if rules.get("exclude_profile_repo", True) and name.lower() == str(cfg.get("username", "")).lower():
            continue
        if rules.get("exclude_without_description", True) and not str(repo.get("description") or "").strip():
            continue
        if rules.get("require_topics", False) and not repo.get("topics"):
            continue
        if name.lower() in blocked:
            continue
        picked.append(repo)

    sort_key = cfg.get("sort", "pushed_at")
    if sort_key == "stars":
        picked.sort(key=lambda r: (int(r.get("stargazers_count") or 0), str(r.get("pushed_at") or "")),
                    reverse=True)
    else:
        picked.sort(key=lambda r: str(r.get("pushed_at") or ""), reverse=True)
    return picked[: int(cfg.get("max_projects", 3))]


def clean(text) -> str:
    return " ".join(str(text or "").split())


def month(value) -> str:
    raw = str(value or "")
    return raw[:7].replace("-", ".") if len(raw) >= 7 else "—"


def project_block(projects: list[dict], cfg: dict) -> str:
    """项目卡片 + 折叠的筛选规则。卡片用引用块，信息仍是可选中的纯文本。"""
    if projects:
        cards = []
        for index, repo in enumerate(projects, start=1):
            name = clean(repo.get("name")) or "repository"
            url = str(repo.get("html_url") or "")
            desc = clean(repo.get("description")) or "无描述"
            tags: list[str] = []
            seen: set[str] = set()
            for tag in [repo.get("language"), *(repo.get("topics") or [])]:
                label = clean(tag)
                if label and label.lower() not in seen:
                    seen.add(label.lower())
                    tags.append(f"`{label}`")
            meta = []
            if tags:
                meta.append(" ".join(tags[:4]))
            meta.append(f"更新 `{month(repo.get('pushed_at'))}`")
            stars = int(repo.get("stargazers_count") or 0)
            if stars:
                meta.append(f"★ `{stars}`")
            meta.append(f"[仓库 →]({url})")
            cards.append(
                f"> **{index:02d} · [{name}]({url})**  \n"
                f"> {desc}  \n"
                f"> " + " · ".join(meta)
            )
        body = "\n\n".join(cards)
    else:
        body = "_暂无符合筛选规则的公开仓库。_"

    rules = cfg.get("filters") or {}
    bits = ["非 fork" if rules.get("exclude_forks", True) else "含 fork"]
    bits.append("非归档" if rules.get("exclude_archived", True) else "含归档")
    if rules.get("exclude_without_description", True):
        bits.append("有描述")
    if rules.get("exclude_empty_repos", True):
        bits.append("非空仓库")
    username = cfg.get("username") or "oliverbuddy"
    details = (
        "<details>\n"
        "<summary><sub>AUTO-SYNC · 筛选规则 / 全部仓库</sub></summary>\n\n"
        f"- 筛选：{' / '.join(bits)}\n"
        f"- 排序：按最近更新，取前 {int(cfg.get('max_projects', 3))} 个\n"
        "- 同步：每天由 GitHub Actions 自动刷新\n\n"
        f"[筛选规则](https://github.com/{username}/{username}/blob/main/scripts/profile_config.json)"
        f" · [全部仓库 →](https://github.com/{username}?tab=repositories)\n\n"
        "</details>"
    )
    return body + "\n\n" + details


def stats_block(user: dict, repos: list[dict]) -> str:
    login = str(user.get("login") or "")
    public = [r for r in repos
              if not r.get("fork") and not r.get("archived")
              and str(r.get("name", "")).lower() != login.lower()]
    stars = sum(int(r.get("stargazers_count") or 0) for r in public)
    langs = Counter(r["language"] for r in public if r.get("language"))
    top = langs.most_common(1)
    lang, lang_count = (top[0][0], top[0][1]) if top else ("—", 0)
    since = str(user.get("created_at") or "")[:4] or "—"
    followers = int(user.get("followers") or 0)
    rows = [
        ("PROFILE", login or "—"),
        ("REPOS", str(len(public))),
        ("STARS", str(stars)),
        ("FOLLOWERS", str(followers)),
        ("MAIN", f"{lang} · {count_word(lang_count)}"),
        ("SINCE", since),
    ]
    width = max(len(key) for key, _ in rows) + 2
    lines = ["```console"]
    lines += [f"{key:<{width}}{value}" for key, value in rows]
    lines.append("```")
    lines.append("")
    lines.append("<sub>数字来自 GitHub API（不含 profile 仓库），工作流每日自动刷新。</sub>")
    return "\n".join(lines)


def count_word(count: int) -> str:
    return f"{count} 个仓库" if count else "暂无语言数据"


def replace_block(text: str, start: str, end: str, body: str) -> str:
    if start not in text or end not in text:
        raise SystemExit(f"README markers missing: {start} / {end}")
    head, rest = text.split(start, 1)
    _, tail = rest.split(end, 1)
    return f"{head}{start}\n{body.strip()}\n{end}{tail}"


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

    updated = replace_block(text, PROJECT_START, PROJECT_END, project_block(projects, cfg))
    updated = replace_block(updated, STATS_START, STATS_END, stats_block(user, repos))

    if updated == text:
        print("README already up to date")
        return
    README.write_text(updated, encoding="utf-8")
    print(f"README updated: {len(projects)} project(s), {len(repos)} public repo(s) scanned")


if __name__ == "__main__":
    main()
