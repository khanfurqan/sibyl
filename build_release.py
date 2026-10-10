"""Build the public release folder, then refuse to bless it if anything private
got in.

Why this exists. Sibyl is developed in a private working folder, and the public
repository is never that folder: it starts with a fresh history, built from
this script's output alone. Publishing cannot be undone. Until 2026-09-25 the
list of what must NOT go lived only as a sentence in a private plan plus a copy
somebody did by hand. Review measured what that was risking: nearly every
commit in the development history touches a private path, and its early ones
carry the author's personal email address.

A gate written in prose cannot run, so it can neither pass nor fail, and
afterwards "I checked" is indistinguishable from "I never checked". This file
makes the check an artifact that either exits 0 or exits 1.

Two deliberate design choices, each the opposite of the obvious one:

  * **The audit reads the OUTPUT FOLDER, not the allowlist.** Checking the
    allowlist would only ever confirm its own intentions. Reading the folder
    catches a file that arrived by any route at all: a stale copy, a wildcard, a
    hand edit made after the build.

  * **Everything unknown is a problem.** A file that cannot be read is not a
    clean file, it is an unscanned one. An empty allowlist is not a clean build
    of nothing. This workspace has already paid once for a gate that reported
    PASS when its wordlist was empty (2026-09-06), so every branch here fails
    toward refusing.

Usage:
    python build_release.py <destination folder>
    python build_release.py --check <existing folder>   # audit only, no copy

Exits 0 only when the folder is built and clean. `test_release.py` proves each
refusal.
"""
import pathlib
import re
import shutil
import sys

# Product files only. Explicit, because a wildcard is how a private file gets
# published: it does what the filesystem says rather than what was intended.
ALLOWLIST = [
    "sibyl.py",
    "sibyl.ico",
    "Start-Sibyl.ps1",
    "Start-Sibyl.cmd",
    "requirements.txt",
    "requirements-lock.txt",
    ".env.example",
    ".gitignore",
    "README.md",
    "INSTALL.md",
    "USER_GUIDE.md",
    "CONTRIBUTING.md",
    "SECURITY.md",
    "LICENSE",
    "build_release.py",
    "eval_cleanup.py",
    "eval_stt.py",
    "test_taplistener.py",
    "test_talk.py",
    "test_pill.py",
    "test_recall.py",
    "test_fillers.py",
    "test_guard.py",
    "test_launch.py",
    "test_eval_stt.py",
    "test_release.py",
    # The walkthrough, and the architecture picture both it and the README show.
    "docs/HOW-IT-WORKS.md",
    "docs/architecture.png",
    # The brand assets the README displays. Named one by one, like everything
    # else. They were a `shutil.copytree` of the whole `brand/` folder until the
    # security review of 2026-09-25 pointed out that this file's own opening
    # sentence forbids exactly that: a wildcard does what the filesystem says
    # rather than what was intended, so a draft note or a screenshot dropped in
    # that folder later would have shipped with no allowlist gate at all.
    # Only the logo ships; the brand toolkit stays private (decided 2026-10-03).
    "brand/sibyl_logo_monochrome_dark.png",
]

# The private documents, by name. Never in the output, at any depth.
#
# Matched WITHOUT regard to case, and that is not tidiness. Measured 2026-09-25,
# while this file was being written: with an exact comparison, `state.md`,
# `Claude.md`, `DECISIONS.MD` and `Roadmap.md` all passed the audit with zero
# problems reported. Windows filesystems are case-insensitive, so those are the
# same files to the operating system and different strings to Python, and their
# text held no email and no name, so no content check caught them either.
#
# The rule, applied throughout this file: a PROHIBITION is safer wide, so it
# ignores case. An EXEMPTION is safer narrow, so it stays exact. That is why
# CONTENT_SCAN_EXEMPT and NAME_ALLOWED_IN below compare exactly: a file named
# `license` is flagged for holding the author's name, which costs a rename. The
# opposite mistake costs a publication.
FORBIDDEN_NAMES = [
    "CLAUDE.md",
    "STATE.md",
    "DECISIONS.md",
    "HANDOVER.md",
    "ROADMAP.md",
]

