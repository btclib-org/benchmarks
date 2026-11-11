# Copyright (c) The btclib developers
# Distributed under the MIT software license, see the accompanying
# LICENSE file or https://opensource.org/license/mit for the full text.

"""What the whole suite shares: an import path, and the coverage gate.

`src/btclib_benchmarks/` is a package, installed into this project's own
venv, and a test of it imports it the way any caller does. What is left
under `scripts/` -- the numbered benchmarks, `artifacts.py` and
`render.py` -- is not: a module whose name opens with a digit or carries
a hyphen is not a Python identifier, so no benchmark could become a
package member as it is, and the other two were never meant to be one.
Nothing imports any of them in ordinary use, they are run by path, and a
test that judges them has to reach them the way a person does -- which
is the whole of what the `sys.path` insert below buys.

`coverage_fail_under` is the other half. `--cov` is in addopts, so the
100% ratchet is what a bare `uv run pytest` measures rather than
something only CI reaches, and the price is that every partial run
reports against it too. Which invocations count as asking for less than
the suite is for section 8 of the organization's standard to decide, and
the docstring below is where each of them is read.

The guard beside it is on that ratchet reaching the run at all. coverage
looks for its configuration in the directory the process started in, so
a run started from `tests/` finds no `fail_under`, no `source` and no
`branch = true`. Section 8 leaves a tree to point such a run at its
configuration or to make it say it is ungated, and this file is the
second of the two: such a run is refused (btclib-org/.github#443).

A guard on the file set sits beside it. Pointed at its configuration
with `--cov-config`, a run from `tests/` still reads the directories
`source` names against `tests/`, and `.github/scripts` is not one there,
so such a run is refused as well. So is a run from another checkout,
whose `.github/scripts` coverage would measure in place of this tree's.
"""

from __future__ import annotations

import sys
from pathlib import Path
from typing import TYPE_CHECKING, Protocol

import pytest

if TYPE_CHECKING:
    from argparse import Namespace

SCRIPTS = Path(__file__).parents[1] / "scripts"
sys.path.insert(0, str(SCRIPTS))

# the tree that holds this file, which is where a refused run is sent and
# where a `source` directory is looked for: no pytest or coverage flag
# moves it, where `--rootdir` and `-c` move the rootdir and `--cov-config`
# moves the file coverage reads
TREE = Path(__file__).parents[1]


def asks_for_everything(
    file_or_dir: list[str] | None,
    *,
    invocation_dir: Path,
    testpaths: list[Path],
) -> bool:
    """Return whether the paths named on the command line take the suite in.

    No path at all is `testpaths`, which is the suite. A path at or above
    one of those entries collects it whole, so what decides is
    containment and not equality: read as equality, `pytest tests` is a
    subset -- and it is what somebody types who means the whole suite and
    says so.

    The two bases are different directories, which `pytest .` run from
    `tests/` shows: pytest reads a positional argument against the
    directory it was invoked from, and `testpaths` against the rootdir.
    Both sides are then resolved, because a directory reached through a
    symlink -- `/tmp` on macOS -- is unequal to the same directory
    spelled without it, and either side can be the one that carries the
    link: an absolute positional keeps whatever the command line wrote,
    and `rootpath` keeps whatever `--rootdir` was given.

    A parent-directory segment is a second reason to resolve, and no
    symlink is involved in it: `pathlib` keeps `..` at construction where
    it collapses `.` and a trailing separator, so `pytest tests/../tests`
    from the rootdir spells the `testpaths` entry in a way that compares
    unequal to it. A reader checks that one in a line, for any `base`:
    `base / "tests/../tests" == base / "tests"` is False, and the same
    pair resolved is True.

    `file_or_dir` is `None` rather than `[]` on the `--help` path, the
    positional never having been parsed, and that names no path either --
    folding it in is what keeps `--help` from ending in a traceback whose
    last frame is this file.
    """
    given = [(invocation_dir / path).resolve() for path in file_or_dir or []]
    if not given:
        return True
    if wanted := [path.resolve() for path in testpaths]:
        return all(
            any(target == path or path in target.parents for path in given)
            for target in wanted
        )
    # `all` over nothing is true, which would make every path named here
    # the whole suite. Nothing names the suite, so a bare run collects the
    # rootdir and anything asked for is less than it
    return False


