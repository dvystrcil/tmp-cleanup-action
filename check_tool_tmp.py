#!/usr/bin/env python3
"""Static check: every temp dir or file a script makes is cleaned up, or marked as kept on purpose.

Tools that can't run in CI (they need FreeCAD, a cluster, a GPU...) can still be checked by reading them. Leaked
temp dirs from tools and tests once used every inode of a laptop's /tmp tmpfs (vt350#90), and then Bash, test
suites and scratch files all failed with ENOSPC while `df -h` still showed free space.

  check_tool_tmp.py [--exclude GLOB ...] [PATH ...]
      PATH: files to check (default: every git-tracked *.py and *.sh). --exclude: fnmatch globs on the file name or
      its repo path; the default `test_*.py` leaves Python unit tests to a run with an empty TMPDIR
      (check_test_tmp.sh). Prints "check-tool-tmp: OK (N files)" and exits 0, else lists each LEAK and exits 1.

Python: each tempfile.mkdtemp(), mkstemp() or NamedTemporaryFile(delete=False) needs, in the same function (or at
module level, the module), a cleanup: shutil.rmtree, weakref.finalize, os.remove, os.unlink or Path.unlink, called or passed on
(self.addCleanup(shutil.rmtree, d), atexit.register(shutil.rmtree, d)).
TemporaryDirectory() and a deleting NamedTemporaryFile clean up by themselves and always pass, and so does a call with
dir=: it makes its temp inside a dir the caller owns, not in the system temp dir.

Shell: `VAR=$(mktemp ...)` needs an `rm` of $VAR somewhere in the file, or of an array $VAR is added to
(`TMPS+=("$VAR")` ... `rm -f "${TMPS[@]}"`). Best in an EXIT trap, so a failure doesn't leak it, but an rm on the
happy path counts: the check is for temp files that are never removed. A mktemp that isn't assigned to a variable
needs a trap line that runs rm.

Either way, a `tmp-kept: <reason>` comment on the call's line or the line above allows it: use it for an output dir
whose path is handed to the user. The reason is required.
"""
import argparse, ast, fnmatch, re, subprocess, sys, warnings
from typing import NamedTuple

LEAKY = {"mkdtemp", "mkstemp"}
CLEANUP = {"rmtree", "finalize", "remove", "unlink"}
MARKER = re.compile(r"#\s*tmp-kept:\s*\S")
FUNCS = (ast.FunctionDef, ast.AsyncFunctionDef, ast.Lambda)


class Finding(NamedTuple):
    path: str
    line: int
    code: str


def _marked(lines, n):
    """A tmp-kept marker (with a reason) on line n (1-based) or the line above."""
    return any(MARKER.search(lines[i - 1]) for i in (n, n - 1) if 1 <= i <= len(lines))


def _name(call):
    f = call.func
    return f.attr if isinstance(f, ast.Attribute) else f.id if isinstance(f, ast.Name) else None


def _leaky(call):
    n = _name(call)
    if any(k.arg == "dir" for k in call.keywords): return False     # inside a dir the caller owns, not the system temp
    if n in LEAKY: return True
    if n == "NamedTemporaryFile":
        return any(k.arg == "delete" and isinstance(k.value, ast.Constant) and k.value.value is False for k in call.keywords)
    return False


def _scopes(tree):
    """[(scope, [nodes directly in it])]: each function's own nodes (not its nested functions'), and the module's."""
    out = []
    def collect(node, nodes):
        for ch in ast.iter_child_nodes(node):
            if isinstance(ch, FUNCS):
                out.append((ch, collect(ch, [])))
            else:
                nodes.append(ch)
                collect(ch, nodes)
        return nodes
    out.append((tree, collect(tree, [])))
    return out


def _cleanup_ref(node):
    """shutil.rmtree / os.remove / weakref.finalize ..., called or passed on (addCleanup(shutil.rmtree, d))."""
    return (isinstance(node, ast.Attribute) and node.attr in CLEANUP) or (isinstance(node, ast.Name) and node.id in CLEANUP)


def check_python(path, src):
    lines = src.splitlines()
    found = []
    with warnings.catch_warnings():                                  # e.g. SyntaxWarning for a bad escape: not ours
        warnings.simplefilter("ignore")
        tree = ast.parse(src, path)
    for _, nodes in _scopes(tree):
        cleans = any(_cleanup_ref(x) for x in nodes)
        for c in (x for x in nodes if isinstance(x, ast.Call)):
            if _leaky(c) and not cleans and not _marked(lines, c.lineno):
                found.append(Finding(path, c.lineno, lines[c.lineno - 1].strip()))
    return sorted(found, key=lambda f: f.line)


def _code(line):
    """A shell line without its comment (a whole-line comment is empty)."""
    return "" if line.lstrip().startswith("#") else re.split(r"(?<![\w$'\"])#", line, maxsplit=1)[0]


def check_shell(path, src):
    lines = src.splitlines()
    code = [_code(l) for l in lines]
    trap_rm = any(re.search(r"\btrap\b.*\brm\b", c) for c in code)
    body = "\n".join(code)
    found = []
    for n, c in enumerate(code, 1):
        if not re.search(r"\bmktemp\b", c) or _marked(lines, n): continue
        m = re.search(r"(?:^|[\s;(])([A-Za-z_]\w*)=[^=]*\bmktemp\b", c)
        if m:
            v = m.group(1)
            arrays = re.findall(rf"([A-Za-z_]\w*)\+=\(\s*[^)]*\$\{{?{v}\b", body)
            ok = any(re.search(rf"\brm\b[^\n]*\$\{{?{x}\b", body) for x in [v, *arrays])
        else:
            ok = trap_rm
        if not ok: found.append(Finding(path, n, lines[n - 1].strip()))
    return found


def _excluded(path, globs):
    name = path.rsplit("/", 1)[-1]
    return any(fnmatch.fnmatch(name, g) or fnmatch.fnmatch(path, g) for g in globs)


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("paths", nargs="*")
    ap.add_argument("--exclude", action="append", default=None,
                    help="fnmatch glob on the file name or path; repeatable (default: test_*.py)")
    a = ap.parse_args(argv)
    globs = ["test_*.py"] if a.exclude is None else [g for g in a.exclude if g]
    if a.paths:
        files = a.paths
    else:
        # safe.directory: a checkout owned by another user (container jobs, act) is refused otherwise; listing is read-only
        r = subprocess.run(["git", "-c", "safe.directory=*", "ls-files", "*.py", "*.sh"], capture_output=True, text=True)
        if r.returncode:
            print(f"check-tool-tmp: CANNOT LIST FILES (git ls-files exit {r.returncode}): {r.stderr.strip()}")
            return 2
        files = r.stdout.split()
    files = [f for f in files if not _excluded(f, globs)]
    found = []
    for f in files:
        src = open(f, encoding="utf-8", errors="replace").read()
        try:
            found += check_python(f, src) if f.endswith(".py") else check_shell(f, src)
        except SyntaxError as e:
            print(f"SKIP   {f}: not parseable as Python 3 ({e.msg}, line {e.lineno})")
    for x in found:
        print(f"LEAK   {x.path}:{x.line}  {x.code}")
    if found:
        print(f"check-tool-tmp: FAILED ({len(found)} temp dir/file(s) never removed; "
              "clean up, or mark `# tmp-kept: <reason>`)")
        return 1
    print(f"check-tool-tmp: OK ({len(files)} files)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
