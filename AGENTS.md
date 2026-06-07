# Repository Agent Instructions

These instructions apply to this repository and to any repository scaffolded from
this one unless a more specific `AGENTS.md` overrides them.

## Operating Rules

1. Before modifying any existing file, create a backup copy of the original file.
   Do not edit source files directly without a copy to compare or restore from.
2. Do not create arbitrary non-code artifacts. When the requested deliverable is a
   document, spreadsheet, or presentation, create an Office-format file unless the
   user explicitly asks for another format.
3. Do not agree with user proposals by default. Evaluate assumptions, surface
   conflicts, and recommend the best technical approach based on the codebase and
   evidence.
4. If project context conflicts with the request, stop and ask how to proceed.
   Explain the conflicting evidence clearly.

## TDD Workflow For Python Changes

All code changes in this project must follow strict TDD. The only exception is a
trivial UI text or typo correction.

Tests are required to validate real behavior, important logic, edge cases, and
regressions. Do not add shallow tests that only check whether:

- a key exists
- a function exists
- a class exists
- a string appears in source code
- a text fragment exists in a file
- a line of code was added

Testing source-code text is forbidden unless the task is specifically about code
generation, static analysis, formatting, or file content transformation.

### Red-Green-Refactor

1. RED - Write tests that define the expected behavior.
   - Run the relevant test file.
   - Confirm the test fails for the correct reason.
   - The failure must show that expected behavior is missing or wrong.
2. GREEN - Implement the minimum change needed to pass.
   - Do not add unrelated behavior.
   - Do not hard-code values only to satisfy tests.
   - Do not weaken tests to match a poor implementation.
3. REFACTOR - Improve clarity and structure while keeping tests green.
   - Remove duplication.
   - Improve naming and control flow.
   - Run tests again after refactoring.

## Test Quality

Every test must prove real behavior: given input or state, when the code runs, it
produces the correct result, side effect, error, or state change.

Good tests usually include realistic input data, expected output validation,
edge cases, invalid input cases, error handling, important business rules, state
changes, interactions with dependencies, and regression cases where relevant.

Avoid tests that only validate implementation details, broad truthiness, object
existence, weak string matching, or mock calls when the resulting behavior is
more important.

Use Arrange-Act-Assert structure and clear behavior-focused test names.

## Bug Fix Protocol

When fixing a bug:

1. Write or update a test that reproduces the bug.
2. Run the test and confirm it fails for the correct reason.
3. Modify the source code only after the regression test exists.
4. Run the new test again and confirm it passes.
5. Run the full test suite.

## Regression Policy

After any code change:

1. Run the relevant tests first.
2. Run the full test suite.
3. Fix regressions before considering the task complete.

Never skip, disable, weaken, or delete a failing test just to make the suite
pass.

## Test Commands

Use the Windows Python launcher for tests:

```bash
py -m pytest tests
```

Run a single test file:

```bash
py -m pytest tests/<file>.py -q
```

Run a single test:

```bash
py -m pytest tests/<file>.py::<test_name> -q
```

When the execution environment supports approval escalation, request it before
running `pytest`. If escalation is unavailable, run the command normally and
report any environment limitation.

## Test File Conventions

- Test files live in `tests/`.
- Test file names must follow `test_*.py`.
- Each source module should have a corresponding test file when practical.
- Test names must describe behavior.

## Mocking Rules

Use mocks only for external dependencies such as network calls, filesystem
access, databases, APIs, time-sensitive behavior, and slow or flaky services.

Do not mock the logic being tested.

## Assertions

Assertions must be specific. Prefer exact values, exact errors, and exact state
checks over truthiness or broad string matching.

Weak assertions are allowed only when paired with stronger assertions that prove
behavior.

## Edge Cases

For important functions or flows, consider empty input, missing fields, `None`
values, invalid types, duplicate values, boundary values, large input,
unexpected but valid input, error paths, and successful paths.

Add only the edge cases relevant to the logic being changed.

## Coding Standards

1. Keep code DRY. If logic repeats, refactor it into a function, class, or
   module and reuse it.
2. Follow Python engineering best practices: clear names, small functions,
   simple control flow, explicit error handling, useful type hints, and no
   unnecessary complexity.
3. Do not hard-code keys, strings, text, field names, items, or special cases
   only to satisfy a requirement, test, user request, or bug fix.
4. If hard-coding appears necessary, stop and present these options:
   - fix code or internal prompting in a broader way that may affect many use
     cases
   - add an error message explaining how the user can fix their input or prompt
   - declare the use case out of scope and make no code change
5. Do not over-engineer. Implement what is needed and keep the design flexible
   enough for the current requirement.

## Definition Of Done

A task is complete only when:

1. New or updated tests cover the requested functionality or bug fix.
2. Tests validate meaningful behavior, not source shape.
3. New tests fail before implementation unless that is impossible for the
   requested change.
4. Implementation passes the new tests.
5. The full test suite passes with zero failures.
6. Code has been refactored for clarity where needed.
7. No tests were skipped, weakened, deleted, or changed only to hide a failure.

## Security Invariants

The following rules must never be removed or weakened by any future agent or
developer. Each invariant maps to a specific protection that prevents
irreversible data loss or security vulnerabilities.

1. **Output-overwrite protection.** `require_safe_output()` from `safe_io.py`
   must be called before any write operation that could overwrite an existing
   file. The function raises `FileExistsError` by default; callers may pass
   `overwrite=True` only when the user has explicitly opted in (e.g.
   `--overwrite` CLI flag).

2. **Automatic backup on overwrite.** When `overwrite=True` is passed,
   `backup_if_exists()` must be called immediately after `require_safe_output()`
   and before writing the new content. This guarantees the old file is always
   recoverable.

3. **Source-equals-output guard.** Every function that writes from a source
   file must check `output.resolve() == source.resolve()` and raise
   `ValueError` before calling `require_safe_output()`. Bypassing this check
   would silently corrupt the source.

4. **Subprocess timeouts.** All `subprocess.run()` calls must include
   `timeout=SUBPROCESS_TIMEOUT_SECONDS`. The constant is defined in each
   module that uses subprocesses (`auto_form.py`, `field_validation.py`).

5. **Subprocess timeout handling.** `subprocess.TimeoutExpired` must be caught
   alongside `OSError` and `subprocess.CalledProcessError` in every
   `except` clause that wraps a `subprocess.run()` call. Catching only two of
   the three leaves a hang path open.

6. **URL download limits.** Any code that downloads a file from a URL must
   validate the scheme (only `http` and `https`), cap the response at
   `MAX_DOWNLOAD_BYTES` (50 MB), and verify the PDF magic bytes before
   accepting the content.

7. **XLSX decompressed-size limit.** Before opening a worksheet entry inside
   an XLSX archive, check `ZipInfo.file_size` against
   `MAX_XLSX_DECOMPRESSED_BYTES` (50 MB) and raise `ValueError` if the limit
   is exceeded. This prevents zip-bomb attacks from exhausting memory.

8. **CLI --overwrite flag required.** Any new CLI subcommand that produces an
   output file must include a `--overwrite` argument and pass it to the
   underlying library function. The library function must call
   `require_safe_output()` with the user-supplied value.

9. **TDD first.** No security invariant or protection may be added, removed,
   or changed without a failing test written first (RED before GREEN).
   Tests must prove the protection works, not just that the function exists.
