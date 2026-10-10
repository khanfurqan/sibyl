"""The scoring maths behind the speech-model comparison.

`eval_stt.py` decides which speech model Sibyl should hear with. That decision
is only as good as its arithmetic, and the arithmetic is easy to get subtly
wrong: an accent or a comma counted as a wrong word would make every contender
look worse than it is, and a hint that plants a name the speaker never said
would look like a win rather than the failure it is.

So this file works every sum by hand first, then checks the code agrees:

  * identical words cost nothing; one wrong, one missing and one extra word
    each cost exactly one edit;
  * accents, punctuation, hyphens, case and the reader's [directions] are not
    words, and a curly apostrophe is the same as a straight one;
  * a known short example scores a word error rate of 1/7 with 2 of 3 names
    heard right;
  * a name the contender added but nobody said is counted as invented;
  * each of the four winning rules refuses on its own, and together they pass.

No microphone, no models, no recordings: only the arithmetic.

Run:  python test_eval_stt.py
"""
import contextlib
import io
import json
import os
import pathlib
import shutil
import sys
import tempfile
import time

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[0]))
import eval_stt
import sibyl

results = []


def check(name, ok, detail=""):
    print(f"  {'PASS' if ok else 'FAIL'}  {name}" + (f"\n        {detail}" if detail else ""))
    results.append(ok)


# -- counting edits ------------------------------------------------------------

print("Counting wrong, missing and extra words")

same = ["the", "meeting", "went", "well"]
check("identical words cost nothing", eval_stt.edits(same, list(same)) == 0)

check("one wrong word costs one edit",
      eval_stt.edits(same, ["the", "meeting", "want", "well"]) == 1)

check("one missing word costs one edit",
      eval_stt.edits(same, ["the", "meeting", "well"]) == 1)

check("one extra word costs one edit",
      eval_stt.edits(same, ["the", "meeting", "went", "very", "well"]) == 1)

check("nothing heard costs one edit per word said",
      eval_stt.edits(same, []) == 4)


# -- what counts as a word -----------------------------------------------------

print("\nWhat counts as a word")

check("accents, punctuation, hyphens and case fall away",
      eval_stt.words("Champs-Élysées, Sibyl!") == ["champs", "elysees", "sibyl"],
      eval_stt.words("Champs-Élysées, Sibyl!"))

check("a direction to the reader is not a word",
      eval_stt.words("went well [cough once] and the notes") ==
      ["went", "well", "and", "the", "notes"],
      eval_stt.words("went well [cough once] and the notes"))

check("a curly apostrophe reads as a straight one",
      eval_stt.words("it’s ready") == eval_stt.words("it's ready") == ["it's", "ready"])

check("a lone apostrophe is not a word",
      eval_stt.words("the ' meeting") == ["the", "meeting"])


# -- scoring a whole contender --------------------------------------------------

print("\nScoring a contender against what was read")

said = ["Sibyl met Siobhan on the Champs-Élysées"]
heard = ["Lina met Siobhan on the Champs Elysees"]
r = eval_stt.score(said, heard)
check("one name misheard out of seven words is a word error rate of 1/7",
      abs(r["wer"] - 1 / 7) < 1e-9, f"wer={r['wer']:.4f}")
check("two of the three names read were heard right",
      abs(r["names"] - 2 / 3) < 1e-9, f"names={r['names']:.4f}")
check("no name was invented here", r["invented"] == 0)

r = eval_stt.score(["the meeting went well"], ["Sibyl the meeting went well"])
check("a name nobody said is counted as invented", r["invented"] == 1,
      f"invented={r['invented']}")

r = eval_stt.score(["Sibyl and Sibyl"], ["Sibyl and Qwen"])
check("names are counted per reading, not once per name",
      abs(r["names"] - 0.5) < 1e-9 and r["invented"] == 1,
      f"names={r['names']:.4f} invented={r['invented']}")


# -- the four winning rules -------------------------------------------------------

print("\nThe four rules that decide a winner")

BASE = {"wer": 0.10, "names": 0.80, "invented": 0, "speech": 1.0, "peak_mb": 400}


