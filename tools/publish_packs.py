# SPDX-FileCopyrightText: 2026 Core Devices LLC
# SPDX-License-Identifier: Apache-2.0

"""GitHub Actions release orchestration. Draft first, verify assets, publish last."""

import argparse
import json
import os
import subprocess
import tempfile
from pathlib import Path

if __package__:
    from .release_packs import build, digest, git
else:
    from release_packs import build, digest, git


def gh(*args):
    return subprocess.check_output(["gh", *args], text=True).strip()


def newest_snapshot(repository):
    pages = json.loads(
        gh("api", f"repos/{repository}/releases?per_page=100", "--paginate", "--slurp")
    )
    candidates = [
        r
        for page in pages
        for r in page
        if r["tag_name"].startswith("packs-") and not r["draft"] and not r["prerelease"]
    ]
    # Do not use /latest: an operator may have rolled back the app-facing release.
    # Each new snapshot carries the cumulative version ledger, including removed locales.
    return max(candidates, key=lambda r: r["id"], default=None)


def current_main(repository, commit):
    head = gh("api", f"repos/{repository}/git/ref/heads/main", "--jq", ".object.sha")
    return head == commit


def publish(repository, output, tag, commit):
    if not current_main(repository, commit):
        print("A newer main commit exists; skipping publication")
        return
    gh(
        "release",
        "create",
        tag,
        "--repo",
        repository,
        "--target",
        commit,
        "--draft",
        "--title",
        f"Language packs {tag.removeprefix('packs-')}",
        "--notes",
        "",
    )
    assets = sorted(p for p in output.iterdir() if p.is_file())
    gh("release", "upload", tag, "--repo", repository, *(str(p) for p in assets))
    # Download from the draft API, so verification also works with immutable releases enabled.
    with tempfile.TemporaryDirectory(prefix="verify-packs-") as temp:
        gh("release", "download", tag, "--repo", repository, "--dir", temp)
        downloaded = Path(temp)
        if {p.name for p in downloaded.iterdir()} != {p.name for p in assets}:
            raise ValueError("Draft release asset list does not match the build")
        for asset in assets:
            if digest(asset.read_bytes()) != digest(
                (downloaded / asset.name).read_bytes()
            ):
                raise ValueError(f"Uploaded asset failed verification: {asset.name}")
    if not current_main(repository, commit):
        print(f"Main advanced during upload; leaving {tag} as an unpublished draft")
        return
    gh("release", "edit", tag, "--repo", repository, "--draft=false", "--latest")
    print(f"Published {tag}")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, default=Path("dist/release"))
    parser.add_argument("--publish", action="store_true")
    parser.add_argument("--rebuild", action="store_true")
    args = parser.parse_args()
    root = Path.cwd()
    repository = os.environ["GITHUB_REPOSITORY"]
    commit = git(root, "rev-parse", "HEAD")
    if args.publish:
        if os.environ.get("GITHUB_REF") != "refs/heads/main":
            raise ValueError("Publication is only allowed from main")
        if not current_main(repository, commit):
            print("Stale run; skipping")
            return
    tag = f"packs-{os.environ['GITHUB_RUN_ID']}-{os.environ['GITHUB_RUN_ATTEMPT']}"
    with tempfile.TemporaryDirectory(prefix="previous-packs-") as temp:
        previous = newest_snapshot(repository)
        directory = None
        if previous:
            directory = Path(temp)
            gh(
                "release",
                "download",
                previous["tag_name"],
                "--repo",
                repository,
                "--dir",
                str(directory),
                "--pattern",
                "manifest.json",
                "--pattern",
                "*.pbl",
            )
        _, changed = build(
            root,
            args.output,
            tag=tag,
            repository=repository,
            previous=directory,
            rebuild=args.rebuild,
        )
    if args.publish and changed:
        publish(repository, args.output, tag, commit)
    else:
        print(
            "Validated snapshot; "
            + ("publication disabled" if not args.publish else "no release changes")
        )


if __name__ == "__main__":
    main()
