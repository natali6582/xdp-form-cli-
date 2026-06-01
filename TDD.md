# TDD Workflow for Python Projects in Codex / VS Code

All code changes in this project MUST follow a strict TDD workflow.
One exception: trivial UI text corrections or typo corrections do not require TDD.

The goal is not only to increase test coverage, but to write tests that validate real behavior, important logic, edge cases, and regressions.

Do not write shallow tests that only check whether:
- a key exists
- a function exists
- a class exists
- a string appears in source code
- a text fragment exists in a file
- a line of code was added

Tests must validate what the code actually does.

---

## Core Testing Principle

Every test must prove a real behavior.

A good test answers:

> Given this input or state, when this code runs, does it produce the correct result, side effect, error, or state change?

A bad test only answers:

> Does the code contain something that looks like the expected implementation?

Do not test implementation details unless there is a clear reason.

---

## Red-Green-Refactor Cycle

Every new feature, enhancement, or change follows this exact sequence:

1. **RED** — Write tests that define the expected behavior.
   - The tests MUST fail before implementation.
   - Run the relevant test file.
   - Confirm the failure is meaningful.
   - The failure should show that the expected behavior is missing or wrong.

2. **GREEN** — Write the minimum implementation needed to pass the failing tests.
   - Do not add unrelated behavior.
   - Do not hard-code values only to satisfy the tests.
   - Do not change the test to match a weak implementation.

3. **REFACTOR** — Improve the implementation while keeping all tests green.
   - Remove duplication.
   - Improve naming and structure.
   - Keep behavior unchanged.
   - Run tests again after refactoring.

The refactor step is important since at first you are not sure how the implementation would look like or work. Only after the code is implemented, you can see new things needed validation and testing against.
You are not only testing to see that it works. You are also testing to prevent future code from breaking this code.

---

## Test Quality Requirements

Tests must validate meaningful behavior.

Good tests should usually include:
- realistic input data
- expected output validation
- edge cases
- invalid input cases
- error handling
- important business rules
- state changes, when relevant
- interactions with dependencies, when relevant
- regression cases for previously broken behavior

Avoid tests that only validate:
- dictionary keys exist without checking values and behavior
- objects are not `None`
- strings appear in the source code
- a method was called when the result is more important
- exact internal structure when public behavior is enough
- mocks instead of testing real logic

Testing source-code text is forbidden unless the task is specifically about code generation, static analysis, formatting, or file content transformation.

---

## What Meaningful Tests Look Like

Prefer tests like this:

```python
def test_calculates_total_price_with_discount():
    order = Order(items=[Item(price=100), Item(price=50)], discount_percent=10)

    total = calculate_total(order)

    assert total == 135
```

Avoid tests like this:

```python
def test_discount_key_exists():
    result = calculate_total(order)

    assert "discount" in result
```

Unless the presence of `"discount"` is itself the real required behavior, this is too shallow.

---

## Test Critical Logic First

When adding or changing code, identify the critical logic before writing tests.

Ask:

1. What is the main behavior the user expects?
2. What inputs can change the result?
3. What edge cases can break this logic?
4. What should happen with invalid or missing data?
5. What regression could happen later if this code is changed?

Write tests for those behaviors.

Do not stop after testing the easiest path.

---

## Bug Fix Protocol

When fixing a bug:

1. First, write or update a test that reproduces the bug.
2. Run the test and confirm it fails for the correct reason.
3. Only then modify the source code.
4. Run the new test again and confirm it passes.
5. Run the full test suite.

The bug fix is complete only when the regression test proves the bug cannot return unnoticed.

---

## Regression Policy

After ANY code change, including a feature, refactor, or bug fix:

* Run the relevant tests first.
* Then run the full test suite.
* A task is NOT complete until every test passes.
* Never skip, disable, weaken, or delete a failing test just to make the suite pass.
* If a pre-existing test fails after your change, fix the regression before moving on.

