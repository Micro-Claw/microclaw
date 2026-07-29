# Repository agent notes

## Running tests

Do not invoke the base-environment `pytest` or `python -m pytest` directly in
this repository. On this machine, `/Users/zachcm/miniforge3/bin/pytest` can exit
immediately with signal 11 (exit code 139) and no pytest output because it loads
the wrong native environment.

Run tests through the project's conda environment instead:

```sh
conda run -n microclaw pytest -q
```

Use the same prefix for targeted tests. If web-server tests fail with
`PermissionError: [Errno 1] Operation not permitted` while binding an ephemeral
localhost port, rerun that test command with sandbox escalation; this is a
sandbox socket restriction, not a test failure.
