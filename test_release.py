"""Does the publish gate actually stop private material reaching a public repo?

The problem this closes. Roadmap step 9 pushes Sibyl to a public GitHub
repository, once, and that cannot be undone. Which files are private
(`CLAUDE.md`, `STATE.md`, `DECISIONS.md`, `HANDOVER.md`, `ROADMAP.md`,
`archive/`, `review/`) lived only as a sentence in `ROADMAP.md` and as a copy
somebody did by hand. Found by review on 2026-09-25, and measured: 42 of the 45
commits in this repository touch one of those paths, they hold 780 KB between
them, and the history carries the author's personal email address.

A gate that exists only as prose cannot run, so it cannot pass or fail. "I
checked and it was fine" and "I never checked" look exactly the same afterwards,
because there is no artifact either way. That is the bug class this file exists
for, and the reason every check below is written as "does it REFUSE", never
"does it allow".

Nine things to prove. Only the first is the feature; the other eight are the
gate refusing, because a gate that cannot fail is not a gate.

  1. The happy path: the real project builds, and the audit is clean.
  2. A private file planted in the output is caught. The whole point.
  3. A private DIRECTORY planted in the output is caught. `archive/` and
     `review/` are directories, and a name check that only looks at top-level
     files would miss every file inside them.
  4. An email address in any file is caught. The history carries a real one, so
     a stray copy in a comment is a live risk, not a hypothetical.
  5. The author's name outside LICENSE is caught, and
  6. inside LICENSE is allowed, because the copyright line is meant to be there.
     One rule with an exception is where a gate usually goes wrong, so both
     directions are proved.
  7. A missing allowlisted file FAILS rather than being skipped. A rename would
     otherwise silently ship a release with a file absent.
  8. A file that cannot be read FAILS rather than being passed over. An
     unreadable file is an unscanned file, and an unscanned file is not a clean
     one.
  9. An empty allowlist FAILS. A gate whose input vanished must not report a
     clean build of nothing, which is the exact shape of the 2026-09-06 miss
     that this project already paid for once.

Needs no microphone, no models, no keyboard and no network.

Run:  python test_release.py
"""
import contextlib
import io
import os
import pathlib
import shutil
import sys
import tempfile

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[0]))
import build_release

# The words that would identify the author live in a private, git-ignored file
# (build_release.PRIVATE_WORDS_FILE), never in a published one. Until 2026-09-26
# this file and build_release.py spelled out the author's first name and username
# in order to search for them, so publishing the gate published them (review
# round 3). These tests use invented words in their place, so they run the same on
# any copy of the project, with or without the maintainer's private list. Each is
# assembled from two halves, because this file is itself searched for the private
# words: written out whole here, they would be found in it.
INVENTED_NAME = "quill" + "feather"
INVENTED_WORD = "zorb" + "wick"
build_release.NAME_WORD = INVENTED_NAME
build_release.PRIVATE_WORDS = [INVENTED_WORD]

results = []


def check(name, ok, detail=""):
    print(f"  {'PASS' if ok else 'FAIL'}  {name}" + (f"\n        {detail}" if detail else ""))
    results.append(ok)


def scratch():
    """A throwaway folder. Never the project, because these tests plant files
    that must never exist here."""
    return pathlib.Path(tempfile.mkdtemp(prefix="sibyl-release-test-"))


# -- 1. the happy path ------------------------------------------------------
# Built from the real project, so this is the release that would actually be
# pushed, not a fixture that resembles it.

print("\nThe real project builds clean")
dest = scratch() / "out"
problems = build_release.build(pathlib.Path(__file__).parent, dest)
check("the real project builds with no problems",
      problems == [], "; ".join(problems) if problems else f"{dest}")
check("and the build is not empty",
      dest.exists() and len(list(dest.rglob("*"))) > 10,
      f"{len(list(dest.rglob('*'))) if dest.exists() else 0} entries")
check("and it holds the exact-version install list the install guide uses",
      (dest / "requirements-lock.txt").is_file())
check("and the double-click launcher the install guide leads with",
      (dest / "Start-Sibyl.cmd").is_file())

# The two install lists must agree: every package requirements.txt names is
# pinned in requirements-lock.txt, at a version requirements.txt allows. Both
# files use one simple form each ("name>=1.2" and "name==1.2.3"), so they are
# read here with plain Python, and any other line is named rather than guessed
# at: a regenerated file in another form fails with its line, not a traceback
# (deep review of batch C, 2026-09-26).
import re   # noqa: E402

