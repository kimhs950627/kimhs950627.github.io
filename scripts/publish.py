#!/usr/bin/env python3
"""publish.py — 저장소 하나를 '지금 공개 상태에 맞는 곳'에 발행하고 색인을 고친다.

  private + 사이트 있음 → Cloudflare Pages (Access 보호, 소유자만)
  public  + 사이트 있음 → GitHub Pages (누구나)
  공개/비공개를 뒤집으면 sync 가 알아서 반대편으로 옮긴다.

사용:
  python3 scripts/publish.py status                 # 관리 중인 항목 상태
  python3 scripts/publish.py publish <repo>         # 발행 또는 재발행
  python3 scripts/publish.py sync                   # 전체 화를 맞춘다 (야간 자동 실행)
  python3 scripts/publish.py approve <email>        # 열람 승인 (Access 그룹에 추가)
  python3 scripts/publish.py pending                # 대기 중인 열람 신청
"""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
CURATED = ROOT / "data" / "curated.json"
OWNER = "kimhs950627"
OWNER_EMAIL = "kimhs950627@gmail.com"
CF_CRED = Path.home() / ".config" / "cloudflare" / "api_token"


# ── 자격증명 ────────────────────────────────────────────────────────
def cf_env() -> tuple[str, str]:
    token = os.environ.get("CLOUDFLARE_API_TOKEN")
    account = os.environ.get("CLOUDFLARE_ACCOUNT_ID")
    if not token and CF_CRED.exists():
        for line in CF_CRED.read_text().splitlines():
            if line.startswith("CLOUDFLARE_API_TOKEN="):
                token = line.split("=", 1)[1].strip()
            elif line.startswith("CLOUDFLARE_ACCOUNT_ID="):
                account = line.split("=", 1)[1].strip()
    if not token or not account:
        sys.exit("Cloudflare 자격증명이 없다 (~/.config/cloudflare/api_token).")
    return token, account


def gh(args: list[str]) -> str:
    out = subprocess.run(["gh", *args], capture_output=True, text=True)
    if out.returncode != 0:
        raise RuntimeError(out.stderr.strip() or "gh 실패")
    return out.stdout


def cf(token: str, account: str, path: str, method: str = "GET", body: dict | None = None) -> dict:
    req = urllib.request.Request(
        f"https://api.cloudflare.com/client/v4/accounts/{account}{path}",
        method=method,
        data=json.dumps(body).encode() if body is not None else None,
        headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"},
    )
    try:
        with urllib.request.urlopen(req, timeout=60) as res:
            return json.loads(res.read() or b"{}")
    except urllib.error.HTTPError as err:
        return json.loads(err.read() or b"{}")


# ── 저장소 살펴보기 ────────────────────────────────────────────────
def repo_info(repo: str) -> dict:
    data = json.loads(gh(["api", f"repos/{OWNER}/{repo}"]))
    return {"private": data["private"], "branch": data["default_branch"], "description": data.get("description") or ""}


def site_root(repo: str, branch: str) -> tuple[str, bool] | None:
    """(경로, 정적여부). 루트 docs 우선, 없으면 Jekyll(index.md) 사이트로 본다."""
    tree = json.loads(gh(["api", f"repos/{OWNER}/{repo}/git/trees/{branch}?recursive=1"]))["tree"]
    names = {t["path"] for t in tree}
    if "index.html" in names:
        return "/", True
    if "docs/index.html" in names:
        return "/docs", True
    if "index.md" in names or "docs/index.md" in names:
        return ("/docs" if "docs/index.md" in names and "index.md" not in names else "/"), False
    if "README.md" in names:  # GitHub Pages 가 README 를 색인으로 렌더한다
        return "/", False
    return None


def has_pages(repo: str) -> dict | None:
    try:
        return json.loads(gh(["api", f"repos/{OWNER}/{repo}/pages"]))
    except RuntimeError:
        return None


