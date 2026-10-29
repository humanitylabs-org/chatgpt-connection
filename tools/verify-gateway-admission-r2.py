#!/usr/bin/env python3
"""Focused forum follow-up for the real Connection.open access/401 seam.

Only disposable loopback fixtures and local Git reads. No account, deployment,
service control or network-policy operations. The coordinator submits this job.
"""
import argparse
import ast
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import time

ROOT = Path(__file__).resolve().parent.parent
PREVIOUS = "4742b662af5bd0cc0bbde1d5736424b86c3ada9e"
FOCUSED = [
    "test_real_access_deadline_prevents_first_backend_send",
    "test_real_access_disconnect_prevents_first_backend_send",
    "test_real_access_401_retry_deadline_never_claims_no_attempt",
    "test_real_access_401_retry_disconnect_prevents_second_backend_send",
    "test_real_access_401_retry_with_live_caller_still_succeeds",
]


def git(root, *args):
    return subprocess.check_output(["git", "-C", str(root), *args], text=True).strip()


def manifest(root):
    names = subprocess.check_output(["git", "-C", str(root), "ls-files", "-z"]).decode().split("\0")
    return {n: hashlib.sha256((root / n).read_bytes()).hexdigest() for n in names if n}


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--expected-revision", required=True)
    ap.add_argument("--previous-source", required=True, type=Path)
    ap.add_argument("--previous-evidence", required=True, type=Path)
    ap.add_argument("--evidence", required=True, type=Path)
    args = ap.parse_args()
    assert re.fullmatch(r"[0-9a-f]{40}", args.expected_revision)
    assert git(ROOT, "rev-parse", "HEAD") == args.expected_revision
    assert not git(ROOT, "status", "--porcelain")
    assert git(args.previous_source, "rev-parse", "HEAD") == PREVIOUS
    assert not git(args.previous_source, "status", "--porcelain")
    original, previous = manifest(ROOT), manifest(args.previous_source)
    prior = json.loads((args.previous_evidence / "receipt.json").read_text())
    assert prior["status"] == "PASS" and prior["candidate"] == PREVIOUS
    assert json.loads((args.previous_evidence / "source-manifest.json").read_text()) == previous
    for row in prior["steps"]:
        log = args.previous_evidence / (row["name"] + ".log")
        assert digest(log) == row["log_sha256"], "prior phase log drift"
        counts = re.findall(r"Ran (\d+) tests? in", log.read_text())
        assert (int(counts[-1]) if counts else None) == row["tests"]
        assert row["exit_code"] == (1 if row["name"] == "negative-live-baseline" else 0)
    # Only the old negative FIFO result and inline check are inherited. Everything
    # reaching the changed handler/transport is rerun, including OAuth persistence.
    for name in ("mcp/front.py", "mcp/tools.py", "mcp/inline.py", "runtipi/docker-compose.yml"):
        assert original[name] == previous[name], "inline evidence dependency changed"
    def admission_tree(root):
        tree = ast.parse((root / "server.py").read_text())
        return ast.dump(next(n for n in tree.body if isinstance(n, ast.ClassDef) and n.name == "GatewayAdmission"))
    assert admission_tree(ROOT) == admission_tree(args.previous_source)
    args.evidence.mkdir(parents=True, exist_ok=False)
    scratch = args.evidence / "scratch"
    scratch.mkdir()
    env = {"PATH": os.defpath, "HOME": str(scratch), "TMPDIR": str(scratch),
           "APP_DATA_DIR": str(scratch / "unused-app-data"), "WIZARD_GRANTS_FILE": "",
           "PYTHONDONTWRITEBYTECODE": "1", "PYTHONUNBUFFERED": "1", "NO_PROXY": "127.0.0.1,localhost"}
    receipt = {"candidate": args.expected_revision, "previous": PREVIOUS, "started_epoch": time.time(),
               "runner_sha256": digest(Path(__file__)), "steps": [],
               "scope": "offline real-owner synthetic OAuth and loopback backend; no live credentials or activation",
               "inherited": {"receipt_sha256": digest(args.previous_evidence / "receipt.json"),
                             "source_manifest_sha256": digest(args.previous_evidence / "source-manifest.json"),
                             "steps": [r for r in prior["steps"] if r["name"] in {"negative-live-baseline", "inline-check"}]}}
    (args.evidence / "source-manifest.json").write_text(json.dumps(original, indent=2) + "\n")

    def save():
        (args.evidence / "receipt.json").write_text(json.dumps(receipt, indent=2) + "\n")

    def run(name, tail, count, cwd=ROOT, negative=False):
        log = args.evidence / (name + ".log")
        argv = [sys.executable, *tail]
        start = time.monotonic()
        with log.open("wb") as output:
            result = subprocess.run(argv, cwd=cwd, env=env, stdout=output, stderr=subprocess.STDOUT, timeout=120)
        text = log.read_text()
        counts = re.findall(r"Ran (\d+) tests? in", text)
        row = {"name": name, "argv": argv, "exit_code": result.returncode,
               "tests": int(counts[-1]) if counts else None,
               "log_sha256": digest(log), "seconds": round(time.monotonic() - start, 3)}
        receipt["steps"].append(row)
        save()
        assert row["tests"] == count, "wrong test count: " + name
        assert result.returncode == (1 if negative else 0), "wrong exit: " + name
        if negative:
            assert text.count(" : stale caller dispatched after access") == count, "wrong negative assertion"
            assert "FAILED (failures=4)" in text and "ERROR:" not in text
        print(json.dumps(row), flush=True)

    selectors = ["test_gateway_admission.GatewayAdmissionTests." + name for name in FOCUSED]
    save()
    try:
        run("access-seam-focused", ["-m", "unittest", "-v", *selectors], 5)
        old = args.evidence / "previous-negative"
        shutil.copytree(args.previous_source, old, ignore=shutil.ignore_patterns(".git", "__pycache__"))
        shutil.copy2(ROOT / "test_gateway_admission.py", old / "test_gateway_admission.py")
        run("negative-previous-access-seam", ["-m", "unittest", "-v", *selectors[:4]], 4, cwd=old, negative=True)
        run("all-discovery", ["-m", "unittest", "discover", "-v"], 15)
        run("chatgpt", ["test-chatgpt.py"], 21)
        run("inference", ["test-inference.py", "-v"], 5)
        run("package", ["test-package.py"], 3)
        run("access-seam-repeat", ["-m", "unittest", "-v", *selectors], 5)
        assert manifest(ROOT) == original and manifest(args.previous_source) == previous
        assert not git(ROOT, "status", "--porcelain")
        assert not git(args.previous_source, "status", "--porcelain")
        receipt["status"] = "PASS"
    except BaseException as exc:
        receipt["status"] = "FAIL"
        receipt["failure"] = str(exc)
        raise
    finally:
        receipt["finished_epoch"] = time.time()
        save()
        print(json.dumps({"status": receipt["status"], "receipt": str(args.evidence / "receipt.json")}), flush=True)


if __name__ == "__main__":
    main()