# The private directories. Named separately because a check that only looked at
# top-level files would miss every file inside one of these.
FORBIDDEN_DIRS = ["archive", "review"]

# The private word list, explained with PRIVATE_WORDS below. It is refused by
# name too, but kept out of FORBIDDEN_NAMES itself, because that list also flags
# any file that MENTIONS a name, and .gitignore, which is published, has to
# mention this one.
PRIVATE_WORDS_FILE = "release-private-words.txt"
_FORBIDDEN_NAMES_LOWER = ({name.lower() for name in FORBIDDEN_NAMES}
                          | {PRIVATE_WORDS_FILE.lower()})
_FORBIDDEN_DIRS_LOWER = {name.lower() for name in FORBIDDEN_DIRS}

# Text that must not appear inside any file. The author's surname is the one
# exception, and only in LICENSE, where the copyright line is meant to carry it.
EMAIL = re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}")

# Three forms, not two. The forward-slash Windows form was missing until the
# security review of 2026-09-25: `C:/Users/someone/...` is what editors, shells
# and this project's own tooling produce constantly, and it matched neither of
# the original alternatives, so it would have passed the scan in silence.
HOME_PATH = re.compile(
    r"[Cc]:\\[Uu]sers\\[A-Za-z0-9_.-]+"      # C:\Users\someone
    r"|[Cc]:/[Uu]sers/[A-Za-z0-9_.-]+"       # C:/Users/someone
    r"|/[Uu]sers/[a-z0-9_.-]+"               # /Users/someone, on macOS
)
NAME_ALLOWED_IN = ["LICENSE"]

# The words that would identify the author: the name, which belongs only in
# LICENSE, and others such as a username. They are NOT written in this file.
# Until 2026-09-26 they were, because a scanner must spell out what it searches
# for, and this file is published, so publishing the gate published the very
# words it guarded (review round 3). They live in PRIVATE_WORDS_FILE, beside this
# script, which .gitignore keeps out of git and the audit refuses to publish.
# One entry a line, "name: <the author's name>" once and "word: <other>" for
# each of the rest; a line starting with "#" is a comment, and a "#" anywhere
# else refuses. Loaded by load_private_words. Until it
# has run, the audit refuses: a gate whose wordlist is empty is how this
# workspace once passed a check on nothing (2026-09-06).
NAME_WORD = None
PRIVATE_WORDS = []
# The project's old names are private words too since 2026-10-10, so this file
# cannot name them to make sure each is searched for. A list holding fewer
# different words than this refuses, so deleting one stops the build. A word
# added to the list means raising this.
MIN_PRIVATE_WORDS = 5

# Scanned as bytes, because a text search does not reliably reach binary content
# and the logo and icon are published. The private words are added to these at
# scan time. Raised by the security review of 2026-09-25, which pointed out that
# the name was missing from the byte list while being checked in the text
# branch: an image re-exported with an EXIF author tag and nothing else would
# have passed. Both shipped binaries hold no readable strings at all today, so
# this guards a future one.
BINARY_FORBIDDEN = [b"@gmail", b"Desktop\\Claude"]

# A scanner cannot scan itself. These two files must spell out the generic search
# terms above (the private filenames, the shapes of an email and
# a home path) in order to look for them, so those checks would report the search
# terms as if they were leaks. They are still checked for their NAME and their
# LOCATION like every other file, and since 2026-09-26 for the private words,
# which they no longer need to contain; only the generic checks skip them.
#
# **Matched on the full path, not the filename.** It was the filename until the
# review of 2026-09-25, which both reviewers caught independently: a file named
# `build_release.py` sitting anywhere at all — `brand/build_release.py`, dropped
# in by a hand edit after the build — skipped every content check purely because
# of what it was called. An exemption for two known files must name where they
# live, or it is an exemption for a name.
#
# The residual risk, stated rather than hidden: an email address or a home path
# pasted into one of these two files would not be caught here. That is why they
# hold no prose about the author and no real example paths, and why this list
# has exactly two entries and must not grow. If a third file ever needs
# exempting, the right answer is almost certainly to move its content instead,
# as the private words were.
CONTENT_SCAN_EXEMPT = ["build_release.py", "test_release.py"]