def coverage_fail_under(
    configured: float | None,
    options: Namespace,
    *,
    invocation_dir: Path,
    testpaths: list[Path],
) -> float | None:
    """Return the coverage threshold this run's selection has to meet.

    `fail_under` applies to every report coverage writes, a partial one
    included, so `uv run pytest tests/inputs_test.py` would end in
    `Required test coverage of 100.0% not reached` -- true of that run
    and saying nothing about the tree. Running one file and one test are
    documented commands, and a gate that fails them is a gate read as
    noise.

    So a run that asked for a subset is gated at zero rather than having
    coverage switched off: the report still prints, which is what makes
    it worth reading while iterating on one module. A whole run is handed
    back `configured`, the threshold pytest-cov has already read out of
    the coverage configuration, so pyproject.toml stays the one place the
    number lives.

    The threshold and the selection arrive as two arguments because by
    the time this runs the two namespaces no longer agree. pytest-cov
    fills `cov_fail_under` from the coverage configuration in
    `pytest_load_initial_conftests`, before `pytest_configure`, so "the
    option is set" has stopped meaning "somebody asked for it": what
    still means that is `options`, `config.option` itself, which carries
    only what the command line and addopts put there. An explicit
    `--cov-fail-under` is therefore `options.cov_fail_under`, and is
    handed back untouched whichever kind of run it is -- the caller
    naming the threshold is the one thing this must not overrule.

    A subset is what pytest was *asked* for, and section 8 of the
    organization's standard names the set: `-k`, `-m`, `--deselect`,
    `--ignore`, `--ignore-glob`, `--lf`, and paths that leave part of the
    suite out, which is `asks_for_everything`'s question and not whether
    a path was named at all. A run that leaves tests out measures the
    same source with fewer tests, so what its report is short of is the
    tests that did not run -- a shortfall no reader can tell from one the
    tree has, which is what teaches whoever meets it to reach for
    `--no-cov`. What is read is that a flag was passed and not what it
    came down to: an `--ignore` naming a path this suite does not hold
    narrows nothing and drops the floor anyway, for the reason the next
    paragraph gives of `--lf`.

    `--lf` counts wherever it appears, rather than only where the cache
    holds a failure to rerun. What the invocation asked for is what
    decides, and the cache is a fact about the run before it: reading it
    here would be a second implementation of the cacheprovider's own rule
    for which tests `--lf` comes down to. What that costs is the `--lf`
    finding nothing to rerun, which is the whole suite ungated, and the
    bare run after it measures the tree again.

    An `-x` that stops early is outside the set: what cuts that run short
    is a failure and not what the invocation asked for.
    """
    asked: float | None = options.cov_fail_under
    if asked is not None:
        return asked
    narrowing = (
        options.keyword,
        options.markexpr,
        options.deselect,
        options.ignore,
        options.ignore_glob,
        # `-p no:cacheprovider` leaves `--lf` unregistered rather than
        # false, and a run that cannot pass the flag has not passed it
        getattr(options, "lf", False),
    )
    if any(narrowing):
        return 0
    if not asks_for_everything(
        options.file_or_dir, invocation_dir=invocation_dir, testpaths=testpaths
    ):
        return 0
    return configured


class CoverageConfiguration(Protocol):
    """What this file reads of coverage's own configuration object.

    `config_file` is the file coverage took its settings from, and
    `None` where it took them from none: coverage sets it as it reads
    one, so the attribute is the run's own answer to whether the
    configuration reached it, rather than an inference from a value
    that reached it.

    `source` is the list `[tool.coverage.run]` names, each entry as
    written there, and `None` where nothing names one.
    """

    config_file: str | None
    source: list[str] | None


def coverage_configuration(config: pytest.Config) -> CoverageConfiguration | None:
    """Return the configuration coverage is measuring with, or `None`.

    `None` is the two ways there is nothing to ask about: `--no-cov`,
    where pytest-cov registers its plugin and returns from `__init__`
    with the controller left unbuilt, and a run whose plugin was never
    registered, where `getplugin` hands back `None` -- the same
    `getattr` default answers for both.
    """
    plugin = config.pluginmanager.getplugin("_cov")
    controller = getattr(plugin, "cov_controller", None)
    return None if controller is None else controller.cov.config


def held_to_a_floor(
    asked: float | None, *, asked_for_help: bool, collect_only: bool
) -> bool:
    """Return whether a measuring run is one the guards below ask about.

    What the command line decided arrives here and in the guards below
    as values rather than as `config.option`, which `coverage_fail_under`
    above takes instead: what that function reads is section 8's set of
    flags and paths, and what is read here is what each signature names.
    The two booleans are keyword-only because FBT001 refuses a
    boolean-typed positional parameter, and because the pair is the
    exemption itself.
    """
    # `asked is None`: section 8 of the organization's standard has the
    # hook never overruling an explicit `--cov-fail-under`, and a caller
    # who named the floor has not had one taken away in silence.
    #
    # neither `--help` nor `--collect-only` is held to a floor to begin
    # with: `--help` exits before a session, and pytest-cov never fails a
    # `--collect-only` run on the floor whatever its report prints. The
    # pair is an enumeration rather than every run pytest-cov leaves
    # ungated, and `--markers` and `--fixtures` are refused knowingly.
    # Widening it is the rejected alternative: what would decide the
    # question is whether pytest-cov would have gated this run, which is
    # no property to read here, so a longer list is the same guess under
    # more names
    return asked is None and not asked_for_help and not collect_only


