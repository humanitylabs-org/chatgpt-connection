#!/usr/bin/env python3
"""Bounded offline acceptance runner for the forum light lane. No deployment.

Source and baseline must be independently staged Git checkouts. This runner only
uses loopback test fixtures, local files, git readback and Python subprocesses.
"""
import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import time

BASELINE = "10887eba131b738b865c8725d72172a8c5755d14"
ROOT = Path(__file__).resolve().parent.parent


def git(root, *args):
    return subprocess.check_output(["git", "-C", str(root), *args], text=True).strip()


def manifest(root):
    names = subprocess.check_output(["git", "-C", str(root), "ls-files", "-z"]).decode().split("\0")
    return {name: hashlib.sha256((root / name).read_bytes()).hexdigest() for name in names if name}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--expected-revision", required=True)
    ap.add_argument("--baseline", type=Path, required=True)
    ap.add_argument("--evidence", type=Path, required=True)
    args = ap.parse_args()
    if not re.fullmatch(r"[0-9a-f]{40}", args.expected_revision):
        raise SystemExit("expected full source SHA")
    assert git(ROOT, "rev-parse", "HEAD") == args.expected_revision, "candidate identity drift"
    assert not git(ROOT, "status", "--porcelain"), "candidate must be clean"
    assert git(args.baseline, "rev-parse", "HEAD") == BASELINE, "wrong baseline"
    assert not git(args.baseline, "status", "--porcelain"), "baseline must be clean"
    args.evidence.mkdir(parents=True, exist_ok=False)
    work = args.evidence / "scratch"
    work.mkdir()
    original = manifest(ROOT)
    (args.evidence / "source-manifest.json").write_text(json.dumps(original, indent=2) + "\n")
    env = {"PATH": os.defpath, "HOME": str(work), "TMPDIR": str(work), "APP_DATA_DIR": str(work / "unused-app-data"),
           "WIZARD_GRANTS_FILE": "", "PYTHONDONTWRITEBYTECODE": "1", "PYTHONUNBUFFERED": "1",
           "NO_PROXY": "127.0.0.1,localhost"}
    steps = []
    receipt = {"candidate": args.expected_revision, "baseline": BASELINE, "steps": steps,
               "started_epoch": time.time(), "runner_sha256": original["tools/verify-gateway-admission.py"],
               "scope": "offline synthetic loopback; no live login, inference, install or restart"}

    def save():
        (args.evidence / "receipt.json").write_text(json.dumps(receipt, indent=2) + "\n")

    def run(name, argv, cwd=ROOT, negative=False, timeout=180):
        started = time.monotonic()
        with (args.evidence / (name + ".log")).open("wb") as log:
            result = subprocess.run(argv, cwd=cwd, env=env, stdout=log, stderr=subprocess.STDOUT, timeout=timeout)
        text = (args.evidence / (name + ".log")).read_text()
        count = re.findall(r"Ran (\d+) tests? in", text)
        row = {"name": name, "argv": argv, "exit_code": result.returncode,
               "seconds": round(time.monotonic() - started, 3), "tests": int(count[-1]) if count else None,
               "log_sha256": hashlib.sha256(text.encode()).hexdigest()}
        steps.append(row)
        save()
        if negative:
            assert result.returncode == 1, "baseline must fail an assertion, not abort"
            assert "ninth request was rejected instead of waiting" in text, "wrong baseline failure"
            assert row["tests"] == 1, "baseline selector must execute exactly one regression"
        else:
            assert result.returncode == 0, name + " failed; read phase log"
            if name.startswith("gateway-"):
                assert row["tests"] == 15, "gateway discovery must run all fifteen regressions"
            elif name in {"package", "chatgpt", "inference", "all-discovery"}:
                assert row["tests"] and row["tests"] > 0, "test phase discovered no tests"
        print(json.dumps(row), flush=True)

    save()
    try:
        # Execute the new real-handler regression against the unmodified live
        # source. Copy into a disposable tree, never edit the staged baseline.
        old = args.evidence / "baseline-negative"
        shutil.copytree(args.baseline, old, ignore=shutil.ignore_patterns(".git", "__pycache__"))
        shutil.copy2(ROOT / "test_gateway_admission.py", old / "test_gateway_admission.py")
        run("negative-live-baseline", [sys.executable, "-m", "unittest", "-v",
            "test_gateway_admission.GatewayAdmissionTests.test_eight_held_streams_fifo_and_ceiling"], cwd=old, negative=True)
        run("gateway-discovery", [sys.executable, "-m", "unittest", "discover", "-v", "-p", "test_gateway*.py"])
        # Keep the existing CI's entrypoints, then the ordinary repository-wide
        # discovery route so no test is stranded in an undiscovered side script.
        run("package", [sys.executable, "test-package.py"])
        run("chatgpt", [sys.executable, "test-chatgpt.py"])
        run("inference", [sys.executable, "test-inference.py", "-v"])
        run("inline-check", [sys.executable, "mcp/inline.py", "--check"])
        run("all-discovery", [sys.executable, "-m", "unittest", "discover", "-v"])
        run("gateway-repeat", [sys.executable, "-m", "unittest", "discover", "-v", "-p", "test_gateway*.py"])
        assert manifest(ROOT) == original, "candidate source changed while testing"
        assert not git(ROOT, "status", "--porcelain"), "candidate checkout dirtied"
        assert not git(args.baseline, "status", "--porcelain"), "staged baseline dirtied"
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