---

## Test Commands

In Codex, request escalated permissions before running `pytest`.

The sandbox cannot reliably access the host Python installation referenced by `.venv`, so `python` and `.venv\Scripts\python.exe` are not reliable there.

Run all tests:

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

---

## Test File Conventions

* Test files live in: `tests/`
* Test file names must follow: `test_*.py`
* Each source module should have a corresponding test file when practical.
* Use clear test names that describe behavior.

Good:

```python
def test_returns_empty_list_when_no_documents_match_query():
```

Bad:

```python
def test_result():
```

---

## Test Structure

Use the Arrange-Act-Assert pattern.

```python
def test_filters_documents_by_status():
    # Arrange
    documents = [
        {"id": 1, "status": "active"},
        {"id": 2, "status": "archived"},
    ]

    # Act
    result = filter_documents(documents, status="active")

    # Assert
    assert result == [{"id": 1, "status": "active"}]
```

Keep each test focused on one behavior.

---

## Mocking Rules

Use mocks only for external dependencies, such as:

* network calls
* file system access
* databases
* APIs
* time-sensitive behavior
* slow or flaky services

Do not mock the logic you are supposed to test.

If a test mocks too much, it may only prove that the mock works, not that the code works.

---

## Assertions

Assertions must be specific.

Prefer:

```python
assert result.total == 135
assert result.status == "approved"
assert result.errors == []
```

Avoid:

```python
assert result
assert result is not None
assert "approved" in str(result)
```

Weak assertions are allowed only when they are paired with stronger assertions that validate behavior.

---

## Edge Cases

For every important function or flow, consider tests for:

* empty input
* missing fields
* `None` values
* invalid types
* duplicate values
* boundary values
* large input
* unexpected but valid input
* error paths
* successful path

Do not add all of these automatically. Add the ones that are relevant to the logic being changed.

---

## Definition of Done

A task is complete ONLY when:

1. New or updated tests cover the requested functionality or bug fix.
2. The tests validate meaningful behavior, not just structure or source text.
3. The new tests fail before implementation, unless updating existing behavior where this is not possible.
4. The implementation passes the new tests.
5. The full test suite passes with zero failures.
6. The code has been refactored for clarity where needed.
7. No tests were skipped, weakened, deleted, or changed only to hide a failure.

---

## Coding Standards

1. Code should be DRY: avoid repeating code or logic.

   * If logic is repeated, refactor it into a separate function, class, or module.
   * Reuse that logic from all needed places.

2. Follow Python engineering best practices.

   * Clear names.
   * Small functions.
   * Simple control flow.
   * Explicit error handling.
   * Type hints where useful.
   * No unnecessary complexity.

3. Never hard-code keys, strings, text, items, field names, or special cases only to satisfy a requirement, user request, test, or bug fix.

   * The code must be generic enough to handle the relevant input type.
   * If hard-coding appears necessary, stop and ask the user.
   * Explain why hard-coding seems necessary and what the tradeoff is.
   * Only hard-code after the user approves.

Do not fall into the trap of hard-coding values, keys or logic specific to the task at hand. Suggest one of 3 options: 1. fix a code or internal prompting which may affect many usecases. 2. add an error message explaining to the user how to fix their input or prompt. 3. declaring the usecase out of scope and do nothing else.

4. If the existing code or project context conflicts with the user request, stop and ask how to proceed.

   * Surface what looks strange or inconsistent.
   * Explain the conflicting evidence.
   * Do not continue blindly if the request may be based on a wrong assumption, wrong workspace, outdated context, or misunderstood code.

5. Do not over-engineer.

   * Implement what is needed.
   * Keep the design flexible enough for the current requirement.
   * Avoid speculative abstractions.

---

## Final Reminder

Tests are not a formality.

A test suite should give confidence that the project works correctly.

For every test, ask:

> Would this test fail if the important logic was broken?

If the answer is no, rewrite the test.