def configuration_went_unread(
    cov_config: CoverageConfiguration | None,
    inipath: Path | None,
    asked: float | None,
    *,
    asked_for_help: bool,
    collect_only: bool,
) -> bool:
    """Return whether a run held to the floor cannot see one.

    A guard and not a sentence in CONTRIBUTING.md. What it catches is a
    plausible spelling switching the floor off, and a reader told to
    start from the root is not the run that does not: the sentence
    leaves the same failure, with somebody having been told about it.

    What it compares is not the threshold. pyproject.toml is the one
    place the number lives, and a `== 100` here would be the second, so
    what decides is whether coverage read a file at all against whether
    pytest read one -- the asymmetry the defect leaves behind, pytest
    walking up from where it was invoked to find its configuration and
    coverage looking only where the process started.
    """
    if cov_config is None or not held_to_a_floor(
        asked, asked_for_help=asked_for_help, collect_only=collect_only
    ):
        return False
    # `inipath` is what the message has to name, so a run pytest read no
    # configuration for is one this cannot tell anybody anything about
    return cov_config.config_file is None and inipath is not None


def sources_out_of_reach(
    cov_config: CoverageConfiguration | None,
    tree: Path,
    asked: float | None,
    *,
    asked_for_help: bool,
    collect_only: bool,
) -> list[str]:
    """Return the `source` entries that send this run to the wrong files.

    coverage takes an entry for a directory where it is one from the
    directory the run started in, and for a module name otherwise. In a
    checkout every entry is a directory under `tree`, and no module is
    imported as `scripts`, `src` or `tests`, so an entry read as a
    module name measures nothing.

    An entry that is a directory here and under `tree` is returned unless
    the two are the same directory: from another checkout, coverage would
    measure that checkout's `.github/scripts`, `scripts`, `src` and
    `tests` in place of this tree's. `samefile` asks it of the
    directories and not of their paths, which differ where a symlink or a
    `..` spells one of them.

    An entry that is a directory under `tree` but none from here is returned
    where it cannot be a module name: from `tests/`, `.github/scripts` is
    that entry. `scripts`, `src` and `tests` are not returned from `tests/`,
    though coverage measures nothing of them there either: the hook refuses
    the run on `.github/scripts` all the same. In an sdist, which ships no
    `.github`, nothing is returned from `tests/`, and coverage, measuring
    nothing of this tree, reports no data.
    """
    if cov_config is None or not held_to_a_floor(
        asked, asked_for_help=asked_for_help, collect_only=collect_only
    ):
        return []
    return [
        entry
        for entry in cov_config.source or []
        if (tree / entry).is_dir()
        and (
            not Path(entry).samefile(tree / entry)
            if Path(entry).is_dir()
            else not all(part.isidentifier() for part in entry.split("."))
        )
    ]


def pytest_configure(config: pytest.Config) -> None:
    """Gate a whole run at `fail_under`, and a partial one at nothing.

    The threshold is written to `known_args_namespace` and not to
    `config.option`: pytest builds the first by parsing the known
    arguments into a *copy* of the second, and pytest-cov holds on to
    that copy. Writing to `config.option` instead runs without error and
    changes nothing -- the plugin never reads it back, and the run still
    fails on the whole tree's coverage.

    A run coverage's configuration never reached is refused rather than
    gated, and so is a run that would leave a `source` directory
    unmeasured, `pytest.UsageError` being what pytest prints without a
    traceback and exits `4` for -- an exit of its own, so the code says
    the run measured nothing rather than that something in the tree
    failed.
    """
    if configuration_went_unread(
        coverage_configuration(config),
        config.inipath,
        config.option.cov_fail_under,
        asked_for_help=config.option.help,
        collect_only=config.option.collectonly,
    ):
        # bound rather than passed inline: EM102 refuses an f-string
        # literal given to an exception
        refusal = (
            "coverage read no configuration, so this run is held to no"
            " floor and measures a different set of files: coverage looks"
            f" only in the directory the run started in, {Path.cwd()}, and"
            f" pytest read {config.inipath}. Run from {TREE}."
        )
        raise pytest.UsageError(refusal)
    if unmeasured := sources_out_of_reach(
        coverage_configuration(config),
        TREE,
        config.option.cov_fail_under,
        asked_for_help=config.option.help,
        collect_only=config.option.collectonly,
    ):
        refusal = (
            f"coverage would not measure {', '.join(unmeasured)}: it reads"
            " a source directory against the directory the run started in,"
            f" {Path.cwd()}, and from there that name is not this tree's"
            f" directory. Run from {TREE}."
        )
        raise pytest.UsageError(refusal)
    testpaths: list[str] = config.getini("testpaths")
    namespace = config.known_args_namespace
    namespace.cov_fail_under = coverage_fail_under(
        namespace.cov_fail_under,
        config.option,
        invocation_dir=config.invocation_params.dir,
        testpaths=[config.rootpath / path for path in testpaths],
    )