# Written into a release folder so a later rebuild knows the folder is one of
# ours and may be cleared. Without it, `build` refuses to delete a folder that
# already holds files. See `_check_destination`.
MARKER = ".sibyl-release"


def load_private_words(folder: pathlib.Path) -> list:
    """Read PRIVATE_WORDS_FILE from `folder` into NAME_WORD and PRIVATE_WORDS.
    Returns every problem with it; empty means loaded. Anything short of one
    name and MIN_PRIVATE_WORDS different other words refuses, and so does a line it cannot
    read, because a skipped line is a word silently never searched for."""
    global NAME_WORD, PRIVATE_WORDS
    path = pathlib.Path(folder) / PRIVATE_WORDS_FILE
    try:
        text = path.read_text(encoding="utf-8")
    except OSError as exc:
        return [f"{PRIVATE_WORDS_FILE}: could not be read ({type(exc).__name__}), so "
                f"the author's name and other private words cannot be searched for. "
                f"It lives beside build_release.py and is never published"]
    names, words = [], []
    for number, line in enumerate(text.splitlines(), start=1):
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        kind, colon, value = line.partition(":")
        kind, value = kind.strip().lower(), value.strip().lower()
        if "#" in value:
            # A note after an entry would be stored as part of the word, which
            # is then searched for and found nowhere, so the real word is never
            # checked and the release is called safe (review, 2026-09-26).
            return [f"{PRIVATE_WORDS_FILE}: line {number} has a '#' after its entry; "
                    f"put notes on a line of their own"]
        if colon and value and kind == "name":
            names.append(value)
        elif colon and value and kind == "word":
            words.append(value)
        else:
            return [f"{PRIVATE_WORDS_FILE}: line {number} is neither 'name: ...' nor "
                    f"'word: ...', so what it holds would never be searched for"]
    if len(names) != 1:
        return [f"{PRIVATE_WORDS_FILE}: must name the author exactly once "
                f"('name: ...'); found {len(names)}"]
    # This also refuses a list with no words at all, which had a rule of its own
    # until the count arrived (2026-10-10) and left that rule nothing to catch.
    if len(set(words)) < MIN_PRIVATE_WORDS:
        return [f"{PRIVATE_WORDS_FILE}: holds {len(set(words))} different private "
                f"words; at least {MIN_PRIVATE_WORDS} are required"]
    NAME_WORD, PRIVATE_WORDS = names[0], words
    return []


def audit(folder: pathlib.Path) -> list:
    """Every problem found in `folder`, as a list of sentences. Empty means
    clean. Reads the folder itself, so a file that arrived by any route is
    caught."""
    problems = []
    folder = pathlib.Path(folder)

    if not NAME_WORD or not PRIVATE_WORDS:
        return [f"the private word list ({PRIVATE_WORDS_FILE}) is not loaded, so the "
                f"author's name and other private words were never searched for"]

    if not folder.is_dir():
        return [f"{folder}: is not a folder, so nothing could be audited"]

    entries = sorted(folder.rglob("*"))
    if not entries:
        return [f"{folder}: is empty, which is never a valid release"]

    for path in entries:
        rel = path.relative_to(folder)

        if path.name.lower() in _FORBIDDEN_NAMES_LOWER:
            problems.append(f"{rel}: {path.name} is a private document and must not be published")
            continue

        if path.is_dir():
            if path.name.lower() in _FORBIDDEN_DIRS_LOWER:
                problems.append(f"{rel}: {path.name}/ is a private directory and must not be published")
            elif path.suffix:
                # A directory named like a file can never be scanned, so it
                # would pass every content check by accident.
                problems.append(f"{rel}: is a directory but named like a file, so it was never scanned")
            continue

        if any(part.lower() in _FORBIDDEN_DIRS_LOWER for part in rel.parts[:-1]):
            problems.append(f"{rel}: sits inside a private directory")
            continue

        problems.extend(_scan_file(path, rel))

    return problems


