# Copyright (c) The btclib developers
# Distributed under the MIT software license, see the accompanying
# LICENSE file or https://opensource.org/license/mit for the full text.

"""Tests for `.github/scripts/check_vendored_vectors.py`.

The script asks upstream through `gh` and edits an issue through `gh`,
and neither belongs in a suite that runs offline. Every test that runs
a function able to reach a `gh` call replaces `subprocess.run` with a
stub that answers from a table and records each call; the others call
functions that reach none. What is tested is the script's own
judgement: which entries it checks, which it names as skipped, what
counts as drift, and which `gh` command each outcome reaches for.

The script is loaded by path, `.github/scripts` being no package.
"""

from __future__ import annotations

import importlib.util
import json
import runpy
import sys
from pathlib import Path
from types import ModuleType, SimpleNamespace

import pytest

_SCRIPT = (
    Path(__file__).parents[1] / ".github" / "scripts" / "check_vendored_vectors.py"
)


def _load() -> ModuleType:
    spec = importlib.util.spec_from_file_location("check_vendored_vectors", _SCRIPT)
    assert spec
    assert spec.loader
    module = importlib.util.module_from_spec(spec)
    # a dataclass looks its own module up in `sys.modules` as it is built
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


check = _load()

# a run of one digit, which detect-secrets does not read as a key
_PINNED = "1" * 40
_TIP = "2" * 40
_TITLE = "Vendored vectors behind upstream"

# one block of each shape the parser tells apart, in the layout of
# vectors/README.md: a `###` heading naming the file, a fenced `text`
# block under it. Two headings stand before the first block, and the
# block is the nearer one's. The block after `upstream/one/b.json`'s
# own has no heading of its own, so it is reported under the last one
# seen
_README = f"""\
# Vendored vectors

### upstream/one/unpinned.json

Not vendored yet, so no block.

### upstream/one/a.json

```text
repo    upstream/one
path    vectors/a.json
commit  {_PINNED} (2026-01-02)
behind  0
```

### upstream/one/b.json

```text
repo    upstream/one
path    vectors/b.json
commit  {_PINNED}
behind  0
```

```text
repo    upstream/one
path    vectors/c.json
commit  {_PINNED}
behind  3 (as of 2026-01-04)
```

### upstream/two/d.json

```text
repo    upstream/two
path    vectors/d.json
blob    5f1d2c3b4a5968778695a4b3c2d1e0f9a8b7c6d5
behind  0
```

### upstream/two/e.json

```text
repo    upstream/two
path    vectors/e.json
commit  {_PINNED}
```
"""


class _Gh:
    """A `subprocess.run` stand-in answering `gh` from a table.

    `commits` maps an upstream path to the commits `gh api` lists for it,
    and `open_issues` is what `gh issue list` finds. Every call is kept
    in `calls`, its argument list without the executable.
    """

    def __init__(
        self, commits: dict[str, list[str]], open_issues: list[int] | None = None
    ) -> None:
        self.commits = commits
        self.open_issues = open_issues or []
        self.calls: list[list[str]] = []

    def __call__(self, args: list[str], **_: object) -> SimpleNamespace:
        self.calls.append(args[1:])
        if args[1] == "api":
            path = next(a for a in args if a.startswith("path="))[len("path=") :]
            listed = [
                {"sha": sha, "commit": {"committer": {"date": "2026-03-04T05:06:07Z"}}}
                for sha in self.commits[path]
            ]
            return SimpleNamespace(stdout=json.dumps(listed))
        if args[1:3] == ["issue", "list"]:
            return SimpleNamespace(
                stdout=json.dumps([{"number": n} for n in self.open_issues])
            )
        return SimpleNamespace(stdout="")


@pytest.fixture
def readme(tmp_path: Path) -> Path:
    """Write `_README` where the script can read it."""
    path = tmp_path / "README.md"
    path.write_text(_README, encoding="utf-8")
    return path


def _stub(monkeypatch: pytest.MonkeyPatch, gh: _Gh) -> _Gh:
    monkeypatch.setattr(check.subprocess, "run", gh)
    return gh


def test_each_block_is_checked_or_named_as_skipped() -> None:
    """A block is an entry, or a skip naming its heading and why."""
    entries, skipped = check._entries_at_tip(_README)

    assert entries == [
        check.Entry("upstream/one/a.json", "upstream/one", "vectors/a.json", _PINNED),
        check.Entry("upstream/one/b.json", "upstream/one", "vectors/b.json", _PINNED),
    ]
    # a block with no `behind` line is not read as one at the tip
    assert skipped == [
        "upstream/one/b.json (already documented as behind)",
        "upstream/two/d.json (no commit to check against)",
        "upstream/two/e.json (already documented as behind)",
    ]


def test_a_pin_at_the_tip_is_no_drift_and_one_behind_names_the_tip(
    monkeypatch: pytest.MonkeyPatch, readme: Path
) -> None:
    """Only the entry whose newest commit is not the pin drifts."""
    gh = _stub(
        monkeypatch, _Gh({"vectors/a.json": [_PINNED], "vectors/b.json": [_TIP]})
    )

    drifted, _ = check.find_drift(readme)

    assert [(d.entry.path, d.latest_commit, d.latest_date) for d in drifted] == [
        ("vectors/b.json", _TIP, "2026-03-04")
    ]
    assert not drifted[0].path_is_gone
    # one commit asked for, by path, from the pinned repository
    assert gh.calls[0] == [
        "api",
        "--method",
        "GET",
        "repos/upstream/one/commits",
        "-f",
        "path=vectors/a.json",
        "-f",
        "per_page=1",
    ]