def contender(**changes):
    # Clearly better on every rule, until one value is changed.
    it = {"wer": 0.07, "names": 0.90, "invented": 0, "speech": 1.2, "peak_mb": 500}
    it.update(changes)
    return it


check("today's model is never judged against itself",
      eval_stt.verdict("base.en", BASE, BASE) == "today's model")

check("clearly fewer word errors, in time and in memory, wins",
      eval_stt.verdict("small.en", contender(), BASE) == "WINS",
      eval_stt.verdict("small.en", contender(), BASE))

check("more names right with word errors no worse also wins",
      eval_stt.verdict("small.en", contender(wer=0.10, names=0.90), BASE) == "WINS",
      eval_stt.verdict("small.en", contender(wer=0.10, names=0.90), BASE))

check("a small gain is refused on its own",
      eval_stt.verdict("small.en", contender(wer=0.09, names=0.80), BASE) ==
      "no: does not hear clearly better",
      eval_stt.verdict("small.en", contender(wer=0.09, names=0.80), BASE))

check("one invented name is refused on its own",
      eval_stt.verdict("small.en", contender(invented=1), BASE) == "no: invents names",
      eval_stt.verdict("small.en", contender(invented=1), BASE))

check("more than half a second slower is refused on its own",
      eval_stt.verdict("small.en", contender(speech=1.6), BASE) == "no: +0.6 s slower",
      eval_stt.verdict("small.en", contender(speech=1.6), BASE))

check("half a second slower is still allowed",
      eval_stt.verdict("small.en", contender(speech=1.5), BASE) == "WINS",
      eval_stt.verdict("small.en", contender(speech=1.5), BASE))

check("too much memory is refused on its own",
      eval_stt.verdict("small.en", contender(peak_mb=1200), BASE) == "no: too much memory",
      eval_stt.verdict("small.en", contender(peak_mb=1200), BASE))

check("every reason it failed is named, not just the first",
      eval_stt.verdict("small.en", contender(wer=0.09, names=0.80, invented=2,
                                             speech=2.0, peak_mb=1200), BASE) ==
      "no: does not hear clearly better, invents names, +1.0 s slower, too much memory",
      eval_stt.verdict("small.en", contender(wer=0.09, names=0.80, invented=2,
                                             speech=2.0, peak_mb=1200), BASE))


# -- the texts to read aloud -------------------------------------------------------

print("\nThe twelve texts")

check("there are twelve texts to read", len(eval_stt.TEXTS) == 12,
      f"{len(eval_stt.TEXTS)} texts")

check("every hint name is read at least once",
      all(any(eval_stt.occurrences(eval_stt.words(t), eval_stt.words(text))
              for text in eval_stt.TEXTS) for t in eval_stt.TARGETS),
      [t for t in eval_stt.TARGETS
       if not any(eval_stt.occurrences(eval_stt.words(t), eval_stt.words(text))
                  for text in eval_stt.TEXTS)])

readings = sum(eval_stt.occurrences(eval_stt.words(t), eval_stt.words(text))
               for t in eval_stt.TARGETS for text in eval_stt.TEXTS)
check("the names are read fifteen times in all", readings == 15, f"{readings} readings")

check("no text contains a digit, which would measure formatting not hearing",
      not any(c.isdigit() for text in eval_stt.TEXTS for c in text))

check("the recordings are kept outside the project folder",
      pathlib.Path(__file__).resolve().parent not in eval_stt.RECORDINGS.resolve().parents,
      str(eval_stt.RECORDINGS))



# -- a clean-up that fails is not a measurement -------------------------------------

print("\nThe clean-up timing, when Ollama fails partway")


class FakeRun:
    """What subprocess.run gives back: one line of JSON from a contender."""

    returncode = 0

    def __init__(self, stdout):
        self.stdout = stdout
        self.stderr = ""


