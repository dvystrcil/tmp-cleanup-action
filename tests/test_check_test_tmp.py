# Tests for check_test_tmp.sh.   python3 tests/test_check_test_tmp.py      # "Ran N tests" + "OK"
import os, shutil, subprocess, tempfile, unittest

SCRIPT = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "check_test_tmp.sh")


class CheckTestTmp(unittest.TestCase):
    def setUp(self):
        # the script's own scratch goes under here, so a leak of ITS dir is visible too
        self.outer = tempfile.mkdtemp(prefix="check-test-tmp-test-")
        self.addCleanup(shutil.rmtree, self.outer, True)

    def run_it(self, *args):
        return subprocess.run(["bash", SCRIPT, *args], capture_output=True, text=True,
                              env=dict(os.environ, TMPDIR=self.outer, OUTER=self.outer))

    def test_a_clean_command_passes(self):
        r = self.run_it("--", "bash", "-c", 'd=$(mktemp -d); rm -rf "$d"')
        self.assertEqual((r.returncode, r.stdout.strip()), (0, "check-test-tmp: OK"))

    def test_a_leaking_command_fails_and_names_what_it_left(self):
        r = self.run_it("--", "bash", "-c", "mktemp -d >/dev/null; mktemp >/dev/null")
        self.assertEqual(r.returncode, 1, r.stdout)
        self.assertEqual(r.stdout.count("LEFT   tmp."), 2)
        self.assertIn("check-test-tmp: FAILED (2 entries left in TMPDIR)", r.stdout)

    def test_the_command_sees_the_empty_tmpdir_not_the_callers(self):
        r = self.run_it("--", "bash", "-c", 'test "$TMPDIR" != "$OUTER" && test -z "$(ls -A "$TMPDIR")"')
        self.assertEqual(r.returncode, 0, r.stdout)

    def test_a_dotfile_left_behind_counts(self):
        r = self.run_it("--", "bash", "-c", 'touch "$TMPDIR/.lock"')
        self.assertEqual(r.returncode, 1)
        self.assertIn("LEFT   .lock", r.stdout)

    def test_ignored_names_may_remain(self):
        r = self.run_it("--ignore", "node-compile-cache", "--", "bash", "-c", 'mkdir "$TMPDIR/node-compile-cache"')
        self.assertEqual(r.returncode, 0, r.stdout)
        r = self.run_it("--ignore", "cache-*", "--", "bash", "-c", 'mkdir "$TMPDIR/cache-1" "$TMPDIR/other"')
        self.assertEqual(r.returncode, 1)
        self.assertIn("LEFT   other", r.stdout); self.assertNotIn("cache-1", r.stdout)

    def test_a_failing_command_passes_its_exit_code_through(self):
        r = self.run_it("--", "bash", "-c", "exit 7")
        self.assertEqual(r.returncode, 7)
        self.assertNotIn("check-test-tmp: OK", r.stdout)

    def test_the_script_removes_its_own_scratch(self):
        self.run_it("--", "bash", "-c", "mktemp >/dev/null")
        self.run_it("--", "true")
        self.assertEqual(os.listdir(self.outer), [])

    def test_usage_errors_exit_2(self):
        self.assertEqual(self.run_it().returncode, 2)
        self.assertEqual(self.run_it("--bogus").returncode, 2)


if __name__ == "__main__":
    unittest.main(verbosity=2)
