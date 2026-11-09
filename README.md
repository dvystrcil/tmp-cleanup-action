# tmp-cleanup-action

Fail a PR whose code leaves temp files behind.

Leaked temp dirs from tools and tests once used every inode of a laptop's `/tmp` (a tmpfs with 1,048,576 inodes). About 39k dirs had built up across three repos, and then Bash, test suites and scratch files all failed with `ENOSPC` while `df -h` still showed free space ([vt350#90](https://github.com/dvystrcil/vt350/issues/90)). This action has two checks that catch the two ways it happened.

| Check | What it does | When it fails |
|---|---|---|
| **Static** (`check_tool_tmp.py`), on by default | Reads every git-tracked `.py` and `.sh`. Covers tools that can't run in CI (FreeCAD, a cluster, a GPU) | A temp dir or file with no cleanup and no `tmp-kept` marker |
| **Runtime** (`check_test_tmp.sh`), when `test-command` is set | Runs your tests with an empty `TMPDIR` | The tests fail, or leave anything in `TMPDIR` |

## Use

```yaml
name: tmp-cleanup
on: [pull_request]
permissions:
  contents: read
jobs:
  tmp-cleanup:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v7
      - uses: dvystrcil/tmp-cleanup-action@v1
        with:
          test-command: python3 -m unittest discover -s tests   # optional
```

| Input | Default | Meaning |
|---|---|---|
| `tool-check` | `true` | Run the static check |
| `exclude` | `test_*.py` | Space-separated globs (file name or repo path) the static check skips. The default leaves Python unit tests to the runtime check. `""` checks everything |
| `test-command` | (none) | Run with an empty `TMPDIR`. It must pass and leave nothing behind |
| `test-ignore` | (none) | Space-separated globs allowed to remain in `TMPDIR`, e.g. `node-compile-cache` (Node's own cache, one dir reused on every run) |
| `working-directory` | `.` | Where both checks run |

## The static check's rules

**Python.** Each `tempfile.mkdtemp()`, `mkstemp()` or `NamedTemporaryFile(delete=False)` needs a cleanup in the **same function**: `shutil.rmtree`, `weakref.finalize`, `os.remove`, `os.unlink` or `Path.unlink`. The cleanup can be called directly or passed on, as in `self.addCleanup(shutil.rmtree, d)` or `atexit.register(shutil.rmtree, d)`. A cleanup in a different function doesn't count, so pass the cleanup on (`weakref.finalize`, `addCleanup`) next to the call.

These always pass:
- `TemporaryDirectory()`
- a deleting `NamedTemporaryFile`
- any call with `dir=`, since it makes the temp inside a dir the caller owns

**Shell.** `VAR=$(mktemp …)` needs an `rm` of `$VAR` somewhere in the file, or of an array `$VAR` is added to (`TMPS+=("$VAR")` … `rm -f "${TMPS[@]}"`). Put it in an `EXIT` trap if you can, so a failure doesn't leak it. An `rm` on the happy path also counts, because the check is for temp files that are *never* removed. A `mktemp` that isn't assigned to a variable needs a `trap` line that runs `rm`.

**Kept on purpose.** A `# tmp-kept: <reason>` comment on the call's line or the line above allows it. Use it for an output dir whose path is handed to the user. The reason is required.

```python
# tmp-kept: the default --out is the run's output; its path is printed for the user
out = a.out or tempfile.mkdtemp(prefix="prove-parallel-")
```

Run it locally from a repo root:

```
python3 path/to/check_tool_tmp.py              # "check-tool-tmp: OK (N files)", exit 0; else LEAK lines, exit 1
bash path/to/check_test_tmp.sh -- npm test     # "check-test-tmp: OK"; leftovers print LEFT lines, exit 1
```

## Limits

- **Static:** it reads code, so it can't follow a path handed between functions or files. Say where the cleanup happens with `weakref.finalize` beside the call, or with a `tmp-kept` marker.
- **Runtime:** it only covers programs that honour `TMPDIR`: Python's `tempfile`, Node's `os.tmpdir()` and `mktemp`. A hard-coded `/tmp` path isn't seen.
- **Listing files:** `git ls-files` runs with `safe.directory=*`, so a checkout owned by another user (a container job, or act) can still be listed. If git fails anyway, the check fails closed with exit 2.

## Releases

Consumers pin `@v1`. Moving `v1` is the release: `gh workflow run release.yaml --repo dvystrcil/tmp-cleanup-action -f bump=patch`.

## License

[MIT](LICENSE)