def time_the_cleanup(failing_texts):
    """Run score_all with every slow part faked, and hand back what it printed.

    The speech models are faked to take no time at all, so the "Total s" column
    is the clean-up's seconds and nothing else. Each clean-up takes 50 ms,
    except the ones named in failing_texts, which fail at once.
    """
    folder = pathlib.Path(tempfile.mkdtemp(prefix="sibyl_test_"))
    for n, text in enumerate(eval_stt.TEXTS, 1):
        (folder / f"{n:02d}.wav").write_bytes(b"")
        (folder / f"{n:02d}.txt").write_text(text, encoding="utf-8")

    heard = json.dumps({"texts": list(eval_stt.TEXTS),
                        "seconds": [0.0] * len(eval_stt.TEXTS),
                        "kept": [1.0] * len(eval_stt.TEXTS), "peak_mb": 100})

    calls = []

    def fake_clean(text, on_token=None):
        calls.append(text)
        if text in failing_texts:
            raise sibyl.OllamaError("the model said " + text)
        time.sleep(0.05)
        return text

    was = (eval_stt.RECORDINGS, eval_stt.subprocess.run, sibyl.ollama_problem,
           sibyl.warm_up, sibyl.clean_transcript)
    eval_stt.RECORDINGS = folder
    eval_stt.subprocess.run = lambda *a, **k: FakeRun(heard)
    sibyl.ollama_problem = lambda model: ""
    sibyl.warm_up = lambda model: None
    sibyl.clean_transcript = fake_clean
    printed = io.StringIO()
    try:
        with contextlib.redirect_stdout(printed):
            eval_stt.score_all(runs=1)
    finally:
        (eval_stt.RECORDINGS, eval_stt.subprocess.run, sibyl.ollama_problem,
         sibyl.warm_up, sibyl.clean_transcript) = was
        shutil.rmtree(folder, ignore_errors=True)
    return printed.getvalue(), calls


def total_seconds(output):
    """The Total s cell of the base.en row of the printed table."""
    row = next(line for line in output.splitlines() if line.startswith("base.en "))
    return float(row.split()[-4])


output, calls = time_the_cleanup(failing_texts=())
check("with nothing failing, each clean-up's 50 ms is the average",
      abs(total_seconds(output) - 0.05) < 0.02, f"{total_seconds(output):.3f} s")
check("the clean-up is primed before it is timed, as Sibyl primes it at start",
      calls[0] == "okay" and len(calls) == len(eval_stt.TEXTS) + 1, f"{len(calls)} calls")

# Eight of the twelve fail. Enough that dividing by twelve rather than by the
# four that worked gives 0.017 s, well outside the tolerance below: the old
# version of this loop, which added a failure's seconds in and said nothing,
# fails this check rather than scraping through it.
output, calls = time_the_cleanup(failing_texts=tuple(eval_stt.TEXTS[:8]))
check("a failed clean-up is left out of the average, not counted as instant",
      abs(total_seconds(output) - 0.05) < 0.02, f"{total_seconds(output):.3f} s")
check("the failures are said out loud, never swallowed",
      "8 of 12 clean-ups failed" in output,
      [line for line in output.splitlines() if "failed" in line])

output, _ = time_the_cleanup(failing_texts=tuple(eval_stt.TEXTS))
check("if every clean-up fails, Total s is the speech model alone",
      total_seconds(output) == 0.0 and "Every clean-up failed" in output,
      f"{total_seconds(output):.3f} s")



# -- a recording made for different words is refused, not scored --------------------
# Found by review on 2026-10-03, when a rename changed the product's name in three
# texts while their recordings still said the old one. Scored anyway, a changed
# text looks exactly like the model mishearing a name. So each recording keeps the
# words it was read from beside it, and scoring refuses any that differ or are
# missing, naming the command that fixes each one.

print("\nA recording made for different words is refused")


def recordings_folder(texts_kept):
    """Twelve recordings, each with the words it was read from, as given."""
    folder = pathlib.Path(tempfile.mkdtemp(prefix="sibyl_test_"))
    for n in range(1, len(eval_stt.TEXTS) + 1):
        (folder / f"{n:02d}.wav").write_bytes(b"")
        kept = texts_kept.get(n, eval_stt.TEXTS[n - 1])
        if kept is not None:
            (folder / f"{n:02d}.txt").write_text(kept, encoding="utf-8")
    return folder