here = pathlib.Path(__file__).parent


def version_numbers(text):
    return tuple(int(n) for n in re.findall(r"\d+", text))


def package(name):
    return re.sub(r"[-_.]+", "-", name).lower()


pins, disagree = {}, []
for line in (here / "requirements-lock.txt").read_text(encoding="utf-8").splitlines():
    if not line.strip() or line.startswith("#"):
        continue
    m = re.fullmatch(r"([A-Za-z0-9_.-]+)==([0-9][0-9.]*)", line.strip())
    if m:
        pins[package(m.group(1))] = m.group(2)
    else:
        disagree.append(f"requirements-lock.txt line not understood: {line!r}")
for line in (here / "requirements.txt").read_text(encoding="utf-8").splitlines():
    if not line[:1].isalpha():
        continue
    m = re.fullmatch(r"([A-Za-z0-9_.-]+)\s*>=\s*([0-9][0-9.]*)", line.split("#")[0].strip())
    if not m:
        disagree.append(f"requirements.txt line not understood: {line!r}")
        continue
    pinned = pins.get(package(m.group(1)))
    if pinned is None or version_numbers(pinned) < version_numbers(m.group(2)):
        disagree.append(f"{m.group(1)}>={m.group(2)} but the lock has {pinned}")
check("the lock pins every package requirements.txt names, at a version it allows",
      disagree == [] and len(pins) > 0, "; ".join(disagree))


# -- 2 and 3. private material planted in the output ------------------------
# The audit runs on the FOLDER, not on the allowlist, so it catches a file that
# arrived by any route: a stale copy, a wildcard, a hand edit after the build.

print("\nPrivate material is refused")
for name in build_release.FORBIDDEN_NAMES:
    box = scratch()
    (box / name).write_text("private", encoding="utf-8")
    problems = build_release.audit(box)
    check(f"a planted {name} is caught",
          any(name in p for p in problems), "; ".join(problems) or "NOTHING REPORTED")

for folder in build_release.FORBIDDEN_DIRS:
    box = scratch()
    (box / folder).mkdir()
    (box / folder / "old-notes.md").write_text("private", encoding="utf-8")
    problems = build_release.audit(box)
    check(f"a planted {folder}/ directory is caught",
          any(folder in p for p in problems), "; ".join(problems) or "NOTHING REPORTED")


# -- 4. an email address ----------------------------------------------------

print("\nAn email address is refused")
box = scratch()
(box / "sibyl.py").write_text("# contact someone@example.com\n", encoding="utf-8")
problems = build_release.audit(box)
check("an email address in any file is caught",
      any("email" in p.lower() for p in problems), "; ".join(problems) or "NOTHING REPORTED")


# -- 4b. a binary file's metadata -------------------------------------------
# Images are published, and a text search does not reach inside them. The
# surname was missing from the byte list until review on 2026-09-25, and then had
# no test until a mutation test showed the needle could be deleted with every
# check still passing. Each needle gets a case, on bytes that are deliberately
# not valid UTF-8 so the binary branch is the one that runs.

print("\nA binary file's metadata is refused")
for needle, label in [(INVENTED_NAME.title().encode(), "the author's name"),
                      (INVENTED_WORD.encode(), "a private word, such as a username"),
                      (b"someone@gmail.com", "an email")]:
    box = scratch()
    (box / "logo.png").write_bytes(b"\x89PNG\r\n\x1a\n\xff\xfe" + b"Author: " + needle)
    problems = build_release.audit(box)
    check(f"{label} in an image's bytes is caught",
          any("binary" in p for p in problems),
          "; ".join(problems) or "NOTHING REPORTED")


# -- 5 and 6. the author's name, and the one place it belongs ---------------

print("\nThe author's name: refused everywhere but LICENSE")
box = scratch()
(box / "README.md").write_text(f"Written by {INVENTED_NAME.title()}.\n", encoding="utf-8")
problems = build_release.audit(box)
check("the name in README.md is caught",
      any("README.md" in p for p in problems), "; ".join(problems) or "NOTHING REPORTED")

box = scratch()
(box / "LICENSE").write_text(f"Copyright (c) 2026 {INVENTED_NAME.title()}\n", encoding="utf-8")
problems = build_release.audit(box)
check("the same name in LICENSE is allowed",
      problems == [], "; ".join(problems))