# ── 발행: GitHub Pages ────────────────────────────────────────────
def publish_github_pages(repo: str, branch: str, path: str) -> str:
    current = has_pages(repo)
    if current and current.get("source", {}).get("branch") == branch and current.get("source", {}).get("path") == path:
        return current["html_url"]
    payload = json.dumps({"source": {"branch": branch, "path": path}})
    if current:
        gh(["api", "-X", "PUT", f"repos/{OWNER}/{repo}/pages", "--input", "-"]) if False else None
        subprocess.run(["gh", "api", "-X", "PUT", f"repos/{OWNER}/{repo}/pages", "--input", "-"], input=payload, text=True, capture_output=True)
    else:
        subprocess.run(["gh", "api", "-X", "POST", f"repos/{OWNER}/{repo}/pages", "--input", "-"], input=payload, text=True, capture_output=True)
    for _ in range(2):
        info = has_pages(repo)
        if info:
            return info["html_url"]
    return f"https://{OWNER}.github.io/{repo}/"


def unpublish_github_pages(repo: str) -> None:
    subprocess.run(["gh", "api", "-X", "DELETE", f"repos/{OWNER}/{repo}/pages"], capture_output=True)


# ── 발행: Cloudflare Pages (+ Access) ──────────────────────────────
def slug_of(repo: str) -> str:
    return re.sub(r"[^a-z0-9-]+", "-", repo.lower())[:48].strip("-")


def cf_project(token: str, account: str, slug: str) -> dict | None:
    res = cf(token, account, f"/pages/projects/{slug}")
    return res.get("result") if res.get("success") else None


def cf_ensure_project(token: str, account: str, slug: str) -> dict:
    proj = cf_project(token, account, slug)
    if proj:
        return proj
    res = cf(token, account, "/pages/projects", "POST", {"name": slug, "production_branch": "main"})
    if not res.get("success"):
        raise RuntimeError(f"Pages 프로젝트 생성 실패: {res.get('errors')}")
    return res["result"]


def cf_deploy(slug: str, workdir: Path, token: str, account: str) -> str:
    env = dict(os.environ, CLOUDFLARE_API_TOKEN=token, CLOUDFLARE_ACCOUNT_ID=account)
    out = subprocess.run(
        ["bunx", "--bun", "wrangler", "pages", "deploy", str(workdir), "--project-name", slug, "--branch", "main", "--commit-dirty=true"],
        capture_output=True, text=True, env=env, cwd=str(workdir),
    )
    blob = out.stdout + out.stderr
    m = re.search(r"https://([a-z0-9]+)\." + re.escape(slug) + r"\.pages\.dev", blob)
    if not m:
        raise RuntimeError(f"배포 실패: {blob[-600:]}")
    return f"https://{slug}.pages.dev/"


def access_apps(token: str, account: str) -> list[dict]:
    res = cf(token, account, "/access/apps")
    return res.get("result", []) if res.get("success") else []


def cf_ensure_access(token: str, account: str, slug: str) -> str:
    """<slug>.pages.dev 와 *.  두 앱을 만들고 소유자만 허용하는 정책을 건다."""
    apps = access_apps(token, account)
    for domain in (f"{slug}.pages.dev", f"*.{slug}.pages.dev"):
        if any(a.get("domain") == domain for a in apps):
            continue
        res = cf(token, account, "/access/apps", "POST", {
            "name": f"{slug} ({domain.split('.')[0]})",
            "domain": domain,
            "type": "self_hosted",
            "session_duration": "24h",
        })
        if not res.get("success"):
            raise RuntimeError(f"Access 앱 생성 실패: {res.get('errors')}")
        app_id = res["result"]["id"]
        pol = cf(token, account, f"/access/apps/{app_id}/policies", "POST", {
            "name": "허용 목록",
            "decision": "allow",
            "include": [{"email": {"email": OWNER_EMAIL}}],
            "require": [],
            "exclude": [],
        })
        if not pol.get("success"):
            raise RuntimeError(f"Access 정책 생성 실패: {pol.get('errors')}")
    return f"https://{slug}.pages.dev/"


def cf_remove(token: str, account: str, slug: str) -> None:
    for a in access_apps(token, account):
        if (a.get("domain") or "").startswith(f"{slug}.pages.dev") or a.get("domain") == f"*.{slug}.pages.dev":
            cf(token, account, f"/access/apps/{a['id']}", "DELETE")
    cf(token, account, f"/pages/projects/{slug}", "DELETE")


def clone_repo(repo: str, branch: str, path: str, dest: Path) -> Path:
    token = subprocess.run(["gh", "auth", "token"], capture_output=True, text=True).stdout.strip()
    subprocess.run(
        ["git", "clone", "-q", "--depth", "1", "--branch", branch,
         f"https://x-access-token:{token}@github.com/{OWNER}/{repo}.git", str(dest)],
        check=True,
    )
    return dest if path == "/" else dest / path.strip("/")


