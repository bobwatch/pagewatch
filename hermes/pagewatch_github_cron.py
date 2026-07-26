#!/usr/bin/env python
"""PageWatch GitHub Repo Auto-Maintenance

Orchestrates automated maintenance of the pagewatch_github repo:
1. Generate README badges (update activity stats)
2. Generate SEO-optimized blog content as markdown files in the repo
3. Commit and push to GitHub

Designed to be called by hermes cron.
"""

from __future__ import annotations

import os
import subprocess
import sys
import time
from pathlib import Path

REPO_DIR = Path(r"E:\workspace\pagewatch_github")
PYTHON_EXE = r"D:\Anaconda3\envs\py312\python.exe"
GITHUB_TOKEN = os.environ.get("GITHUB_TOKEN", "")


def run_git(args: list[str], check: bool = True) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["git"] + args,
        cwd=str(REPO_DIR),
        capture_output=True,
        text=True,
        timeout=60,
        check=check,
    )


def git_commit_and_push(message: str) -> bool:
    """Stage all changes, commit, and push to GitHub."""
    try:
        run_git(["add", "-A"])
        status = run_git(["status", "--porcelain"])
        if not status.stdout.strip():
            print("No changes to commit.")
            return True

        run_git(["config", "user.email", "bot@pagewatch.tech"])
        run_git(["config", "user.name", "pagewatch-bot"])
        run_git(["commit", "-m", message])

        remote_url = f"https://bobwatch:{GITHUB_TOKEN}@github.com/bobwatch/pagewatch.git"
        run_git(["remote", "set-url", "origin", remote_url])
        result = run_git(["push", "origin", "main"])
        print(f"Pushed commit: {message}")
        return True
    except subprocess.CalledProcessError as exc:
        print(f"Git error: {exc.stderr}")
        return False


def update_readme_stats():
    """Update activity stats in the repo."""
    now = time.strftime("%Y-%m-%d %H:%M:%S UTC", time.gmtime())
    stats_file = REPO_DIR / ".github" / "activity.log"
    stats_file.parent.mkdir(parents=True, exist_ok=True)
    with open(stats_file, "a", encoding="utf-8") as f:
        f.write(f"{now} - automated maintenance\n")
    print(f"Updated activity log: {now}")
    return True


def generate_seo_article():
    """Generate an SEO-optimized markdown article in the repo's blog/ directory."""
    import random
    import json
    from datetime import datetime, timezone

    topics = [
        {
            "slug": "website-change-monitoring-python-guide",
            "title": "How to Monitor Website Changes with Python: A Complete Guide",
            "tags": ["python", "web-scraping", "monitoring", "tutorial"],
        },
        {
            "slug": "best-free-website-change-detection-tools-2026",
            "title": "10 Best Free Website Change Detection Tools in 2026",
            "tags": ["comparison", "tools", "free-software", "web-monitoring"],
        },
        {
            "slug": "visualping-alternatives-open-source",
            "title": "Visualping Alternatives: 7 Open Source Website Change Monitors",
            "tags": ["visualping", "open-source", "comparison", "alternatives"],
        },
        {
            "slug": "website-change-detection-seo-strategy",
            "title": "Using Website Change Detection for SEO: A Strategic Guide",
            "tags": ["seo", "digital-marketing", "monitoring", "strategy"],
        },
        {
            "slug": "scrape-dynamic-websites-python-2026",
            "title": "How to Scrape Dynamic JavaScript Websites with Python in 2026",
            "tags": ["web-scraping", "javascript", "python", "selenium"],
        },
        {
            "slug": "cron-job-website-monitoring",
            "title": "Setting Up Cron Jobs for Website Monitoring: Step-by-Step Guide",
            "tags": ["cron", "linux", "devops", "monitoring"],
        },
        {
            "slug": "docker-website-change-monitor",
            "title": "Deploying a Website Change Monitor with Docker: Complete Tutorial",
            "tags": ["docker", "devops", "self-hosting", "tutorial"],
        },
        {
            "slug": "monitor-competitor-pricing-changes",
            "title": "How to Monitor Competitor Pricing Changes Automatically",
            "tags": ["ecommerce", "competitor-analysis", "pricing", "business"],
        },
    ]

    history_file = REPO_DIR / ".article_history.json"
    if history_file.is_file():
        history = json.loads(history_file.read_text(encoding="utf-8"))
    else:
        history = {"generated": []}

    available = [t for t in topics if t["slug"] not in history["generated"]]
    if not available:
        available = list(topics)
        history["generated"] = []

    topic = random.choice(available)
    topic_data = next(t for t in topics if t["slug"] == topic["slug"])
    history["generated"].append(topic_data["slug"])
    history_file.write_text(json.dumps(history, indent=2, ensure_ascii=False), encoding="utf-8")

    article = _generate_article_content(topic_data)
    blog_dir = REPO_DIR / "blog"
    blog_dir.mkdir(exist_ok=True)
    date_str = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    file_path = blog_dir / f"{topic_data['slug']}.md"
    file_path.write_text(article, encoding="utf-8")
    print(f"Generated article: {file_path.name}")
    return True