box = scratch()
(box / "INSTALL.md").write_text(f"cd C:\\home\\{INVENTED_WORD}\n", encoding="utf-8")
problems = build_release.audit(box)
check("another private word, such as a username, is caught in any text file",
      any(INVENTED_WORD in p for p in problems), "; ".join(problems) or "NOTHING REPORTED")

# Each home-path form gets its own case, and the username is deliberately one
# that is NOT a private word. The first version of this check used a real
# username that happened to be a secret word, so it passed on that word and would
# have passed with the HOME_PATH pattern deleted entirely. Caught by review,
# 2026-09-25. The forward-slash Windows form was genuinely missing.
# The capitalised forward-slash case is here because a mutation test showed the
# others did not need the Windows forward-slash alternative at all: `/Users/someone`
# inside `C:/Users/someone` already matches the macOS branch. What that branch does
# NOT match is a capitalised name, because its character class is lowercase only.
# So this is the one case that makes the Windows alternative load-bearing, and
# without it the alternative could be deleted with every test still passing.
for path_text, form in [
    ("C:\\Users\\someone\\Desktop", "backslash Windows"),
    ("C:/Users/someone/Desktop", "forward-slash Windows"),
    ("C:/Users/Someone/Desktop", "forward-slash Windows, capitalised name"),
    ("/Users/someone/Documents", "macOS"),
]:
    box = scratch()
    (box / "sibyl.py").write_text(f"# path {path_text}\n", encoding="utf-8")
    problems = build_release.audit(box)
    check(f"a home-directory path is caught: {form}",
          any("home-directory" in p for p in problems),
          "; ".join(problems) or "NOTHING REPORTED")


# -- 7. a missing allowlisted file fails, it is not skipped -----------------
# A rename is the realistic cause. Skipping would ship a release quietly short
# of a file, and the build would still say it succeeded.

print("\nThe gate fails closed")

# An empty folder is never a valid release. Nothing audited it until a mutation
# test showed this guard could be deleted unnoticed, 2026-09-25 — and "audit found
# no problems in a folder with nothing in it" is the purest form of a check that
# passes because it could not run.
problems = build_release.audit(scratch())
check("an empty folder is refused, not called clean",
      any("empty" in p for p in problems),
      "; ".join(problems) or "NOTHING REPORTED - an empty folder passed the audit")

# And a path that is not a folder at all.
box = scratch()
(box / "not-a-folder").write_text("x\n", encoding="utf-8")
problems = build_release.audit(box / "not-a-folder")
# The reason is asserted, not just the refusal: rglob on a file yields nothing, so
# the empty-folder guard above would also fire and this one would look redundant.
check("auditing something that is not a folder is refused, and says so",
      any("is not a folder" in p for p in problems),
      "; ".join(problems) or "NOTHING REPORTED")
box = scratch()
src = box / "project"
src.mkdir()
(src / "sibyl.py").write_text("print('hi')\n", encoding="utf-8")
problems = build_release.build(src, box / "out")
check("a missing allowlisted file is a problem, not a silent skip",
      any("missing" in p.lower() for p in problems),
      f"{len(problems)} problem(s): " + ("; ".join(problems[:3]) or "NONE REPORTED"))


# -- 8. an unreadable file fails, it is not passed over ---------------------
# Simulated by a name that exists as a DIRECTORY where a file is expected:
# read_text then raises, and the question is whether the gate treats that as
# clean. An unscanned file is not a clean file.

box = scratch()
(box / "notes.md").mkdir()
try:
    problems = build_release.audit(box)
    ok = problems != []
    detail = "; ".join(problems) or "NOTHING REPORTED - treated as clean"
except Exception as exc:
    ok = False
    detail = f"raised instead of reporting: {type(exc).__name__}: {exc}"
check("a directory named like a file is a problem, never a pass", ok, detail)

# The check above trips the "directory named like a file" branch and never
# reaches `_scan_file`, so it does not prove what it used to claim. Both
# reviewers caught that on 2026-09-25, and it means `_scan_file`'s `except
# OSError` had no coverage at all: reorder the read and the exemption inside it
# and every check would still have passed. So call `_scan_file` directly, on a
# path that genuinely cannot be read.
box = scratch()
unreadable = box / "sibyl.py"
unreadable.mkdir()                       # read_bytes on a directory raises OSError
found = build_release._scan_file(unreadable, pathlib.Path("sibyl.py"))
check("_scan_file reports a file it could not read, rather than returning clean",
      any("could not be read" in p for p in found),
      "; ".join(found) or "RETURNED CLEAN - an unscanned file was called a clean one")