# ── 색인 갱신 ────────────────────────────────────────────────────
def load_curated() -> dict:
    return json.loads(CURATED.read_text(encoding="utf-8"))


def save_curated(data: dict) -> None:
    CURATED.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def upsert(data: dict, repo: str, *, url: str, host: str, private: bool, note: str = "", title: str = "") -> None:
    page = data["pages"].setdefault(repo, {"category": "other", "order": 900})
    page["url"] = url
    page["host"] = host
    if private:
        page["access"] = "private"
    else:
        page.pop("access", None)
    if title and not page.get("title"):
        page["title"] = title
    if note and not page.get("note"):
        page["note"] = note


def publish(repo: str, *, quiet: bool = False) -> str:
    token, account = cf_env()
    info = repo_info(repo)
    found = site_root(repo, info["branch"])
    if not found:
        raise RuntimeError(f"{repo}: 발행할 index 가 없다 (루트 또는 /docs).")
    path, static = found
    if info["private"] and not static:
        raise RuntimeError(f"{repo}: Jekyll 사이트(index.md)는 Cloudflare 로 그대로 못 올린다 — HTML 로 빌드해야 한다.")

    data = load_curated()
    slug = slug_of(repo)
    if info["private"]:
        cf_ensure_project(token, account, slug)
        tmp = Path(tempfile.mkdtemp(prefix="pages-"))
        try:
            out = clone_repo(repo, info["branch"], path, tmp / "src")
            url = cf_deploy(slug, out, token, account)
        finally:
            shutil.rmtree(tmp, ignore_errors=True)
        cf_ensure_access(token, account, slug)
        unpublish_github_pages(repo)  # 공개 채널이 남아 있으면 닫는다
        host = url.replace("https://", "").rstrip("/")
        upsert(data, repo, url=url, host=host, private=True, note=info["description"], title=repo)
        kind = "비공개(Cloudflare + Access)"
    else:
        url = publish_github_pages(repo, info["branch"], path)
        cf_remove(token, account, slug)
        host = url.replace("https://", "").rstrip("/")
        upsert(data, repo, url=url, host=host, private=False, note=info["description"], title=repo)
        kind = "공개(GitHub Pages)"

    save_curated(data)
    if not quiet:
        print(f"{repo} → {kind} {url}")
    return url


def sync() -> None:
    data = load_curated()
    managed = [r for r, p in data["pages"].items() if not p.get("manual")]
    changed = 0
    for repo in managed:
        try:
            before = json.dumps(data["pages"][repo], sort_keys=True)
            publish(repo, quiet=True)
            data = load_curated()
            if json.dumps(data["pages"][repo], sort_keys=True) != before:
                changed += 1
            print(f"  ok {repo}")
        except Exception as err:  # noqa: BLE001
            print(f"  ! {repo}: {err}", file=sys.stderr)
    print(f"sync 완료 · 검사 {len(managed)} · 변경 {changed}")


# ── 열람 승인 ────────────────────────────────────────────────────
def approve(email: str) -> None:
    token, account = cf_env()
    groups = cf(token, account, "/access/groups").get("result", [])
    group = next((g for g in groups if g["name"] == "viewers"), None)
    if not group:
        sys.exit("viewers 그룹이 없다.")
    include = group.get("include", [])
    if any(r.get("email", {}).get("email", "").lower() == email.lower() for r in include):
        print(f"{email} 은 이미 허용 목록에 있다")
        return
    include.append({"email": {"email": email}})
    res = cf(token, account, f"/access/groups/{group['id']}", "PUT", {"name": "viewers", "include": include})
    print(f"{email} 승인" if res.get("success") else f"실패: {res.get('errors')}")


def status() -> None:
    data = load_curated()
    for repo, page in data["pages"].items():
        flag = "🔒" if page.get("access") == "private" else "  "
        print(f"{flag} {repo:42} {page.get('url') or '-'}")


def main() -> int:
    argv = sys.argv[1:]
    if not argv:
        print(__doc__)
        return 1
    cmd = argv[0]
    if cmd == "publish" and len(argv) > 1:
        publish(argv[1])
    elif cmd == "sync":
        sync()
    elif cmd == "status":
        status()
    elif cmd == "approve" and len(argv) > 1:
        approve(argv[1])
    else:
        print(__doc__)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