def test_a_path_upstream_no_longer_has_is_drift_with_no_tip(
    monkeypatch: pytest.MonkeyPatch, readme: Path
) -> None:
    """No commit touching the path is drift, not an error."""
    _stub(monkeypatch, _Gh({"vectors/a.json": [], "vectors/b.json": [_PINNED]}))

    drifted, _ = check.find_drift(readme)

    assert len(drifted) == 1
    assert drifted[0].path_is_gone


def test_the_issue_body_tells_the_two_kinds_of_drift_apart(readme: Path) -> None:
    """A gone path and a moved tip read differently, skips listed after."""
    entry = check.Entry("h", "upstream/one", "vectors/a.json", _PINNED)
    body = check._issue_body(
        readme,
        [check.Drift(entry, "", ""), check.Drift(entry, _TIP, "2026-03-04")],
        ["other (no commit to check against)"],
    )

    assert body.splitlines()[3:] == [
        (
            f"- **h**: pinned to `{_PINNED}`, and `upstream/one` has no commit"
            " touching `vectors/a.json` any more -- a path its default"
            " branch never held"
        ),
        (
            f"- **h**: pinned to `{_PINNED}`, upstream's tip of `vectors/a.json`"
            f" is now `{_TIP}` (2026-03-04), `upstream/one` -- which may have"
            " deleted or renamed the file rather than changed it"
        ),
        "",
        "Not checked by this run, for the reason named:",
        "- other (no commit to check against)",
    ]


def test_a_body_with_nothing_skipped_has_no_skipped_list(readme: Path) -> None:
    """The heading of the skipped list comes only with a skip under it."""
    entry = check.Entry("h", "upstream/one", "vectors/a.json", _PINNED)
    body = check._issue_body(readme, [check.Drift(entry, _TIP, "2026-03-04")], [])

    assert "Not checked" not in body


@pytest.mark.parametrize(
    "drifted, open_issues, command",
    [
        (False, [], None),
        (False, [7], ["issue", "close", "7"]),
        (True, [], ["issue", "create", "--title", _TITLE]),
        (True, [7], ["issue", "edit", "7"]),
    ],
)
def test_the_report_is_one_gh_command_per_outcome(
    monkeypatch: pytest.MonkeyPatch,
    readme: Path,
    *,
    drifted: bool,
    open_issues: list[int],
    command: list[str] | None,
) -> None:
    """Close, create, edit, or nothing, after looking the title up."""
    gh = _stub(monkeypatch, _Gh({}, open_issues))
    entry = check.Entry("h", "upstream/one", "vectors/a.json", _PINNED)
    drifts = [check.Drift(entry, _TIP, "2026-03-04")] if drifted else []

    check.report(readme, _TITLE, drifts, [])

    assert gh.calls[0] == [
        "issue",
        "list",
        "--state",
        "open",
        "--search",
        f'"{_TITLE}" in:title',
        "--json",
        "number",
    ]
    if command is None:
        assert len(gh.calls) == 1
    else:
        assert len(gh.calls) == 2
        assert gh.calls[1][: len(command)] == command


@pytest.mark.parametrize("argv", [[], ["README.md"], ["a", "b", "c"]])
def test_a_run_without_its_two_arguments_says_how_to_call_it(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    argv: list[str],
) -> None:
    """Exit 2 with the usage line, and no `gh` call."""
    gh = _stub(monkeypatch, _Gh({}))
    monkeypatch.setattr(sys, "argv", ["check_vendored_vectors.py", *argv])

    assert check.main() == 2
    assert capsys.readouterr().err.startswith("usage: check_vendored_vectors.py")
    assert not gh.calls


@pytest.mark.parametrize("dry_run", [True, False])
def test_main_prints_each_finding_and_reports_unless_dry(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    readme: Path,
    *,
    dry_run: bool,
) -> None:
    """GONE, BEHIND and SKIPPED lines, then the issue unless `--dry-run`."""
    gh = _stub(monkeypatch, _Gh({"vectors/a.json": [], "vectors/b.json": [_TIP]}))
    flag = ["--dry-run"] if dry_run else []
    monkeypatch.setattr(sys, "argv", ["x", str(readme), _TITLE, *flag])

    assert check.main() == 0

    out = capsys.readouterr().out.splitlines()
    assert out == [
        (
            f"GONE: upstream/one/a.json pinned to {_PINNED}, and upstream/one"
            " has no commit touching vectors/a.json any more"
        ),
        (
            f"BEHIND: upstream/one/b.json pinned to {_PINNED}, tip is {_TIP}"
            " (2026-03-04), which may have deleted or renamed it"
        ),
        "SKIPPED: upstream/one/b.json (already documented as behind)",
        "SKIPPED: upstream/two/d.json (no commit to check against)",
        "SKIPPED: upstream/two/e.json (already documented as behind)",
    ]
    issue_calls = [c for c in gh.calls if c[0] == "issue"]
    assert bool(issue_calls) is not dry_run


def test_a_clean_run_says_so(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    readme: Path,
) -> None:
    """Every pin at its tip is said in a line of its own."""
    _stub(monkeypatch, _Gh({"vectors/a.json": [_PINNED], "vectors/b.json": [_PINNED]}))
    monkeypatch.setattr(sys, "argv", ["x", str(readme), _TITLE, "--dry-run"])

    assert check.main() == 0
    assert capsys.readouterr().out.splitlines()[-1] == (
        "Every checked pin is still at upstream's tip."
    )


def test_run_as_a_script_it_exits_with_mains_code(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The `__main__` guard hands `main()`'s return to `sys.exit`."""
    gh = _stub(monkeypatch, _Gh({}))
    monkeypatch.setattr(sys, "argv", [str(_SCRIPT)])

    with pytest.raises(SystemExit) as exited:
        runpy.run_path(str(_SCRIPT), run_name="__main__")

    assert exited.value.code == 2
    assert not gh.calls