# And the same for an EXEMPT name, which is the ordering the comment in
# `_scan_file` exists to protect: read first, exempt second.
box = scratch()
unreadable = box / "build_release.py"
unreadable.mkdir()
found = build_release._scan_file(unreadable, pathlib.Path("build_release.py"))
check("an exempt file that cannot be read is reported, not waved through",
      any("could not be read" in p for p in found),
      "; ".join(found) or "RETURNED CLEAN - the exemption ran before the read")

# The nested-file guard (`rel.parts[:-1]`) needs its own case too: the earlier
# planted-directory check is satisfied by the top-level name match alone, so that
# guard was never exercised on its own.
box = scratch()
nested = box / "outer" / "review" / "deep"
nested.mkdir(parents=True)
(nested / "notes.txt").write_text("private\n", encoding="utf-8")
problems = build_release.audit(box)
check("a file nested deep inside a private directory is caught",
      any("notes.txt" in p and "private directory" in p for p in problems),
      "; ".join(problems) or "NOTHING REPORTED")

# And the exemption must not follow its own name into another folder.
box = scratch()
(box / "brand").mkdir()
(box / "brand" / "build_release.py").write_text("someone@example.com\n", encoding="utf-8")
problems = build_release.audit(box)
check("an exempt NAME in another folder is not exempt",
      problems != [],
      "; ".join(problems) or "NOTHING REPORTED - the exemption followed the filename")


# -- 9. an empty allowlist fails --------------------------------------------
# The 2026-09-06 miss in this workspace, exactly: a gate whose wordlist was
# empty reported PASS. Restored after the check, so later runs are unaffected.

real_allowlist = build_release.ALLOWLIST
try:
    build_release.ALLOWLIST = []
    box = scratch()
    problems = build_release.build(pathlib.Path(__file__).parent, box / "out")
    check("an empty allowlist refuses, rather than passing a build of nothing",
          problems != [], "; ".join(problems) or "NOTHING REPORTED - passed on nothing")
finally:
    build_release.ALLOWLIST = real_allowlist

check("the allowlist was restored after that test",
      build_release.ALLOWLIST == real_allowlist and len(build_release.ALLOWLIST) > 10,
      f"{len(build_release.ALLOWLIST)} entries")


# -- 10. the content-scan exemption is narrow -------------------------------
# The gate and its test cannot be text-scanned, because they must spell out
# every word they search for. That exemption is the weakest point in this file,
# so it is bounded from both sides: it must stay small, and it must not buy an
# exempt file any other kind of pass.

print("\nThe content-scan exemption stays narrow")
check("only two files are exempt from the text scan",
      len(build_release.CONTENT_SCAN_EXEMPT) == 2,
      f"{build_release.CONTENT_SCAN_EXEMPT}")

box = scratch()
(box / "review").mkdir()
(box / "review" / "build_release.py").write_text("x\n", encoding="utf-8")
problems = build_release.audit(box)
check("an exempt file inside a private directory is still caught",
      any("review" in p for p in problems), "; ".join(problems) or "NOTHING REPORTED")

box = scratch()
(box / "build_release.py").mkdir()          # unreadable: a directory, not a file
problems = build_release.audit(box)
check("an exempt name that cannot be read is still caught",
      problems != [], "; ".join(problems) or "NOTHING REPORTED")


# -- 11. case, in both directions -------------------------------------------
# Windows filesystems are case-insensitive, so `state.md` and `STATE.md` are the
# same file to the operating system and different strings to Python. Measured
# 2026-09-25 while this file was being written: four case-different private
# documents were planted and the audit reported ZERO problems. Their text held
# no email and no name, so no content check saved it either.
#
# The rule that fixes it, and the reason it is not "compare everything
# case-insensitively": a PROHIBITION is safer wide, so those match without
# regard to case. An EXEMPTION is safer narrow, so those stay exact. A file
# named `license` therefore gets flagged for holding the author's name, which is
# a false alarm that costs a rename — the opposite mistake costs a publication.

print("\nCase does not open a hole")
box = scratch()
odd = ["state.md", "Claude.md", "DECISIONS.MD", "Roadmap.md", "Handover.Md"]
for name in odd:
    (box / name).write_text("notes with no name and no email in them\n", encoding="utf-8")
