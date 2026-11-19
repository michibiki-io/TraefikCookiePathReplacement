"""Publish a source release for a main push identified as a merged pull request."""

import json
import os
import re
import subprocess
import tempfile
import time
from dataclasses import dataclass
from pathlib import Path


STABLE_TAG = re.compile(r"v(0|[1-9]\d*)\.(0|[1-9]\d*)\.(0|[1-9]\d*)")
CONVENTIONAL_SUBJECT = re.compile(r"^([a-z][a-z0-9-]*)(?:\([^()]+\))?(!)?: \S", re.IGNORECASE)
FOOTER_START = re.compile(r"^(?:BREAKING CHANGE|[A-Za-z0-9-]+)(?:: | #)")
BREAKING_FOOTER = re.compile(r"^(?:BREAKING CHANGE|BREAKING-CHANGE): \S", re.MULTILINE)
COMMIT_QUERY = """
query($owner:String!, $repo:String!, $number:Int!, $endCursor:String) {
  repository(owner:$owner, name:$repo) {
    pullRequest(number:$number) {
      commits(first:100, after:$endCursor) {
        nodes { commit { message } }
        pageInfo { hasNextPage endCursor }
      }
    }
  }
}
"""


@dataclass(frozen=True)
class PullRequest:
    number: int
    merge_sha: str


@dataclass(frozen=True)
class Tag:
    version: tuple[int, int, int]
    sha: str

    @property
    def name(self):
        return "v{}.{}.{}".format(*self.version)


def output(*args):
    return subprocess.check_output(args, text=True)


def api_pages(endpoint, *args):
    return json.loads(output("gh", "api", "--paginate", "--slurp", endpoint, *args))


def merged_pull_requests(rows, repository):
    return [
        PullRequest(pr["number"], pr["merge_commit_sha"])
        for pr in rows
        if pr["merged_at"]
        and pr["merge_commit_sha"]
        and pr["base"]["ref"] == "main"
        and pr["base"]["repo"]["full_name"].casefold() == repository.casefold()
    ]


def find_merged_pull_requests(repository, target):
    endpoint = f"repos/{repository}/pulls?state=closed&base=main&per_page=100"
    # The push can arrive before GitHub exposes the merged PR through the API.
    for delay in (0, 2, 4, 8):
        if delay:
            time.sleep(delay)
        pages = api_pages(endpoint)
        pull_requests = merged_pull_requests((pr for page in pages for pr in page), repository)
        if any(pr.merge_sha == target for pr in pull_requests):
            return pull_requests
    return []


def stable_tags(history):
    tags = []
    for name in output("git", "tag", "--list").splitlines():
        match = STABLE_TAG.fullmatch(name)
        if match:
            sha = output("git", "rev-parse", f"refs/tags/{name}^{{commit}}").strip()
            if sha in history:
                tags.append(Tag(tuple(map(int, match.groups())), sha))
    return sorted(tags, key=lambda tag: tag.version, reverse=True)


def commit_bump(message):
    subject, _, body = message.partition("\n")
    conventional = CONVENTIONAL_SUBJECT.match(subject)
    if not conventional:
        return 2
    footer = body.strip("\n").rsplit("\n\n", 1)[-1] if body.startswith("\n") else ""
    if conventional[2] or (FOOTER_START.match(footer) and BREAKING_FOOTER.search(footer)):
        return 0
    if conventional[1].lower() == "feat":
        return 1
    return 2


def next_tag(baseline, target, commits):
    if baseline is None:
        return Tag((0, 1, 0), target)
    bump = min(commit_bump(message) for message in commits)
    parts = baseline.version
    version = parts[:bump] + (parts[bump] + 1,) + (0,) * (2 - bump)
    return Tag(version, target)


def release_range(target, history, pull_requests, tags):
    target_index = history.index(target)
    fixed = next((tag for tag in tags if tag.sha == target), None)
    newest = tags[0] if tags else None
    if fixed is None and newest and history.index(newest.sha) > target_index:
        return newest, None, []
    baseline = next((tag for tag in tags if history.index(tag.sha) < target_index), None)
    before = history.index(baseline.sha) + 1 if baseline else 0
    included_shas = {sha: index for index, sha in enumerate(history[before:target_index + 1])}
    included = sorted(
        (pr for pr in pull_requests if pr.merge_sha in included_shas),
        key=lambda pr: (included_shas[pr.merge_sha], pr.number),
    )
    return fixed, baseline, included


def original_commit_messages(repository, number):
    owner, name = repository.split("/")
    pages = api_pages(
        "graphql", "-f", f"query={COMMIT_QUERY}", "-f", f"owner={owner}", "-f", f"repo={name}",
        "-F", f"number={number}",
    )
    messages = [
        node["commit"]["message"]
        for page in pages
        for node in page["data"]["repository"]["pullRequest"]["commits"]["nodes"]
    ]
    if not messages:
        raise RuntimeError(f"No original commits were returned for PR #{number}")
    return messages


def release_notes(pr_commits):
    lines = []
    for number, commits in pr_commits:
        lines += [f"### Pull request #{number}", ""]
        for message in commits:
            subject, _, body = message.partition("\n")
            lines.append(f"- {subject}")
            if body.strip():
                lines.append("")
                lines.extend(f"  {line}" if line else "" for line in body.strip().splitlines())
                lines.append("")
        lines.append("")
    return "\n".join(lines).rstrip() + "\n"


def published_release(repository, tag):
    pages = api_pages(f"repos/{repository}/releases?per_page=100")
    for page in pages:
        for release in page:
            if release["tag_name"] == tag:
                if release["draft"] or release["prerelease"]:
                    raise RuntimeError(f"{tag} already has a draft or prerelease")
                return release["html_url"]
    return None


def publish(repository, target):
    pull_requests = find_merged_pull_requests(repository, target)
    if not pull_requests:
        print(f"::warning::No merged PR into {repository}:main matches {target}. "
              "Skipping release. If this push was a merge, rerun this job.")
        return

    history = output("git", "rev-list", "--first-parent", "--reverse", "origin/main").splitlines()
    if target not in history:
        raise RuntimeError(f"{target} is no longer on the first-parent history of origin/main")
    tags = stable_tags(set(history))
    existing, baseline, included = release_range(target, history, pull_requests, tags)
    if existing and existing.sha != target:
        print(f"{target} is included in the source tagged {existing.name} ({existing.sha}).")
        return
    if existing:
        url = published_release(repository, existing.name)
        if url:
            print(f"{existing.name} is already published at {url}.")
            return

    commits = [(pr.number, original_commit_messages(repository, pr.number)) for pr in included]
    tag = existing or next_tag(baseline, target, (message for _, messages in commits for message in messages))
    if existing is None:
        subprocess.run(
            ["git", "-c", "user.name=github-actions[bot]",
             "-c", "user.email=41898282+github-actions[bot]@users.noreply.github.com",
             "tag", "-a", tag.name, target, "-m", f"Release {tag.name}"],
            check=True,
        )
        ref = f"refs/tags/{tag.name}"
        subprocess.run(["git", "push", "origin", f"{ref}:{ref}"], check=True)

    with tempfile.TemporaryDirectory() as directory:
        notes = Path(directory) / "notes.md"
        notes.write_text(release_notes(commits), encoding="utf-8")
        subprocess.run(
            ["gh", "release", "create", tag.name, "--repo", repository, "--verify-tag",
             "--title", tag.name, "--notes-file", str(notes)],
            check=True,
        )


if __name__ == "__main__":
    publish(os.environ["GITHUB_REPOSITORY"], os.environ["GITHUB_SHA"])