def _generate_article_content(topic: dict) -> str:
    slug = topic["slug"]
    title = topic["title"]
    tags = topic.get("tags", [])
    date_str = time.strftime("%Y-%m-%d")

    content = f"""---
title: "{title}"
description: "A comprehensive guide to {slug.replace('-', ' ')} — learn how to monitor web pages for changes with open-source tools and Python."
slug: "{slug}"
date: "{date_str}"
tags: [{', '.join(f'"{t}"' for t in tags)}]
canonical: "https://pagewatch.tech/blog/{slug}"
---

# {title}

*Originally published on the [PageWatch.tech Blog](https://pagewatch.tech/blog) — the professional website change monitoring platform.*

---

## Introduction

Monitoring website changes is essential for developers, marketers, and business owners who need to stay informed about content updates, price changes, and competitor activity. In this guide, we'll explore how to build your own website change detection system using open-source tools.

## Why Monitor Website Changes?

Website change detection serves many purposes:

- **Competitor monitoring** — Track pricing, product launches, and content strategy changes
- **SEO monitoring** — Detect changes to your own site's content that could affect rankings
- **Compliance tracking** — Monitor regulatory and policy pages for updates
- **News and alerts** — Get notified when specific pages change

## Building a Simple Change Detector

The core of any website change monitor is straightforward:

1. **Fetch** the page content
2. **Extract** the relevant text or elements
3. **Hash** the content
4. **Compare** against the previous hash
5. **Alert** if changed

Here's a minimal Python implementation:

```python
import hashlib
import requests
from bs4 import BeautifulSoup

def check_for_changes(url, previous_hash):
    response = requests.get(url)
    soup = BeautifulSoup(response.text, 'html.parser')
    content = soup.get_text(strip=True)
    current_hash = hashlib.sha256(content.encode()).hexdigest()
    changed = previous_hash and current_hash != previous_hash
    return current_hash, changed
```

## Open Source vs Hosted Solutions

| Feature | DIY (Open Source) | Hosted (e.g. [PageWatch.tech](https://pagewatch.tech)) |
|---------|------------------|------------------------------------------------------|
| Setup effort | High | Minimal |
| Maintenance | Your responsibility | Fully managed |
| Visual diffs | Manual | Automatic |
| Proxy rotation | Custom setup | 50+ regions |
| Alert channels | Basic | Email, Slack, Discord, Webhook |
| Cost | Server costs | Free tier available |

## Conclusion

Website change monitoring doesn't have to be expensive or complicated. Start with simple open-source tools, and when you need advanced features like visual diffs, proxy rotation, and team collaboration, consider upgrading to a professional platform like [PageWatch.tech](https://pagewatch.tech).

---

*This article is part of the [PageWatch.tech Blog](https://pagewatch.tech/blog). For a fully managed website change monitoring solution with visual diffs, 50+ proxy regions, and instant alerts, visit [pagewatch.tech](https://pagewatch.tech).*
"""
    return content


def main():
    print("=" * 60)
    print("PageWatch GitHub Repo Maintenance")
    print("=" * 60)

    # Step 1: Update stats
    print("\n[1/3] Updating activity log...")
    update_readme_stats()

    # Step 2: Generate SEO article
    print("\n[2/3] Generating SEO article...")
    generate_seo_article()

    # Step 3: Commit and push
    print("\n[3/3] Committing and pushing to GitHub...")
    date_str = time.strftime("%Y-%m-%d")
    ok = git_commit_and_push(f"chore: automated maintenance {date_str}")

    if ok:
        print("\nOK - Maintenance complete")
    else:
        print("\nFAIL - Maintenance failed (git push error)")
        sys.exit(1)


if __name__ == "__main__":
    main()