problems = build_release.audit(box)
check("a private document in the wrong case is still caught",
      len(problems) >= len(odd),
      f"{len(problems)} problem(s) for {len(odd)} files: " + ("; ".join(problems) or "NOTHING REPORTED"))

box = scratch()
(box / "ARCHIVE").mkdir()
(box / "ARCHIVE" / "notes.md").write_text("x\n", encoding="utf-8")
problems = build_release.audit(box)
check("a private directory in the wrong case is still caught",
      problems != [], "; ".join(problems) or "NOTHING REPORTED")

# An EMPTY one, so only the directory-name check can fire. With a file inside, the
# nested-path check catches it too and the name check looks redundant — which a
# mutation test showed it was, as tested. 2026-09-25.
box = scratch()
(box / "REVIEW").mkdir()
(box / "keeps-the-folder-from-being-empty.md").write_text("x\n", encoding="utf-8")
problems = build_release.audit(box)
check("an empty private directory is caught by its name alone",
      any("REVIEW" in p for p in problems),
      "; ".join(problems) or "NOTHING REPORTED")

box = scratch()
(box / "license").write_text(f"Copyright (c) 2026 {INVENTED_NAME.title()}\n", encoding="utf-8")
problems = build_release.audit(box)
check("the LICENSE exemption stays exact, so lowercase 'license' is flagged",
      problems != [],
      "; ".join(problems) or "NOTHING REPORTED - the exemption widened, which is the wrong direction")

box = scratch()
(box / "Build_Release.py").write_text("someone@example.com\n", encoding="utf-8")
problems = build_release.audit(box)
check("the content-scan exemption stays exact too",
      problems != [], "; ".join(problems) or "NOTHING REPORTED - the exemption widened")


# -- 12. the destination is never destroyed ---------------------------------
# `build` used to call shutil.rmtree on whatever path it was handed. Measured on
# a disposable copy, 2026-09-25: `python build_release.py .` run inside a project
# folder deleted that folder's contents INCLUDING `.git`, then died with a
# confusing WinError 32 about the folder being in use. The real invocation is one
# manual command typed immediately before an irreversible push, which is the
# worst possible moment for a mistyped path.
#
# So every check here asserts two things at once: that it REFUSES, and that the
# files were still there afterwards. A guard that refuses after deleting is no
# guard at all.

print("\nThe destination is never destroyed")


def throwaway_project():
    """A disposable source tree, inside a scratch folder."""
    root = scratch()
    src = root / "project"
    src.mkdir()
    (src / "sibyl.py").write_text("print('x')\n", encoding="utf-8")
    return root, src


def refused_with_sentinel(pick_target, label, because):
    """Target is inside the scratch folder, so a sentinel file can safely be
    planted in it and checked afterwards.

    `because` is asserted, not just "it refused somehow". Several guards overlap —
    a folder that is the project's parent is also a folder that already holds
    files — so a test that accepts any refusal passes even with the guard it names
    deleted. A mutation test caught exactly that here on 2026-09-25.
    """
    root, src = throwaway_project()
    target = pick_target(root, src)
    sentinel = target / "precious.txt"
    sentinel.write_text("must survive\n", encoding="utf-8")
    problems = build_release.build(src, target)
    check(f"{label}: refused, and for the right reason",
          any(because in p for p in problems),
          f"wanted {because!r}; got: " + ("; ".join(problems) or "NOTHING REPORTED"))
    check(f"{label}: the target kept its files", sentinel.exists(),
          f"sentinel still present: {sentinel.exists()}")


def refused_untouched(target, label):
    """For real places on this machine — the home directory, a drive root.

    **This calls the guard directly and never calls `build`.** That is not
    squeamishness, it is a scar. On 2026-09-25 an earlier version of this helper
    called `build(src, home)`, relying on the guard to refuse. Someone then
    removed the guard on purpose, to check that these tests would notice — the
    ordinary way to prove a test is load-bearing — and `shutil.rmtree` ran on the
    home directory. Windows' lock on NTUSER.DAT stopped it after one entry, and
    `~/.cache` was emptied: about 800 MB of re-downloadable model weights.

    The rule that follows: a test must never hand a real, valuable path to the
    destructive function, however good the guard in front of it is. Verifying a
    guard means deleting it, and at that moment the test's fixture is all that is
    left. So the fixture is a question, not an action.
    """
    _, reason = build_release._check_destination(pathlib.Path(__file__).parent, target)
    check(f"{label}: the guard gives a reason to refuse",
          bool(reason), repr(reason))


