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
    from .release_packs import build, digest, git, merge_snapshot
else:
    from release_packs import build, digest, git, merge_snapshot


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


def download(repository, tag, directory):
    directory.mkdir(parents=True)
    gh(
        "release",
        "download",
        tag,
        "--repo",
        repository,
        "--dir",
        str(directory),
        "--pattern",
        "manifest.json",
        "--pattern",
        "*.pbl",
    )


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
    parser.add_argument(
        "--initial",
        action="store_true",
        help="Allow publishing without a previous snapshot (first release only)",
    )
    parser.add_argument(
        "--recover-from",
        metavar="TAG",
        help="Restore locales and version ledger from an older snapshot",
    )
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
            directory = Path(temp) / "previous"
            download(repository, previous["tag_name"], directory)
        elif args.publish and not args.initial:
            # Starting from nothing silently drops held locales and resets versions.
            raise ValueError(
                "No previous language pack snapshot found; refusing to publish "
                "(pass --initial for the very first release)"
            )
        if args.recover_from:
            if not directory:
                raise ValueError("--recover-from needs a newest snapshot to merge into")
            if args.recover_from == previous["tag_name"]:
                raise ValueError("--recover-from must name an older snapshot")
            recovered = Path(temp) / "recovered"
            download(repository, args.recover_from, recovered)
            restored = merge_snapshot(directory, recovered)
            print(
                f"Recovered from {args.recover_from}: {', '.join(restored) or 'none'}"
            )
        _, changed = build(
            root,
            args.output,
            tag=tag,
            repository=repository,
            previous=directory,
            rebuild=args.rebuild,
        )
    readiness = json.loads((args.output / "readiness.json").read_text())
    for language in readiness["languages"]:
        detail = "; ".join(language["reasons"]) or "Approved release checks passed"
        print(f"{language['locale']}: {language['status']} — {detail}")
    # The merged baseline differs from the live release, so recovery always publishes.
    if args.publish and (changed or args.recover_from):
        publish(repository, args.output, tag, commit)
    else:
        print(
            "Validated snapshot; "
            + ("publication disabled" if not args.publish else "no release changes")
        )


if __name__ == "__main__":
    main()
