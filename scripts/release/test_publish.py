import unittest
from pathlib import Path
from unittest.mock import patch

import publish


REPO = "michibiki-io/TraefikCookiePathReplacement"


def pr(number, sha, branch="main", merged=True, repo=REPO):
    return {
        "number": number,
        "merge_commit_sha": sha,
        "merged_at": "2026-11-19T00:00:00Z" if merged else None,
        "base": {"ref": branch, "repo": {"full_name": repo}},
    }


class ReleaseTests(unittest.TestCase):
    def test_matches_recorded_merge_result_for_all_merge_methods(self):
        rows = [[
            pr(1, "merge-tip"),
            pr(2, "squash-tip"),
            pr(3, "rebase-tip"),
            pr(4, "unmerged-tip", merged=False),
            pr(5, "other-branch", branch="develop"),
            pr(6, "other-repo", repo="example/other"),
        ]]
        with patch.object(publish, "api_pages", return_value=rows), patch.object(publish.time, "sleep"):
            for sha in ("merge-tip", "squash-tip", "rebase-tip"):
                with self.subTest(sha=sha):
                    self.assertIn(sha, [p.merge_sha for p in publish.find_merged_pull_requests(REPO, sha)])
            for sha in ("rebase-intermediate", "direct-push", "unmerged-tip", "other-branch", "other-repo"):
                with self.subTest(sha=sha):
                    self.assertEqual(publish.find_merged_pull_requests(REPO, sha), [])

    def test_direct_push_does_not_read_tags_or_publish(self):
        with (
            patch.object(publish, "find_merged_pull_requests", return_value=[]),
            patch.object(publish, "output") as output,
            patch.object(publish.subprocess, "run") as run,
            patch("builtins.print") as report,
        ):
            publish.publish(REPO, "unmatched")
        output.assert_not_called()
        run.assert_not_called()
        self.assertTrue(report.call_args.args[0].startswith("::warning::"))

    def test_consecutive_merges(self):
        history = ["first", "second", "third"]
        prs = [publish.PullRequest(number, sha) for number, sha in enumerate(history, 1)]
        first = publish.next_tag(None, "first", ["feat: add support"])
        self.assertEqual(first.name, "v0.1.0")
        fixed, baseline, included = publish.release_range("second", history, prs, [first])
        self.assertIsNone(fixed)
        self.assertEqual(included, [prs[1]])
        second = publish.next_tag(baseline, "second", ["feat(plugin): add support"])
        self.assertEqual(second.name, "v0.2.0")
        _, baseline, included = publish.release_range("third", history, prs, [second, first])
        self.assertEqual(included, [prs[2]])
        self.assertEqual(publish.next_tag(baseline, "third", ["fix: preserve path"]).name, "v0.2.1")

    def test_out_of_order_merge_keeps_original_bump_and_notes(self):
        history = ["first", "breaking", "fix"]
        prs = [publish.PullRequest(number, sha) for number, sha in enumerate(history, 1)]
        first = publish.Tag((0, 1, 0), "first")
        _, baseline, included = publish.release_range("fix", history, list(reversed(prs)), [first])
        self.assertEqual(included, prs[1:])
        commits = ["feat!: use a new format", "fix: preserve path"]
        new = publish.next_tag(baseline, "fix", commits)
        self.assertEqual(new.name, "v1.0.0")
        notes = publish.release_notes([(p.number, [message]) for p, message in zip(included, commits)])
        self.assertIn("feat!: use a new format", notes)
        self.assertIn("fix: preserve path", notes)
        covered, _, included = publish.release_range("breaking", history, prs, [new, first])
        self.assertEqual(covered, new)
        self.assertEqual(included, [])

    def test_retry_reuses_tag_and_notes_after_later_release(self):
        history = ["first", "second", "third"]
        prs = [publish.PullRequest(number, sha) for number, sha in enumerate(history, 1)]
        first = publish.Tag((0, 1, 0), "first")
        second = publish.Tag((0, 1, 1), "second")
        third = publish.Tag((0, 2, 0), "third")
        _, original_baseline, original_prs = publish.release_range("second", history, prs, [first])
        fixed, baseline, included = publish.release_range("second", history, prs, [third, second, first])
        self.assertEqual((fixed, baseline, included), (second, original_baseline, original_prs))

        commands = []

        def record(command, **_kwargs):
            notes = Path(command[-1]).read_text()
            commands.append((command, notes))

        with (
            patch.object(publish, "find_merged_pull_requests", return_value=prs),
            patch.object(publish, "output", return_value="\n".join(history)),
            patch.object(publish, "stable_tags", return_value=[third, second, first]),
            patch.object(publish, "published_release", return_value=None),
            patch.object(publish, "original_commit_messages", return_value=["fix: keep path"]) as messages,
            patch.object(publish.subprocess, "run", side_effect=record),
        ):
            publish.publish(REPO, "second")

        messages.assert_called_once_with(REPO, 2)
        self.assertEqual(len(commands), 1)
        self.assertEqual(commands[0][0][:4], ["gh", "release", "create", "v0.1.1"])
        self.assertEqual(commands[0][1], publish.release_notes([(2, ["fix: keep path"])]))

    def test_breaking_footer_and_patch_for_other_commits(self):
        cases = {
            "fix: update\n\nBREAKING CHANGE: updated format": 0,
            "fix: update\n\nDescription.\n\nRefs: #1\nBREAKING-CHANGE: updated format": 0,
            "docs: update the guide": 2,
            "Update the guide\n\nBREAKING CHANGE: template example": 2,
            "fix: update\n\nExample:\nBREAKING CHANGE: sample\n\nEnd of example.": 2,
            "Feat(plugin): add support": 1,
        }
        for message, bump in cases.items():
            with self.subTest(message=message):
                self.assertEqual(publish.commit_bump(message), bump)


if __name__ == "__main__":
    unittest.main()
