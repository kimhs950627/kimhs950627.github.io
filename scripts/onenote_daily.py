#!/usr/bin/env python3
"""onenote_daily.py — 하루치 파이프라인을 한 번에 돌린다(로컬·Actions 공용).

    감지 → 텍스트 캐시 → 매핑(모델) → 병합(elaborate) → 그림 → 문장 다듬기 → job 파일 생성

각 단계는 기존 스크립트를 그대로 호출한다. 실패한 단계는 건너뛰고 나머지를 계속한다
(한 페이지 오류로 전체가 멈추지 않게).

  python3 scripts/onenote_daily.py --notebook Books_for_Meds --since 1d
  python3 scripts/onenote_daily.py --notebook Books_for_Meds --since 3d --with-images --out-dir jobs
  python3 scripts/onenote_daily.py --dry-run            # 감지까지만

출력: `--out-dir` 에 단계 순서대로 `NN-단계.json` job 파일(apply.yml 이 순서대로 적용).
"""
from __future__ import annotations

import json
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
PY = sys.executable


def run(name: str, args: list[str], timeout: int = 1800) -> tuple[bool, str]:
    t0 = time.time()
    print(f"\n=== {name}: {' '.join(args[1:])}", flush=True)
    try:
        proc = subprocess.run(args, cwd=ROOT, capture_output=True, text=True, timeout=timeout)
    except subprocess.TimeoutExpired:
        print(f"  ! {name} 시간 초과({timeout}s)", flush=True)
        return False, ""
    out = (proc.stdout or "").strip()
    if proc.returncode != 0:
        print(f"  ! {name} 실패({proc.returncode})", flush=True)
        print((proc.stderr or out)[-800:], flush=True)
        return False, out
    print(f"  · {name} 완료 {time.time() - t0:.1f}s", flush=True)
    tail = out.splitlines()[-3:]
    for line in tail:
        print(f"    {line[:160]}", flush=True)
    return True, out


def self_test() -> int:
    """환경 점검 — 모델 키 · 그래프 토큰 · 저장소 클론이 실제로 되는지(값은 출력하지 않는다)."""
    sys.path.insert(0, str(ROOT / "scripts"))
    ok = True
    try:
        from onenote_decide import MODEL, call
        reply = call("한 단어로만 답하라: ok", max_tokens=300, system="JSON 없이 한 단어만 출력한다.")
        print(f"model {MODEL}: {str(reply)[:40]!r}")
    except SystemExit as err:
        print(f"model 실패: {err}")
        ok = False
    except Exception as err:  # noqa: BLE001
        print(f"model 실패: {type(err).__name__}: {str(err)[:120]}")
        ok = False
    try:
        from onenote_delta import GRAPH, access_token
        import urllib.request
        req = urllib.request.Request(f"{GRAPH}/me/onenote/notebooks?$select=displayName&$top=5",
                                     headers={"Authorization": f"Bearer {access_token()}"})
        with urllib.request.urlopen(req, timeout=60) as res:
            data = json.loads(res.read())
        print(f"graph: 노트북 {len(data.get('value', []))}개")
    except Exception as err:  # noqa: BLE001
        print(f"graph 실패: {type(err).__name__}: {str(err)[:120]}")
        ok = False
    repos = Path.home() / ".local" / "state" / "onenote" / "repos"
    found = sorted(p.name for p in repos.glob("*") if (p / ".git").exists()) if repos.exists() else []
    print(f"repos: {found}")
    return 0 if ok else 1


def main() -> int:
    args = sys.argv[1:]
    get = lambda k, d="": args[args.index(k) + 1] if k in args else d
    notebook = get("--notebook")
    since = get("--since", "1d")
    out_dir = Path(get("--out-dir", "/tmp/opencode/jobs"))
    with_images = "--with-images" in args
    dry = "--dry-run" in args
    max_map = get("--max-map", "40")

    if "--self-test" in args:
        return self_test()

    work = Path("/tmp/opencode/daily")
    work.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S")

    # 1) 감지 + 바뀐 페이지 본문 캐시
    det_args = [PY, "scripts/onenote_changes.py", "--since", since, "--save-text"]
    if notebook:
        det_args += ["--notebook", notebook]
    ok, out = run("감지", det_args, timeout=2400)
    if not ok:
        return 1
    det = json.loads(out.splitlines()[-1]) if out else {}
    changed = det.get("changed") or []
    print(f"  변경 페이지 {len(changed)}개 (페이지 확인 {det.get('pages_checked')}개, "
          f"{det.get('seconds')}s, 재시도 {det.get('retries')}회)", flush=True)
    if not changed:
        print("변경 없음 — 종료", flush=True)
        return 0
    if dry:
        for c in changed[:20]:
            print(f"  - {c['section']} / {c['title']}", flush=True)
        return 0

    # 2) 매핑(모델) — 아직 판단 안 한 페이지만
    map_args = [PY, "scripts/onenote_map_model.py", "--limit", max_map]
    if notebook:
        map_args += ["--notebook", notebook]
    run("매핑", map_args, timeout=3600)

    jobs: list[Path] = []
    out_dir.mkdir(parents=True, exist_ok=True)

    # 3) 병합
    patch = work / "1-elaborate.json"
    if run("병합", [PY, "scripts/onenote_elaborate.py", "--out", str(patch)], timeout=3600)[0]:
        try:
            if json.loads(patch.read_text(encoding="utf-8")).get("updates"):
                jobs.append(patch)
        except json.JSONDecodeError:
            pass

    # 4) 그림
    if with_images:
        img = work / "2-images.json"
        if run("그림", [PY, "scripts/onenote_images_pull.py", "--out", str(img)], timeout=3600)[0]:
            try:
                if json.loads(img.read_text(encoding="utf-8")).get("updates"):
                    jobs.append(img)
            except json.JSONDecodeError:
                pass

    # 5) 문장 다듬기 — 병합이 건드린 파일만
    files: list[str] = []
    for job in jobs:
        for upd in json.loads(job.read_text(encoding="utf-8")).get("updates", []):
            target = f"{upd['repo']}/{upd['file']}"
            if target not in files:
                files.append(target)
    if files:
        polish = work / "3-polish.json"
        if run("문장", [PY, "scripts/onenote_polish.py", "--files", ",".join(files),
                       "--out", str(polish)], timeout=3600)[0]:
            try:
                if json.loads(polish.read_text(encoding="utf-8")).get("updates"):
                    jobs.append(polish)
            except json.JSONDecodeError:
                pass

    if not jobs:
        print("생성된 job 없음", flush=True)
        return 0

    written = []
    for i, job in enumerate(jobs, 1):
        label = {"1-elaborate": "병합", "2-images": "그림", "3-polish": "문장"}.get(job.stem, job.stem)
        dest = out_dir / f"{stamp}-{i}-{job.stem}.json"
        doc = json.loads(job.read_text(encoding="utf-8"))
        if not doc.get("source"):
            # 알림이 '?' 로 뜨지 않도록 페이지 이름을 최상위에 남긴다
            pages: list[str] = []
            for upd in doc.get("updates", []):
                name = (upd.get("source") or {}).get("page") or ""
                if name and name not in pages:
                    pages.append(name)
            doc["source"] = {"page": " · ".join(pages[:3]) or job.stem}
        dest.write_text(json.dumps(doc, ensure_ascii=False, indent=1), encoding="utf-8")
        n = len(json.loads(job.read_text(encoding="utf-8"))["updates"])
        written.append(str(dest))
        print(f"  job {i}: {label} · {n}건 → {dest.name}", flush=True)
    print(json.dumps({"jobs": written}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