folder = recordings_folder({})
check("recordings read from today's texts are all current",
      eval_stt.stale_recordings(folder) == [], eval_stt.stale_recordings(folder))
shutil.rmtree(folder, ignore_errors=True)

folder = recordings_folder({1: "An older wording of the first text.", 8: None})
check("one read from old words, and one with no words kept, are both named",
      eval_stt.stale_recordings(folder) == [1, 8], eval_stt.stale_recordings(folder))

listened = []
was = (eval_stt.RECORDINGS, eval_stt.subprocess.run)
eval_stt.RECORDINGS = folder
eval_stt.subprocess.run = lambda *a, **k: listened.append(a)
try:
    eval_stt.score_all(runs=1)
    refusal = ""
except SystemExit as exc:
    refusal = str(exc)
finally:
    eval_stt.RECORDINGS, eval_stt.subprocess.run = was
    shutil.rmtree(folder, ignore_errors=True)
check("scoring refuses them before any model listens",
      refusal != "" and listened == [], refusal or f"{len(listened)} contenders ran")
check("and says which to record again, by the command that does it",
      "--record 1" in refusal and "--record 8" in refusal, refusal)


class FakeRecorder:
    """The microphone, replaced: each stop hands back a fresh empty .wav."""

    last_duration = 4.0

    def start(self):
        pass

    def stop(self):
        fd, name = tempfile.mkstemp(suffix=".wav")
        os.close(fd)
        return pathlib.Path(name)


folder = pathlib.Path(tempfile.mkdtemp(prefix="sibyl_test_"))
was = (eval_stt.RECORDINGS, eval_stt.sibyl.Recorder)
eval_stt.RECORDINGS = folder
eval_stt.sibyl.Recorder = FakeRecorder
eval_stt.input = lambda prompt="": ""   # every Enter pressed: keep, and move on
try:
    with contextlib.redirect_stdout(io.StringIO()):
        eval_stt.record(3)
    kept = (folder / "03.txt").read_text(encoding="utf-8") if (folder / "03.txt").exists() else None
finally:
    eval_stt.RECORDINGS, eval_stt.sibyl.Recorder = was[0], was[1]
    del eval_stt.input
    shutil.rmtree(folder, ignore_errors=True)
check("recording a text keeps the words it was read from beside it",
      kept == eval_stt.TEXTS[2], repr(kept))



# -- the hint sibyl.py actually uses -------------------------------------------------

print("\nThe hint, as the recogniser really sends it")


class FakeWhisper:
    """Stands in for faster-whisper's model, and remembers how it was called."""

    def __init__(self):
        self.sent = None

    def transcribe(self, path, **settings):
        self.sent = settings
        return [], None


def settings_sent(hotwords):
    """What WhisperBackend.transcribe hands the model, with this hint set."""
    was = sibyl.STT_HOTWORDS
    sibyl.STT_HOTWORDS = hotwords
    backend = sibyl.WhisperBackend()
    fake = FakeWhisper()
    backend._model = fake
    backend._ready.set()
    try:
        backend.transcribe(pathlib.Path("nothing.wav"))
    finally:
        sibyl.STT_HOTWORDS = was
    return fake.sent


sent = settings_sent("Sibyl, Qwen.")
check("the hint is sent as hotwords, before every thirty seconds of audio",
      sent.get("hotwords") == "Sibyl, Qwen.", sent)

check("the settings measured on 2026-09-20 are the settings sent",
      sent.get("beam_size") == 1 and sent.get("vad_filter") is True
      and sent.get("language") == "en", sent)

check("no hint means no hint, not an empty one",
      settings_sent("").get("hotwords") is None, settings_sent(""))

check("every name measured by this harness is in the shipped hint",
      all(t.lower() in sibyl.STT_HOTWORDS.lower() for t in eval_stt.TARGETS),
      [t for t in eval_stt.TARGETS if t.lower() not in sibyl.STT_HOTWORDS.lower()])


passed = sum(results)
print(f"\n{passed}/{len(results)} passed")
sys.exit(0 if passed == len(results) else 1)