def _scan_file(path: pathlib.Path, rel: pathlib.Path) -> list:
    """What is wrong inside one file. An unreadable file is reported, never
    skipped: an unscanned file is not a clean file."""
    found = []
    try:
        raw = path.read_bytes()
    except OSError as exc:
        return [f"{rel}: could not be read ({type(exc).__name__}), so it was never scanned"]

    private = [NAME_WORD] + PRIVATE_WORDS
    lowered_bytes = raw.lower()

    # Read first, exempt second, so an exempt file that cannot be read is still
    # reported above rather than waved through. `test_release.py` proves that
    # ordering directly, by calling _scan_file on a file it has made unreadable.
    # An exempt file skips only the generic checks, whose search terms it must
    # spell out; the private words it no longer needs, so it is searched for them.
    if rel.as_posix() in CONTENT_SCAN_EXEMPT:
        if NAME_WORD.encode() in lowered_bytes:
            found.append(f"{rel}: contains the author's name")
        for word in PRIVATE_WORDS:
            if word.encode() in lowered_bytes:
                found.append(f"{rel}: contains {word!r}")
        return found

    try:
        text = raw.decode("utf-8")
    except UnicodeDecodeError:
        # A real binary: the icon and the logo. Checked as bytes instead.
        for needle in BINARY_FORBIDDEN + [word.encode() for word in private]:
            if needle.lower() in lowered_bytes:
                found.append(f"{rel}: binary file contains {needle.decode()!r}")
        return found

    match = EMAIL.search(text)
    if match:
        found.append(f"{rel}: contains an email address ({match.group()!r})")

    match = HOME_PATH.search(text)
    if match:
        found.append(f"{rel}: contains a home-directory path ({match.group()!r})")

    lowered = text.lower()
    for word in PRIVATE_WORDS:
        if word in lowered:
            found.append(f"{rel}: contains {word!r}")

    if NAME_WORD in lowered and rel.name not in NAME_ALLOWED_IN:
        found.append(f"{rel}: contains the author's name, which belongs only in "
                     f"{' or '.join(NAME_ALLOWED_IN)}")

    for name in FORBIDDEN_NAMES:
        if name.lower() in lowered:
            found.append(f"{rel}: points at {name}, which is not published, so the link would be dead")

    return found


def _check_destination(source: pathlib.Path, dest: pathlib.Path):
    """`(resolved_dest, None)` when it is safe to build there, else `(None, why)`.

    The resolved path is returned, not just a verdict, so that `build` deletes
    **the same object this function judged**. It used to judge `dest.resolve()`
    and then hand the original to `shutil.rmtree`, which are different things for
    a symbolic link or a Windows junction. Raised by review, 2026-09-25.

    A first attempt at that fix wrapped this function in a second one that
    resolved again. A mutation test then showed the second resolve could be
    deleted with every check still passing: it was unreachable, because a resolve
    that fails here already returns a refusal. One function, one resolve.

    Called before anything is deleted, because a guard that refuses afterwards is
    not a guard. `build` used to call `shutil.rmtree` straight on the path it was
    given. Measured on a disposable copy, 2026-09-25: `python build_release.py .`
    run inside a project folder deleted that folder's contents **including
    `.git`**, then died with a confusing WinError 32 about the folder being in
    use. The real invocation is one command typed by hand immediately before an
    irreversible push, which is the worst moment to mistype a path.
    """
    try:
        source = source.resolve()
        dest = dest.resolve()
    except OSError as exc:
        return None, (f"the destination path could not be worked out "
                      f"({type(exc).__name__}), so it is not safe to write to")

    if dest == source or dest in source.parents:
        return None, (f"{dest} is the project itself, or a folder containing it. "
                      f"Building there would delete the source.")
    if dest == pathlib.Path.cwd().resolve():
        return None, f"{dest} is the folder you are standing in"
    if dest == pathlib.Path.home().resolve():
        return None, f"{dest} is your home directory"
    if dest.parent == dest:
        return None, f"{dest} is a drive root"

    if dest.exists():
        if not dest.is_dir():
            return None, f"{dest} already exists and is not a folder"
        if (dest / ".git").exists():
            # The realistic disaster, and the marker cannot save you from it.
            # Step 9 turns a release folder into the public repository, so that
            # folder ends up holding a .git with the only copy of the published
            # history — and it keeps the marker from the build that made it, which
            # grants every later build permission to delete the lot. Raised by
            # review, 2026-09-25. A repository is never build output.
            return None, (f"{dest} holds a .git, so it is a repository rather "
                          f"than a build folder. Deleting it would destroy its "
                          f"history. Build somewhere else and copy the files in.")
        if any(dest.iterdir()) and not (dest / MARKER).exists():
            # Rebuilding over a previous release is normal and stays allowed, so
            # that nobody is pushed into deleting folders by hand instead, which
            # is the same danger with no guard at all.
            return None, (f"{dest} already holds files and has no {MARKER}, so it "
                          f"is not a previous release. Pass a new or empty folder.")
    return dest, None


