#!/usr/bin/env python3
"""Catch common accidental private artifacts in the public source tree.

This is a guardrail, not a replacement for reviewing each publication diff.
It deliberately contains no private identifiers to search for.
"""

import hashlib
import ipaddress
from pathlib import Path
import re
import subprocess
import sys


ROOT = Path(__file__).resolve().parents[1]
IGNORED = {".git", ".venv", "__pycache__", "data", "captures", "dist"}
# These exact files were visually reviewed and stripped of embedded metadata.
# A changed image needs another review; an extension alone is not approval.
REVIEWED_IMAGES = {
    "docs/images/power-one-pvi-5000-outd-us-z-front.jpg":
        "4e40ea9c5a48528e18fca71b1739125451aa13ffaf3a64a20ca30aa392d86d12",
    "docs/images/original-solarcity-collector.jpg":
        "b7e5a39567b5ceefb6b18b45c4f061c2055a67c325d438450137a3a055e81254",
}
IMAGE_SUFFIXES = {".jpg", ".jpeg", ".png", ".webp", ".gif", ".avif", ".heic", ".heif"}
PATTERNS = [
    ("private key", re.compile(r"-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----")),
    ("GitHub token", re.compile(r"\b(?:gh[pousr]_[A-Za-z0-9]{30,}|github_pat_[A-Za-z0-9_]{30,})\b")),
    ("AWS access key", re.compile(r"\b(?:AKIA|ASIA)[A-Z0-9]{16}\b")),
    ("private home path", re.compile(r"/(?:home|Users)/[A-Za-z0-9_.-]+/")),
    ("real Digi EUI", re.compile(r"(?i)(?<![0-9a-f])0013a2[0-9a-f]{10}(?![0-9a-f])")),
]


def files():
    result = subprocess.run(["git", "ls-files", "-z"], cwd=ROOT,
                            capture_output=True, check=False)
    if result.returncode == 0 and result.stdout:
        return [ROOT / name for name in result.stdout.decode().split("\0") if name]
    return [path for path in ROOT.rglob("*") if path.is_file()
            and not IGNORED.intersection(path.relative_to(ROOT).parts)
            and not path.name.endswith(".local.json")]


def check(paths):
    failures = []
    for path in paths:
        relative = path.relative_to(ROOT)
        if path.suffix in {".sqlite3", ".sqlite", ".db", ".pcap", ".pcapng", ".log", ".pyc"} or (
                path.name.endswith(".local.json") or path.name.startswith(".env")
                or IGNORED.intersection(relative.parts)):
            failures.append(f"{relative}: private/runtime artifact tracked")
            continue
        if path.suffix.lower() in IMAGE_SUFFIXES:
            expected = REVIEWED_IMAGES.get(relative.as_posix())
            if expected is None or hashlib.sha256(path.read_bytes()).hexdigest() != expected:
                failures.append(f"{relative}: unreviewed or changed image; review privacy and strip metadata")
            continue
        try:
            content = path.read_text(encoding="utf-8")
        except UnicodeDecodeError:
            failures.append(f"{relative}: unreviewed binary artifact")
            continue
        # Upstream bundled code is reviewed by its hash and retained notices.
        if "vendor" in relative.parts:
            continue
        for label, pattern in PATTERNS:
            if pattern.search(content):
                failures.append(f"{relative}: possible {label}")
        for match in re.finditer(r"(?<![\w.])(?:\d{1,3}\.){3}\d{1,3}(?![\w.])", content):
            try:
                address = ipaddress.ip_address(match.group())
            except ValueError:
                continue
            # Loopback and wildcard bind examples are expected. Firmware versions
            # can resemble public IPs; only flag private network address blocks.
            private = any(address in ipaddress.ip_network(block) for block in (
                (0x0A000000, 8), (0xAC100000, 12), (0xC0A80000, 16), (0x64400000, 10)))
            if private:
                failures.append(f"{relative}: private network address")
        if path.suffix == ".md":
            for target in re.findall(r"\]\(([^)]+)\)", content):
                if "://" in target or target.startswith("#"):
                    continue
                target = target.split("#", 1)[0]
                if target and not (path.parent / target).exists():
                    failures.append(f"{relative}: broken local link")
    return failures


if __name__ == "__main__":
    failures = check(files())
    if failures:
        print("\n".join(sorted(set(failures))))
        sys.exit(1)
    print("Publication hygiene: no flagged private artifacts or broken local links")