refused_with_sentinel(lambda root, src: src, "the project folder itself",
                      "is the project itself")
refused_with_sentinel(lambda root, src: root, "a folder containing the project",
                      "or a folder containing it")

# The home and drive-root guards cannot be isolated with the real project as the
# source, because this project lives under the home directory on the system drive,
# so the ancestor guard fires first and both look redundant. They are not: they
# matter for anyone whose project sits on another drive. So each gets a SYNTHETIC
# source that the target is not an ancestor of. Nothing is written anywhere.
_, reason = build_release._check_destination(
    pathlib.Path("C:/ProgramData/not-a-real-project"), pathlib.Path.home())
check("the home directory is refused on its own merits",
      bool(reason) and "home directory" in (reason or ""), repr(reason))

_, reason = build_release._check_destination(
    pathlib.Path("C:/ProgramData/not-a-real-project"), pathlib.Path("Z:/"))
check("a drive root is refused on its own merits",
      bool(reason) and "drive root" in (reason or ""), repr(reason))

# And the real home and the real system-drive root still refuse, by whichever
# guard gets there first. Nothing is written into either.
refused_untouched(pathlib.Path.home(), "the real home directory")
refused_untouched(pathlib.Path(pathlib.Path.cwd().anchor), "the real drive root")

# -- 13. the four guards that had no test at all -----------------------------
# Review, 2026-09-25: the cwd branch, the "exists and is not a folder" branch and
# the `.resolve()` OSError branch could each have been deleted with all 44 checks
# still passing. A guard nobody can fail is indistinguishable, to the next editor,
# from one that has already regressed. The .git branch is new, and is the one that
# matters most in practice.

print("\nThe guards that had no test")

# A repository is never build output. Step 9 turns a release folder into the
# public repo, and that folder keeps the marker that permits deleting it.
root, src = throwaway_project()
repo = root / "published"
repo.mkdir()
(repo / ".git").mkdir()
(repo / ".git" / "HEAD").write_text("ref: refs/heads/main\n", encoding="utf-8")
(repo / build_release.MARKER).write_text("from an earlier build\n", encoding="utf-8")
problems = build_release.build(src, repo)
check("a folder holding a .git is refused, marker or no marker",
      any(".git" in p for p in problems), "; ".join(problems) or "NOTHING REPORTED")
check("and its history survived", (repo / ".git" / "HEAD").exists(),
      f"HEAD present: {(repo / '.git' / 'HEAD').exists()}")

# The working directory, on its own. Every earlier spelling of "." was also the
# project or its parent, so this branch was never the one that fired.
here = pathlib.Path.cwd()
try:
    empty = scratch()
    os.chdir(empty)
    _, reason = build_release._check_destination(pathlib.Path(__file__).parent,
                                                 pathlib.Path.cwd())
    check("the working directory is refused on its own merits",
          bool(reason) and "standing in" in (reason or ""), repr(reason))
finally:
    os.chdir(here)

# A destination that already exists as a plain file.
box = scratch()
(box / "out").write_text("not a folder\n", encoding="utf-8")
_, reason = build_release._check_destination(pathlib.Path(__file__).parent, box / "out")
check("a destination that is a file, not a folder, is refused",
      bool(reason) and "not a folder" in (reason or ""), repr(reason))

# And if the path cannot be worked out at all, that refuses rather than raising.
real_resolve = pathlib.Path.resolve
try:
    def explode(self, *a, **k):
        raise OSError("simulated")
    pathlib.Path.resolve = explode
    _, reason = build_release._check_destination(pathlib.Path("x"), pathlib.Path("y"))
    check("a path that cannot be resolved refuses, rather than raising",
          bool(reason), repr(reason))
finally:
    pathlib.Path.resolve = real_resolve
check("resolve was put back", pathlib.Path.resolve is real_resolve, "")

# An allowlist entry that would escape the destination.
real_allowlist = build_release.ALLOWLIST
try:
    build_release.ALLOWLIST = ["sibyl.py", "../escaped.txt"]
    root, src = throwaway_project()
    (src.parent / "escaped.txt").write_text("outside\n", encoding="utf-8")
    problems = build_release.build(src, root / "out")
    check("an allowlist entry pointing outside the release is refused",
          any("outside" in p for p in problems),
          "; ".join(problems) or "NOTHING REPORTED")