def build(source: pathlib.Path, dest: pathlib.Path) -> list:
    """Copy the allowlist from `source` into `dest`, then audit `dest`. Returns
    every problem; empty means the folder is ready to publish."""
    source = pathlib.Path(source)
    dest = pathlib.Path(dest)
    problems = []

    if not ALLOWLIST:
        return ["the allowlist is empty, so there is nothing to publish and "
                "nothing to check: refusing rather than reporting a clean build"]

    # Before anything is deleted. This used to be a bare shutil.rmtree on
    # whatever path arrived on the command line.
    dest, reason = _check_destination(source, dest)
    if reason:
        return [f"refusing to build: {reason}"]

    if dest.exists():
        # Say what is being removed. The guard above has already established that
        # this is an empty folder or a previous release, so deleting it is
        # correct — but a marker written by a previous build grants permission to
        # every build after it, and anything dropped into the folder meanwhile
        # goes too. Measured 2026-09-25: it went silently. A disposable output
        # folder should fail loud, not quiet.
        doomed = sum(1 for _ in dest.rglob("*"))
        if doomed:
            print(f"  clearing a previous release at {dest} ({doomed} entries)")
        shutil.rmtree(dest)
    dest.mkdir(parents=True)
    (dest / MARKER).write_text(
        "Built by build_release.py. This folder is rebuilt from scratch each\n"
        "time, so nothing here should be edited by hand.\n", encoding="utf-8")

    for name in ALLOWLIST:
        origin = source / name
        if not origin.is_file():
            # A rename is the realistic cause. Skipping would ship a release
            # quietly short of a file, and still call it a success.
            problems.append(f"{name}: missing from {source}, so the release would be incomplete")
            continue
        target = dest / name
        # `dest / name` does not collapse "..", and it throws `dest` away entirely
        # if `name` happens to be absolute — so a mistyped allowlist entry could
        # write outside the release folder with no error from either call below.
        # Every entry is a reviewed literal today; this is for whoever edits the
        # list next. Raised by review, 2026-09-25.
        if not target.resolve().is_relative_to(dest):
            problems.append(f"{name}: would be written outside {dest}, so it is not "
                            f"a valid allowlist entry")
            continue
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(origin, target)

    problems.extend(audit(dest))
    return problems


def main(argv: list) -> int:
    here = pathlib.Path(__file__).resolve().parent
    if len(argv) == 2 and argv[0] == "--check":
        where = pathlib.Path(argv[1])
        print(f"Auditing {where}")
        problems = load_private_words(here) or audit(where)
    elif len(argv) == 1:
        where = pathlib.Path(argv[0])
        print(f"Building the public release of {here}\n           into {where}")
        # Before anything is built: without the list there is nothing to check
        # the names against, so there is no point clearing a folder for it.
        problems = load_private_words(here) or build(here, where)
    else:
        print(__doc__.strip().split("Usage:")[1].strip())
        return 2

    if problems:
        print(f"\n  REFUSED: {len(problems)} problem(s). Do not publish this folder.\n")
        for problem in problems:
            print(f"    - {problem}")
        return 1

    count = len(list(pathlib.Path(where).rglob("*")))
    print(f"\n  OK: {count} entries, nothing private found. Safe to publish.")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
