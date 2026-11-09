# Tests for check_tool_tmp.py.   python3 tests/test_check_tool_tmp.py      # "Ran N tests" + "OK"
import os, shutil, subprocess, sys, tempfile, textwrap, unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
import check_tool_tmp as c

CLI = os.path.join(ROOT, "check_tool_tmp.py")


def write(path, text):
    with open(path, "w") as f: f.write(text)


def py(src):
    return c.check_python("t.py", textwrap.dedent(src))


def sh(src):
    return c.check_shell("t.sh", textwrap.dedent(src))


class Python(unittest.TestCase):
    def test_a_mkdtemp_nobody_removes_is_a_leak(self):
        self.assertEqual([f.line for f in py("""\
            import tempfile
            def f():
                d = tempfile.mkdtemp()
                return d
            """)], [3])

    def test_rmtree_in_the_same_function_is_cleanup(self):
        self.assertEqual(py("""\
            import shutil, tempfile
            def f():
                d = tempfile.mkdtemp()
                try: pass
                finally: shutil.rmtree(d)
            """), [])

    def test_rmtree_in_another_function_does_not_count(self):
        self.assertEqual(len(py("""\
            import shutil, tempfile
            def f():
                return tempfile.mkdtemp()
            def g(d):
                shutil.rmtree(d)
            """)), 1)

    def test_a_cleanup_in_a_nested_function_does_not_count_for_the_outer_one(self):
        self.assertEqual(len(py("""\
            import shutil, tempfile
            def f():
                d = tempfile.mkdtemp()
                def g(): shutil.rmtree(d)
                return d
            """)), 1)

    def test_a_weakref_finalize_is_cleanup(self):
        self.assertEqual(py("""\
            import tempfile, weakref
            class P:
                def __init__(self):
                    self.dir = tempfile.mkdtemp()
                    self._f = weakref.finalize(self, print, self.dir)
            """), [])

    def test_mkstemp_needs_a_remove(self):
        self.assertEqual(len(py("import tempfile\nfd, p = tempfile.mkstemp()\n")), 1)
        self.assertEqual(py("import os, tempfile\ndef f():\n    fd, p = tempfile.mkstemp()\n    os.remove(p)\n"), [])
        self.assertEqual(py("import tempfile\ndef f(P):\n    fd, p = tempfile.mkstemp()\n    P(p).unlink()\n"), [])

    def test_named_temporary_file_only_leaks_with_delete_false(self):
        self.assertEqual(py("import tempfile\nwith tempfile.NamedTemporaryFile() as f: pass\n"), [])
        self.assertEqual(len(py("import tempfile\nf = tempfile.NamedTemporaryFile(delete=False)\n")), 1)
        self.assertEqual(py("import tempfile\nf = tempfile.NamedTemporaryFile(delete=True)\n"), [])

    def test_temporary_directory_is_always_fine(self):
        self.assertEqual(py("import tempfile\nwith tempfile.TemporaryDirectory() as d: pass\n"), [])

    def test_a_tmp_kept_marker_on_the_line_or_the_line_above_allows_it(self):
        self.assertEqual(py("import tempfile\nd = tempfile.mkdtemp()   # tmp-kept: the output, printed\n"), [])
        self.assertEqual(py("import tempfile\n# tmp-kept: the output, printed\nd = tempfile.mkdtemp()\n"), [])

    def test_a_marker_two_lines_up_does_not_count(self):
        self.assertEqual(len(py("import tempfile\n# tmp-kept: the output\nx = 1\nd = tempfile.mkdtemp()\n")), 1)

    def test_a_marker_needs_a_reason(self):
        self.assertEqual(len(py("import tempfile\nd = tempfile.mkdtemp()   # tmp-kept:\n")), 1)

    def test_a_bare_name_import_is_caught_too(self):
        self.assertEqual(len(py("from tempfile import mkdtemp\ndef f():\n    return mkdtemp()\n")), 1)


