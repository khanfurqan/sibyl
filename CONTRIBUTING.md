# Contributing to Sibyl

Thank you for looking. This file covers the one rule that matters, how to run
the tests, how to measure a change, and a handful of things that are expensive
to rediscover.

## Contents

- [The one rule](#the-one-rule)
- [The shape of the code](#the-shape-of-the-code)
- [Running the tests](#running-the-tests)
- [Measuring a change to the clean-up](#measuring-a-change-to-the-clean-up)
- [Building a release](#building-a-release)
- [Measuring a change to the speech model](#measuring-a-change-to-the-speech-model)
- [Things that are expensive to rediscover](#things-that-are-expensive-to-rediscover)
- [Opening an issue](#opening-an-issue)
- [Opening a pull request](#opening-a-pull-request)

---

## The one rule

**Nothing leaves the machine.**

This is the product, not a feature of it. A change that adds a network call to
anything other than a local model runtime will not be merged, however useful it
is. That includes crash reporting, update checks, analytics, remote models, and
"opt-in" versions of any of those.

Concretely:

- `requests` is the only library Sibyl itself sends anything with, and it may only talk
  to `SIBYL_OLLAMA_HOST`, which accepts only `127.0.0.1`: `ollama_address` refuses
  anything else. Never read Ollama's own `OLLAMA_HOST`.
- **The one existing exception is the first-run model download.** `faster-whisper`
  fetches the speech weights from Hugging Face over its own transport the first
  time it loads, then caches them. That is a one-way fetch of a public file and
  is documented in the README's privacy audit. **Do not add a second exception**,
  and do not let this one grow: no version pings, no "check for a newer model",
  no usage counter attached to it.
- **No dictated text on disk, ever**, other than the temporary WAV that is
  deleted in a `finally` block. The log file is guarded by an automated test
  that searches it for a known dictated phrase.
- **No real dictation in the repository.** Real transcripts hold names, dates
  and places from someone's life. To reproduce a real failure, invent content of
  the same *shape* and keep the real transcript in a scratch file outside the
  project.

---

## The shape of the code

`sibyl.py` is a single file in nine numbered sections. That is deliberate: one
pipeline, read top to bottom, with no import graph to hold in your head.

| Section | What lives there |
|---|---|
| 1. Config | Every setting, the log, and `private()`, which keeps dictated text out of it |
| 2. Audio cues | The beeps, played on their own thread so they never block |
| 3. Recorder | Audio capture |
| 4. Transcription | Speech to text, behind a pluggable interface |
| 5. The LLM pipeline | **The clean-up prompt and the model call**, the plain-rule fixes, and `faithful`, the check after the model |
| 6. Injection | The one-copy lock, the target window, clipboard and paste |
| 7. Hotkeys | `TapListener`: holds, taps and stray keys |
| 8. Application | The dot (`Pill`), the tray, and `Sibyl`, which joins every stage together |
| 9. Staged self-tests | The `--test-*` commands, and `main` |

> **If output looks wrong, look at section 5 first.** Nearly every output defect
> this project has had was in those blocks of English, not in the code around
> them.

---

## Running the tests

Everything needed to run the tests is in `requirements.txt`. No microphone and
no keyboard are needed for any of them.

**All of them, plus a live clean-up:**

```powershell
.\Start-Sibyl.cmd check
```

**Or one file at a time:**

```
python test_taplistener.py   # tap and hold gestures
python test_talk.py          # a hold, a tap, a stray key, a microphone that will not open
python test_pill.py          # every state of the dot, real clicks, never takes focus
python test_recall.py        # Paste again, including with no target window
python test_fillers.py       # the plain rules: layout kept, "iPhone" kept, real phrases kept
python test_guard.py         # the check after the model, and every fallback path
python test_launch.py        # the log under pythonw, and none of your words in it
python test_eval_stt.py      # the speech-model scoring arithmetic
python test_release.py       # the publish gate: nothing private reaches a release
```

Two of them have requirements worth knowing:

- **`test_pill.py` needs a display and moves your mouse pointer** for a few
  seconds. It clicks the dot with real mouse input, because a simulated click
  would not prove the thing that matters — that the dot never steals focus. It
  puts a grey backdrop window under the dot so a missed click lands harmlessly.
  **It can fail because of what is on screen.** It clicks a fixed spot above the
  taskbar, so anything sitting there takes the clicks. The symptom is a cluster
  of failures starting at "the notch is on top of the test's backdrop", and it
  means nothing is wrong with the code. Seen on 2026-09-25 with a Sibyl up whose notch was a
  wide error chip; an idle Sibyl, which is a small dot, does not interfere. Look
  at your taskbar before you suspect the test.
- **`test_launch.py` needs `pythonw.exe`** to exist beside `python.exe`. It does
  on a normal Windows Python install.

**Tests are not optional here.** Almost every rule in the code exists because
something broke in a measurable way, and a test was added so it cannot break
again quietly. Changing a rule without changing its test usually means the rule
was not understood.

---

## Measuring a change to the clean-up

Sibyl has a scored test for the thing it actually does. It is the measure of the
product, and it needs Ollama running.

```powershell
python eval_cleanup.py --runs 3                        # the configured model
python eval_cleanup.py --model qwen2.5:1.5b --runs 3   # any candidate
```

50 cases. Each checks one behaviour: a spoken list becomes bullets, a correction
replaces rather than reorders, a hedge survives, nothing is invented, a dictated
instruction is not obeyed. The last 17 check that no meaning is reversed: a
negation, a number, names in order, "must", "only", "all", "unless".

**Four rules for using it. Each of them exists because it was got wrong once.**

1. **Run it three times, never once.** The current model scores 41 to 42 of the
   50. On the first 33, the baseline every change has been judged against, it
   scores a mean of 24.9 over nine runs, ranging 24 to 26. A change worth less than about one case is
   inside that noise. One candidate that looked like a gain over three runs
   reversed over the next three.
2. **Never measure by sending one input repeatedly.** The same sentence sent back
   to back behaved completely differently from the same sentence interleaved with
   others — 1 time in 10 versus 9 in 10. Real use never repeats a request, and
   `eval_cleanup.py` interleaves. The likely cause is the runtime reusing cached
   work from an identical request, but that is not proven.
3. **It scores the model's own answer, never the fallback's.** What actually gets
   pasted is often your own words, so scoring that would make a broken model look
   perfect. The check's verdict is reported beside the score: caught, missed,
   false alarm.
4. **When a case passes, read the output anyway.** An automated test passes
   whatever it does not look for. An early version of this file passed an
   invented meeting time because it only forbade one exact phrase.

**Do not run a long measurement on a laptop that may sleep.** A run spanning a
sleep is unusable — one reported 26,548 seconds for a single clean-up. Throw
those numbers away.

---

## Building a release

The public release folder is built by a script, not by hand, and the script
refuses rather than warns:

```powershell
python build_release.py C:\some\empty\folder      # build, then audit
python build_release.py --check C:\some\folder     # audit an existing folder
```

It copies an **explicit allowlist** of product files — never a wildcard, because
a wildcard does what the filesystem says rather than what was intended — and then
audits the folder it just produced. It exits 0 only if that folder holds no
private document, no email address, no home-directory path and no author name
outside `LICENSE`.

**It audits the output folder, not the allowlist.** Checking the allowlist would
only confirm its own intentions; reading the folder catches a file that arrived by
any route.

**Everything unknown is a problem.** A file that cannot be read is an unscanned
file, not a clean one. An empty allowlist refuses rather than reporting a clean
build of nothing. `test_release.py` proves each of those refusals, because a gate
that cannot fail is not a gate.

If you add a file that belongs in the public release, add it to `ALLOWLIST` in
`build_release.py`. If you forget, the build says the release is incomplete rather
than shipping without it.

## Measuring a change to the speech model

`eval_stt.py` scores speech models against recordings of your own voice. The
recordings are **never committed** — they are your voice saying real sentences.
They live outside the project folder.

```powershell
python eval_stt.py --download   # fetch a contender model, once
python eval_stt.py --record     # read the twelve texts aloud, about 10 minutes
python eval_stt.py --score      # the table, and a verdict per contender
```

`test_eval_stt.py` checks the scoring arithmetic itself, and needs no
recordings.

---

## Things that are expensive to rediscover

These are the findings that cost the most to learn. Each one was measured.

### About the clean-up prompt

- **The prompt is full.** Seven changes were measured in one session, three runs
  each. Six scored *worse* and broke cases that had been passing; the seventh did
  not survive a second measurement. The remaining known failures need a different
  model or a different mechanism, not more English.
- **Telling this model to do something can make it worse at that exact thing.** A
  rule instructing it to keep hedges made it lose *more* of them. A rule
  instructing it never to obey an instruction still wrote the email, and cost two
  hedge cases.
- **Prose does not hold a small model. Examples do.** Instructions get parroted or
  ignored; three worked examples fixed a problem that four attempts at wording
  could not. Reach for `CLEANUP_EXAMPLES` before another sentence of prose.
- **A worked example teaches its exact pattern, including a wrong one.** An
  example that showed a correction being *reordered* rather than *replaced*
  taught exactly that, and the model then kept both the wrong word and the right
  one. Read every example as the literal rule the model will copy.
- **Examples leak into output as content.** A concrete noun in the instructions
  can come back as if it had been said. Keep example subjects deliberately
  unrelated to real use, so a leak is unmistakable.
- **An instruction budget is a shared resource.** Adding an instruction takes
  attention from every other instruction, including the ones that stop the model
  inventing. **If something does not depend on what was said, it must be code or
  a constant, never an instruction.**

### About the rest

- **Never hand a model a job a plain rule does perfectly.** Fillers at sentence
  starts and known mis-hearings are fixed by deterministic code before the model
  sees the text. Instant, and cannot be got wrong.
- **A filler word is only a filler in the position it was measured in.** Both
  filler lists were widened once from imagination, and both broke: "and now, we
  wait" lost its *at this moment*, and "So you know, I will not be in tomorrow"
  lost its whole preface, because "so you know" means *for your information*.
  Widen either list only from measured answers.
- **Short test cases hide long-input failures.** Every original case was one or
  two sentences and passed; real 40–60 second dictation came back a third
  shorter. Every behaviour needs a long case too.
- **Telling the recogniser the hard names beats buying a bigger one.** Measured:
  the hint more than halved word errors and quadrupled name accuracy, for 0.03 s.
  A model 3.3× the size was slower, hungrier, and worse at names.
- **`"think": False` is sent on every model call and must stay.** Newer models
  reason before answering by default. One spent its entire output budget
  thinking and returned nothing.
- **Never block inside the keyboard callback, and never let an error escape
  it.** Blocking freezes the listener and silently drops every later keypress.
  An uncaught error makes the library stop the listener for good, so the hotkey
  goes dead with no warning at all.
- **Keys are matched by numeric code, never by name.** `pynput` exposes
  `Key.alt_r` and `Key.alt_gr` as unequal objects carrying the same code.
  Binding the wrong name fails silently, which looks exactly like dead hardware.
- **The dot must never take focus, not even when clicked.** Sibyl pastes into
  the window that was in front when you started; a status window that stole
  focus would *become* that window.
- **Never touch a window widget from the capture thread.** Cross-thread updates
  go on a queue. Direct calls appear to work, then corrupt the interpreter under
  load.

---

## Opening an issue

Please include:

1. **What you expected and what happened.**
2. **The last twenty lines of `%LOCALAPPDATA%\Sibyl\sibyl.log`.** These contain
   no dictated text.
3. **The output of `.\Start-Sibyl.cmd ollama`** and your Python version.
4. **Your Windows version and how much RAM was free** at the time.

**Please do not paste your `.env` file** — it may hold real names of real people
— **and please do not paste a real dictation.** Reproduce the problem with
invented text of the same shape.

---

## Opening a pull request

- **One change per pull request**, with the measurement that justifies it.
- **Every suite green.** Say so in the description, with the counts.
- **A change to the clean-up needs `eval_cleanup.py --runs 3`**, before and
  after, in the description. A change that does not move the number is not an
  improvement; it is a risk with no upside.
- **A new behaviour needs a test**, including a long-input case if length could
  plausibly matter.
- **Match the surrounding style.** Comments here explain *why*, and cite the
  measurement. Please keep that.
- **No new dependency without a reason** that the standard library cannot meet.
  Every dependency is a thing that can phone home in a future version, in a
  project whose whole claim is that nothing does.