finally:
    build_release.ALLOWLIST = real_allowlist
check("the allowlist was put back again",
      build_release.ALLOWLIST is real_allowlist, f"{len(build_release.ALLOWLIST)} entries")

# A folder that already holds somebody else's files is not ours to clear.
root = scratch()
src = root / "project"
src.mkdir()
(src / "sibyl.py").write_text("print('x')\n", encoding="utf-8")
busy = root / "someone-elses-work"
busy.mkdir()
(busy / "thesis.docx").write_text("years of work\n", encoding="utf-8")
problems = build_release.build(src, busy)
check("a folder that already holds unrelated files: refused",
      problems != [], "; ".join(problems) or "NOTHING REPORTED")
check("a folder that already holds unrelated files: they survived",
      (busy / "thesis.docx").exists(), f"present: {(busy / 'thesis.docx').exists()}")

# But rebuilding over a previous release must still work, or the friction pushes
# people into deleting folders by hand, which is the same danger with no guard.
root = scratch()
first = build_release.build(pathlib.Path(__file__).parent, root / "out")
spoken = io.StringIO()
with contextlib.redirect_stdout(spoken):
    second = build_release.build(pathlib.Path(__file__).parent, root / "out")
check("rebuilding over a previous release still works",
      first == [] and second == [],
      f"first: {first or 'clean'} / second: {second or 'clean'}")

# The marker a build writes grants every later build permission to clear that
# folder, so anything dropped in meanwhile goes too. That is correct for an output
# folder and was silent until 2026-09-25. Silence is the part that was wrong.
check("clearing a previous release says so out loud",
      "clearing a previous release" in spoken.getvalue(),
      repr(spoken.getvalue().strip()[:90]) or "SAID NOTHING")


# -- 14. the private word list ---------------------------------------------------
# The author's name and other identifying words used to be written into this
# file and build_release.py, which are published, so the gate published the very
# words it guarded (review round 3, 2026-09-26). They now live in a git-ignored
# file that is read at build time, and every way that file can be missing or
# wrong refuses: a gate whose wordlist is empty is how this workspace once passed
# a check on nothing (2026-09-06).

print("\nThe private word list: kept out of the published files, and required")
LIST = build_release.PRIVATE_WORDS_FILE

saved = (build_release.NAME_WORD, build_release.PRIVATE_WORDS)
try:
    build_release.NAME_WORD, build_release.PRIVATE_WORDS = None, []
    box = scratch()
    (box / "README.md").write_text("nothing private here\n", encoding="utf-8")
    problems = build_release.audit(box)
    check("with no private word list loaded, the audit refuses rather than passing",
          any(LIST in p for p in problems),
          "; ".join(problems) or "NOTHING REPORTED - names were never checked")
finally:
    build_release.NAME_WORD, build_release.PRIVATE_WORDS = saved


def load(text):
    """load_private_words on a scratch folder holding `text`, or no file at all.
    Returns what it reported and what it loaded; the invented words are put back."""
    box = scratch()
    if text is not None:
        (box / LIST).write_text(text, encoding="utf-8")
    before = (build_release.NAME_WORD, build_release.PRIVATE_WORDS)
    try:
        reported = build_release.load_private_words(box)
        loaded = (build_release.NAME_WORD, list(build_release.PRIVATE_WORDS))
    finally:
        build_release.NAME_WORD, build_release.PRIVATE_WORDS = before
    return reported, loaded


reported, _ = load(None)
check("a missing private word list refuses", reported != [],
      "; ".join(reported) or "NOTHING REPORTED")
# Five words, so the missing name is the only thing wrong: with fewer, the
# count refuses too and hides this rule (review, apollo, 2026-10-10).
FOUR_WORDS = ["plimsoll", "tarnby", "gublet", "mossreach"]
reported, _ = load(f"# who wrote it is missing\nword: {INVENTED_WORD}\n"
                   + "".join(f"word: {w}\n" for w in FOUR_WORDS))
check("a list naming no author refuses", reported != [],
      "; ".join(reported) or "NOTHING REPORTED")
reported, _ = load(f"name: {INVENTED_NAME}\n")
check("a list holding no private words refuses", reported != [],
      "; ".join(reported) or "NOTHING REPORTED - an empty wordlist passed")
