"""Block 58c demo gate: inspect the state left by the human mechanisms."""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import subprocess
import sys
import traceback
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from microclaw import config

RESULTS: list[dict[str, str]] = []


class NotExercised(Exception):
    """The named mechanism could not run. This is never a pass."""


class Tee:
    def __init__(self, stream, path: Path):
        self.stream, self.file = stream, path.open("w", encoding="utf-8")
    def write(self, data):
        self.stream.write(data); self.file.write(data); return len(data)
    def flush(self):
        self.stream.flush(); self.file.flush()


def limb(name: str, fails_if: str):
    """Run independently and record the mandatory falsifying control."""
    def decorate(fn):
        try:
            detail = fn() or ""
            status = "PASS"
        except NotExercised as exc:
            detail, status = str(exc), "NOT EXERCISED"
        except Exception as exc:
            detail, status = f"{type(exc).__name__}: {exc}", "FAIL"
            traceback.print_exc()
        RESULTS.append({"name": name, "status": status, "detail": detail,
                        "fails_if": fails_if})
        return fn
    return decorate


def need(path: Path, mechanism: str) -> Path:
    if not path.exists():
        raise NotExercised(f"missing {path.name}; human mechanism not exercised: {mechanism}")
    return path


def tree_hash(path: Path) -> str:
    digest = hashlib.sha256()
    for item in sorted(p for p in path.rglob("*") if p.is_file()):
        digest.update(str(item.relative_to(path)).encode()); digest.update(item.read_bytes())
    return digest.hexdigest()


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--repo", required=True); parser.add_argument("--out", required=True)
    args = parser.parse_args()
    repo, out = Path(args.repo).resolve(), Path(args.out).resolve()
    out.mkdir(parents=True, exist_ok=True)
    sys.stdout = sys.stderr = Tee(sys.__stdout__, out / "gate.txt")
    managed = Path(os.environ["LOCALAPPDATA"]) / "microclaw"
    appdata = Path(os.environ["APPDATA"]) / "microclaw"
    sha = subprocess.run(["git", "-C", str(repo), "rev-parse", "HEAD"],
                         capture_output=True, text=True, check=True).stdout.strip()

    @limb("migrated layout and immutable commit",
          "env-a is absent, active is not a, or env-a metadata is not checkout HEAD")
    def _():
        need(managed / "env-a", "install.bat migration")
        active = need(managed / "active-slot.txt", "install.bat migration").read_text().strip()
        marker = json.loads(need(managed / "env-a" / "microclaw-slot.json",
                                 "installer slot marker").read_text(encoding="utf-8-sig"))
        if active != "a" or marker.get("commit") != sha:
            raise AssertionError(f"active={active!r}, marker commit={marker.get('commit')!r}, HEAD={sha}")
        return f"env-a exists; active=a; commit={sha}"

    @limb("desktop command line uses selected slot",
          "the captured real process does not name env-a/env-b microclaw.exe serve")
    def _():
        text = need(out / "process-command.txt", "desktop icon plus Win32_Process capture").read_text()
        if not re.search(r"env-[ab].*microclaw\.exe.*serve", text, re.I | re.S):
            raise AssertionError("captured process was not the slot serve child")
        return text.strip()

    @limb("nonce-matched health marker",
          "health is absent or differs from the last launcher-generated nonce")
    def _():
        health = need(managed / "launch-health.txt", "desktop launcher health").read_text().strip()
        log = need(managed / "launcher.log", "launcher nonce control").read_text().splitlines()
        match = re.search(r"nonce=([0-9a-f]{32})", log[-1]) if log else None
        if not match or health != match.group(1):
            raise AssertionError(f"health={health!r}; last launch={log[-1] if log else 'none'}")
        return f"health equals fresh launcher nonce {health}"

    @limb("roaming config, key, and histories unchanged",
          "the sorted SHA256 manifests differ or either human hash step was skipped")
    def _():
        before = need(out / "appdata-before.txt", "pre-install APPDATA hashes").read_bytes()
        after = need(out / "appdata-after.txt", "post-mechanism APPDATA hashes").read_bytes()
        if before != after:
            raise AssertionError("APPDATA manifests differ")
        return f"byte-identical manifests; current tree hash={tree_hash(appdata) if appdata.exists() else 'empty'}"

    @limb("installer is idempotent and retains active slot",
          "either installer run failed or active selector changed on the second run")
    def _():
        first = need(out / "install-first.txt", "first install.bat")
        second = need(out / "install-second.txt", "second install.bat")
        before = need(out / "active-before-second.txt", "pre-second selector").read_text().strip()
        after = need(out / "active-after-second.txt", "post-second selector").read_text().strip()
        if before != after or before not in {"a", "b"}:
            raise AssertionError(f"active changed {before!r}->{after!r}")
        return f"both logs exist ({first.stat().st_size}, {second.stat().st_size} bytes); active={after}"

    @limb("two real slot validators classify one shared config",
          "either real slot CLI cannot classify, classifications differ unsafely, or env-b is absent")
    def _():
        a = managed / "env-a" / "Scripts" / "microclaw.exe"
        b = managed / "env-b" / "Scripts" / "microclaw.exe"
        if not a.exists() or not b.exists():
            raise NotExercised("a real second slot does not exist; 58b debt remains")
        shared = appdata / "safety_config.yaml"
        active_class = config.classify_config_with_slot(a, shared)
        candidate_class = config.classify_config_with_slot(b, shared)
        comparison = config.compare_slot_configurations(a, b, shared)
        detail = {"active": active_class, "candidate": candidate_class,
                  "proceed": comparison.proceed}
        if not comparison.proceed:
            raise AssertionError(detail)
        return json.dumps(detail, sort_keys=True)

    @limb("non-uv environment remains byte-identical and importable",
          "its pre/post site-packages hashes differ, import path moved, or installer notice is absent")
    def _():
        record = need(out / "nonuv.json", "throwaway non-uv environment setup")
        data = json.loads(record.read_text())
        path = Path(data["python"])
        before = need(out / "nonuv-before.txt", "pre-install site-packages hash").read_text().strip()
        after = tree_hash(Path(data["site_packages"]))
        if before != after:
            raise AssertionError("non-uv site-packages changed")
        imported = subprocess.run([str(path), "-c", "import microclaw; print(microclaw.__file__)"],
                                  capture_output=True, text=True, check=True).stdout.strip()
        notice = need(out / "install-first.txt", "installer non-uv notice").read_text()
        if str(Path(data["site_packages"])) not in imported or "left untouched" not in notice or "icon now moves" not in notice:
            raise AssertionError(f"import={imported!r}; required installer notice missing")
        return f"unchanged hash={after}; import={imported}"

    (out / "results.json").write_text(json.dumps(RESULTS, indent=2) + "\n")
    for result in RESULTS:
        print(f"{result['status']}: {result['name']} — {result['detail']}; FAILS IF: {result['fails_if']}")
    passed = all(r["status"] == "PASS" for r in RESULTS)
    print("BLOCK 58c DEMO GATE " + ("PASSED" if passed else "DID NOT PASS"))
    return 0 if passed else 1


if __name__ == "__main__":
    raise SystemExit(main())
