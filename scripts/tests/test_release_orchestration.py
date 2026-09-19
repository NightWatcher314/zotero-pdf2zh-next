"""Exercise release.sh's actual orchestration with offline publisher substitutes."""
from pathlib import Path
import os
import subprocess
import tempfile
import unittest

SCRIPT = Path(__file__).resolve().parents[1] / "release.sh"
ORCHESTRATION = SCRIPT.read_text().split(
    "# Run independent publishers concurrently; promote automatic updates only on success.\n",
    1,
)[1]

PRELUDE = r'''
set -euo pipefail
TAG=v-test
COMMIT=test-commit
die() { echo "$*" >&2; exit 1; }
record() { echo "$1" >> "$EVENTS"; }
publish_docker() {
    record docker-start
    touch "$STATE/docker-start"
    if [[ "$UPDATE_TAP" == 1 && "$PUSH" == 1 ]]; then
        for _ in {1..100}; do
            [[ -f "$STATE/brew-start" ]] && break
            sleep 0.01
        done
        test -f "$STATE/brew-start"
    fi
    if [[ "$FAIL_DOCKER" == 1 ]]; then
        false
        record forbidden-after-docker-failure
    fi
    record docker-end
}
publish_homebrew() (
    record brew-start
    touch "$STATE/brew-start"
    if [[ "$PUBLISH_DOCKER" == 1 ]]; then
        for _ in {1..100}; do
            [[ -f "$STATE/docker-start" ]] && break
            sleep 0.01
        done
        test -f "$STATE/docker-start"
    fi
    if [[ "$FAIL_BREW" == 1 ]]; then
        false
        record forbidden-after-brew-failure
    fi
    sleep 0.03
    record brew-end
)
publish_update_manifest() { record promote; }
'''


class ReleaseOrchestrationTests(unittest.TestCase):
    def run_release(self, **overrides):
        with tempfile.TemporaryDirectory() as directory:
            events = Path(directory) / "events"
            events.touch()
            env = {
                **os.environ,
                "STATE": directory,
                "EVENTS": str(events),
                "PUBLISH_DOCKER": "1",
                "UPDATE_TAP": "1",
                "PUSH": "1",
                "PUBLISH_RELEASE": "1",
                "FAIL_DOCKER": "0",
                "FAIL_BREW": "0",
                **overrides,
            }
            result = subprocess.run(
                ["bash", "-c", PRELUDE + ORCHESTRATION],
                env=env, capture_output=True, text=True, timeout=5,
            )
            return result, events.read_text().splitlines()

    def test_publishers_overlap_and_manifest_is_last(self):
        result, events = self.run_release()
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(set(events[:2]), {"docker-start", "brew-start"})
        self.assertEqual(events[-1], "promote")
        self.assertIn("docker-end", events)
        self.assertIn("brew-end", events)

    def test_failures_block_promotion_and_other_publisher_is_awaited(self):
        for failures in (
            {"FAIL_DOCKER": "1"},
            {"FAIL_BREW": "1"},
            {"FAIL_DOCKER": "1", "FAIL_BREW": "1"},
        ):
            with self.subTest(failures=failures):
                result, events = self.run_release(**failures)
                self.assertNotEqual(result.returncode, 0)
                self.assertNotIn("promote", events)
                self.assertFalse(any(e.startswith("forbidden") for e in events))
                for key, name, event in (
                    ("FAIL_DOCKER", "Docker", "docker-end"),
                    ("FAIL_BREW", "Homebrew", "brew-end"),
                ):
                    if key in failures:
                        self.assertIn(f"{name} publication failed", result.stderr)
                    else:
                        self.assertIn(event, events)

    def test_channel_opt_outs(self):
        for options, absent in (
            ({"PUBLISH_DOCKER": "0"}, "docker-start"),
            ({"UPDATE_TAP": "0"}, "brew-start"),
            ({"UPDATE_TAP": "1", "PUSH": "0", "PUBLISH_DOCKER": "0", "PUBLISH_RELEASE": "0"}, "brew-start"),
        ):
            with self.subTest(options=options):
                result, events = self.run_release(**options)
                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertNotIn(absent, events)
                if options.get("PUBLISH_RELEASE") == "0":
                    self.assertNotIn("promote", events)
                else:
                    self.assertEqual(events[-1], "promote")

    def test_no_release_does_not_promote(self):
        result, events = self.run_release(PUBLISH_RELEASE="0", PUBLISH_DOCKER="0")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("brew-end", events)
        self.assertNotIn("promote", events)


class HomebrewWorkflowGateTests(unittest.TestCase):
    def test_whole_run_is_required_before_check_summary(self):
        source = SCRIPT.read_text()
        gate = source[source.index('    TAP_TEST_RUN=""'):source.index('    PREVIOUS_PUBLISH_RUN=')]
        for fail in ("0", "1"):
            with self.subTest(fail=fail), tempfile.TemporaryDirectory() as directory:
                events = Path(directory) / "events"
                env = {**os.environ, "EVENTS": str(events), "FAIL": fail}
                mock = r'''
set -euo pipefail
TAP_REPO=test/tap
TAP_HEAD_SHA=expected-head
TAP_PR_NUMBER=13
die() { exit 1; }
gh() {
    if [[ "$1 $2" == "run list" ]]; then
        [[ " $* " == *" --workflow tests.yml "* ]] || return 98
        [[ " $* " == *" --event pull_request "* ]] || return 98
        [[ " $* " == *" --commit expected-head "* ]] || return 98
        echo 123
    elif [[ "$1 $2" == "run watch" ]]; then
        [[ "$3" == 123 ]]
        echo workflow >> "$EVENTS"
        [[ "$FAIL" == 0 ]]
    elif [[ "$1 $2" == "pr checks" ]]; then
        echo checks >> "$EVENTS"
    else
        exit 98
    fi
}
'''
                result = subprocess.run(
                    ["bash", "-c", mock + gate + '\necho publish >> "$EVENTS"'],
                    env=env, capture_output=True, text=True, timeout=5,
                )
                if fail == "1":
                    self.assertNotEqual(result.returncode, 0)
                    self.assertEqual(events.read_text().splitlines(), ["workflow"])
                else:
                    self.assertEqual(result.returncode, 0, result.stderr)
                    self.assertEqual(events.read_text().splitlines(), ["workflow", "checks", "publish"])


if __name__ == "__main__":
    unittest.main()