class Shell(unittest.TestCase):
    def test_mktemp_without_a_trap_is_a_leak(self):
        self.assertEqual([f.line for f in sh('#!/bin/bash\nT=$(mktemp -d)\necho hi\n')], [2])

    def test_an_exit_trap_that_removes_the_variable_is_cleanup(self):
        self.assertEqual(sh('#!/bin/bash\nT=$(mktemp -d); trap \'rm -rf "$T"\' EXIT\n'), [])

    def test_a_trap_calling_a_function_that_removes_the_variable_is_cleanup(self):
        self.assertEqual(sh("""\
            #!/bin/bash
            cleanup() {
                [[ -n "${TMP_PY:-}" ]] && rm -f "$TMP_PY" || true
            }
            trap cleanup EXIT
            TMP_PY=$(mktemp --suffix=.py)
            """), [])

    def test_a_trap_that_never_removes_that_variable_is_a_leak(self):
        self.assertEqual(len(sh("""\
            #!/bin/bash
            cleanup() { rm -f "$OTHER"; }
            trap cleanup EXIT
            T=$(mktemp -d)
            """)), 1)

    def test_an_rm_on_the_happy_path_counts(self):
        # a trap is better (an early exit leaks it), but the check is for temp files that are NEVER removed
        self.assertEqual(sh('#!/bin/bash\nT=$(mktemp)\necho hi > "$T"\nrm -f "$T"\n'), [])

    def test_an_rm_of_a_different_variable_does_not_count(self):
        self.assertEqual(len(sh('#!/bin/bash\nT=$(mktemp)\nrm -f "$TT" "$U"\n')), 1)

    def test_a_variable_added_to_an_array_the_trap_removes_is_cleanup(self):
        self.assertEqual(sh("""\
            #!/bin/bash
            TMPS=()
            trap 'rm -f "${TMPS[@]}" 2>/dev/null || true' EXIT
            OUT=$(mktemp)
            TMPS+=("$OUT" "${OUT}.meta")
            """), [])

    def test_only_the_array_the_variable_is_in_counts(self):
        self.assertEqual([f.line for f in sh("""\
            #!/bin/bash
            OUT=$(mktemp)
            KEEP+=("$OUT")
            X=$(mktemp)
            TMPS+=("$X")
            rm -f "${TMPS[@]}"
            """)], [2])

    def test_an_array_nobody_removes_does_not_count(self):
        self.assertEqual(len(sh('#!/bin/bash\nOUT=$(mktemp)\nTMPS+=("$OUT")\nrm -f "${OTHER[@]}"\n')), 1)

    def test_an_unassigned_mktemp_needs_a_trap_line_with_rm(self):
        self.assertEqual(len(sh('#!/bin/bash\ncmd > "$(mktemp)"\ntrap \'echo bye\' EXIT\n')), 1)
        self.assertEqual(sh('#!/bin/bash\ntrap \'rm -rf /tmp/x.*\' EXIT\ncmd > "$(mktemp /tmp/x.XXX)"\n'), [])

    def test_local_and_braced_forms(self):
        self.assertEqual(sh('#!/bin/bash\nf() {\n  local W=$(mktemp -d)\n  trap \'rm -rf "${W}"\' RETURN\n}\n'), [])

    def test_a_commented_out_trap_is_not_cleanup(self):
        self.assertEqual(len(sh('#!/bin/bash\nT=$(mktemp)\n# trap \'rm -f "$T"\' EXIT\n')), 1)

    def test_a_comment_mentioning_mktemp_is_not_a_call(self):
        self.assertEqual(sh('#!/bin/bash\n# we used to mktemp here\necho ok  # no mktemp now\n'), [])

    def test_a_marker_allows_it(self):
        self.assertEqual(sh('#!/bin/bash\nT=$(mktemp)   # tmp-kept: handed to the caller\n'), [])


class Cli(unittest.TestCase):
    def setUp(self):
        self.d = tempfile.mkdtemp(prefix="check-tool-tmp-test-")
        self.addCleanup(shutil.rmtree, self.d, True)
        subprocess.run(["git", "-C", self.d, "init", "-q"], check=True)

    def put(self, name, text):
        p = os.path.join(self.d, name); os.makedirs(os.path.dirname(p), exist_ok=True); write(p, text)
        subprocess.run(["git", "-C", self.d, "add", name], check=True)

    def run_cli(self, *args):
        return subprocess.run([sys.executable, CLI, *args], cwd=self.d, capture_output=True, text=True)

    def test_fails_on_a_leak_and_passes_when_clean(self):
        self.put("tool.py", "import tempfile\ndef f():\n    return tempfile.mkdtemp()\n")
        self.put("test_tool.py", "import tempfile\nx = tempfile.mkdtemp()\n")      # unit tests: skipped by default
        r = self.run_cli()
        self.assertEqual(r.returncode, 1, r.stdout)
        self.assertIn("LEAK   tool.py:3", r.stdout)
        self.assertNotIn("test_tool.py", r.stdout)
        self.put("tool.py", "import tempfile\nwith tempfile.TemporaryDirectory() as t: pass\n")
        r = self.run_cli()
        self.assertEqual((r.returncode, r.stdout.strip().splitlines()[-1]), (0, "check-tool-tmp: OK (1 files)"))

    def test_an_empty_exclude_checks_test_files_too(self):
        self.put("test_tool.py", "import tempfile\nx = tempfile.mkdtemp()\n")
        self.assertEqual(self.run_cli().returncode, 0)
        self.assertEqual(self.run_cli("--exclude", "").returncode, 1)

    def test_exclude_matches_a_path_glob(self):
        self.put("lab/x.sh", "#!/bin/bash\nT=$(mktemp)\n")
        self.assertEqual(self.run_cli().returncode, 1)
        r = self.run_cli("--exclude", "lab/*")
        self.assertEqual((r.returncode, r.stdout.strip()), (0, "check-tool-tmp: OK (0 files)"))

    def test_named_paths_are_checked_instead_of_git(self):
        self.put("a.sh", "#!/bin/bash\nT=$(mktemp)\n")
        p = os.path.join(self.d, "b.sh"); write(p, "#!/bin/bash\necho hi\n")
        self.assertEqual(self.run_cli("b.sh").returncode, 0)

    def test_a_python2_file_is_skipped_and_said(self):
        self.put("old.py", "print 'hi'\n")
        r = self.run_cli()
        self.assertEqual(r.returncode, 0)
        self.assertIn("SKIP   old.py", r.stdout)

    def test_outside_a_git_repo_it_fails_closed(self):
        d = tempfile.mkdtemp(prefix="check-tool-tmp-nogit-")
        self.addCleanup(shutil.rmtree, d, True)
        r = subprocess.run([sys.executable, CLI], cwd=d, capture_output=True, text=True,
                           env=dict(os.environ, GIT_CEILING_DIRECTORIES=os.path.dirname(d)))
        self.assertEqual(r.returncode, 2, r.stdout + r.stderr)
        self.assertIn("check-tool-tmp: CANNOT LIST FILES", r.stdout)

    def test_this_repo_is_clean(self):
        r = subprocess.run([sys.executable, CLI], cwd=ROOT, capture_output=True, text=True)
        self.assertEqual(r.returncode, 0, r.stdout)


if __name__ == "__main__":
    unittest.main(verbosity=2)