# A valid name and word too, so the misspelt line is the only thing wrong: with
# nothing else valid, the word count also refuses and hides this rule
# (a mutation test found exactly that, 2026-09-26).
reported, _ = load(f"name: {INVENTED_NAME}\nword: {INVENTED_WORD}\nwoord: plimsoll\n")
check("a line the gate cannot read refuses, rather than being skipped", reported != [],
      "; ".join(reported) or "NOTHING REPORTED - a misspelt line was ignored")
# A note after an entry must refuse, not be stored as part of the word: stored,
# the gate searched for "the word  # the note", found it nowhere, and called the
# release safe with the real word never checked (review, artemis, 2026-09-26).
reported, _ = load(f"name: {INVENTED_NAME}\nword: {INVENTED_WORD}  # a note to self\n")
check("a note after an entry refuses, rather than hiding the word it follows",
      reported != [], "; ".join(reported) or "NOTHING REPORTED - the word was never searched for")
reported, loaded = load(f"# the author\nName: {INVENTED_NAME.title()}\n"
                        f"word: {INVENTED_WORD.title()}\n"
                        + "".join(f"word: {w}\n" for w in FOUR_WORDS))
check("a well-formed list loads, in lower case",
      reported == [] and loaded == (INVENTED_NAME, [INVENTED_WORD] + FOUR_WORDS),
      f"{reported} {loaded}")
# The project's old names are private words since 2026-10-10, and a published
# file cannot name them, so the build refuses a list too short to hold them all:
# deleting a word from it must stop the build, not only a test (review, ares and
# artemis, 2026-10-10). A repeated word counts once, or it could hide a deletion.
reported, _ = load(f"name: {INVENTED_NAME}\n" + "".join(f"word: {w}\n" for w in FOUR_WORDS))
check("a list of fewer than five different private words refuses", reported != [],
      "; ".join(reported) or "NOTHING REPORTED - a short list passed")
reported, _ = load(f"name: {INVENTED_NAME}\n"
                   + "".join(f"word: {w}\n" for w in FOUR_WORDS + ["Tarnby"]))
check("a repeated word counts once", reported != [],
      "; ".join(reported) or "NOTHING REPORTED - a repeat made up the count")

# The two exempt files are exempt only from the checks whose search terms they
# must spell out. The private words are no longer among those, so they are
# searched for there like everywhere else.
box = scratch()
(box / "build_release.py").write_text(f"SECRET = [{INVENTED_WORD!r}]\n", encoding="utf-8")
problems = build_release.audit(box)
check("a private word inside the exempt build_release.py is caught",
      any(INVENTED_WORD in p for p in problems), "; ".join(problems) or "NOTHING REPORTED")
box = scratch()
(box / "test_release.py").write_text(f"# written by {INVENTED_NAME}\n", encoding="utf-8")
problems = build_release.audit(box)
check("and the author's name inside the exempt test_release.py",
      any("author" in p for p in problems), "; ".join(problems) or "NOTHING REPORTED")

# The list itself must never be published, nor ever committed. Planted holding
# no private word, so its NAME is the only thing that can catch it: with the words
# inside, the text scan also fires and hides the name rule (mutation test,
# 2026-09-26).
box = scratch()
(box / LIST).write_text("# a list with nothing in it yet\n", encoding="utf-8")
problems = build_release.audit(box)
check("the private word list planted in a release is caught by its name",
      any(LIST in p for p in problems), "; ".join(problems) or "NOTHING REPORTED")
ignored = (pathlib.Path(__file__).parent / ".gitignore").read_text(encoding="utf-8")
check(".gitignore keeps the private word list out of git",
      LIST in ignored.split(), f"{LIST!r} not a line of .gitignore")

# With the maintainer's real list, which a public copy of the project will not
# have, the release is audited with the real words too. On this machine that is
# the proof the move worked: the published scripts no longer hold them.
here = pathlib.Path(__file__).parent
if (here / LIST).exists():
    before = (build_release.NAME_WORD, build_release.PRIVATE_WORDS)
    try:
        problems = (build_release.load_private_words(here)
                    or build_release.build(here, scratch() / "out"))
        check("with the maintainer's real private words, the release is still clean",
              problems == [], "; ".join(problems))
    finally:
        build_release.NAME_WORD, build_release.PRIVATE_WORDS = before
else:
    print(f"  (no {LIST} here, as on any public copy: the real-word audit is skipped)")


passed = sum(results)
print(f"\n{passed}/{len(results)} passed")
sys.exit(0 if passed == len(results) else 1)
