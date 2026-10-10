"""Roadmap step 7: which speech setting hears you best, how fast, in how much memory.

Sibyl hears with faster-whisper's base.en. This compares it with three
contenders on the same twelve recordings of your voice:

    base.en                    today's model
    small.en                   3.3 times the size: may hear better, costs time
    base.en + initial_prompt   today's model, told the names to expect
    base.en + hotwords         the same hint, given again for every 30 seconds

Record once, then score as often as you like:

    python eval_stt.py --download     fetch small.en once, about 466 MB
    python eval_stt.py --record       read the twelve texts aloud, about 10 minutes
    python eval_stt.py --record 7     read text 7 again
    python eval_stt.py --score        the comparison table

The recordings are your voice, so they live in %LOCALAPPDATA%\\Sibyl\\stt-eval,
never in this folder and never in git. The texts are invented.
"""
from __future__ import annotations

import argparse
import ctypes
import ctypes.wintypes as wt
import json
import os
import re
import subprocess
import sys
import time
import unicodedata

import sibyl

RECORDINGS = sibyl.LOG_FILE.parent / "stt-eval"

# Names Whisper is likely to mishear, and the hint that lists them.
TARGETS = ["Sibyl", "Qwen", "Ollama", "Champs-Élysées", "Siobhan", "Joaquin"]
HINTS = ", ".join(TARGETS) + "."

CONTENDERS = {
    "base.en": {"model": "base.en"},
    "small.en": {"model": "small.en"},
    "base.en + initial_prompt": {"model": "base.en", "initial_prompt": HINTS},
    "base.en + hotwords": {"model": "base.en", "hotwords": HINTS},
}

# Read aloud exactly as written; words in square brackets are directions.
# Invented, in the shape of real dictation. No numbers: Whisper writes
# "fifteen" as "15", and scoring that would measure formatting, not hearing.
TEXTS = [
    "Sibyl should paste this into the notes app, and then I will check every word.",
    "I think we should probably move the review to next week, because Siobhan is travelling.",
    "Can you ask Joaquin whether the Qwen model runs any faster on the new laptop?",
    "Before I forget, the Ollama server needs a restart after the update finishes.",
    "We walked along the Champs-Élysées in the evening and stopped for coffee near the arch.",
    "Maybe we could try the smaller model first, and keep the bigger one as a backup.",
    "Please send Siobhan the slides from this morning, and copy Joaquin as well.",
    "Sibyl keeps mishearing my name, so I want to measure it properly this time.",
    "The client wants a shorter report, with the risks at the top and the budget at the end.",
    "I tested Qwen through Ollama on battery power, and it was slower than on the charger.",
    "So here is the plan for the trip. We land in Paris in the evening and walk down the "
    "Champs-Élysées to stretch our legs. The next morning Siobhan wants to visit the museum, "
    "and I will probably spend a few hours working from the hotel, because the report is "
    "still not finished. After lunch we could take the train south, although Joaquin thinks "
    "the drive would be nicer if the weather holds. Either way, I want Sibyl to write this "
    "down exactly as I say it, with every maybe and probably left in.",
    "I I I think the meeting went well [cough once] [stay silent for three seconds] and the "
    "notes are ready to send.",
]

# A contender wins only if all of these hold. Agreed 2026-09-19.
MIN_FEWER_ERRORS = 0.25   # word errors at least 25% below base.en's, or more names
                          # right with word errors no worse than base.en's
MAX_EXTRA_SECONDS = 0.5   # no slower than today
MAX_PEAK_MB = 1000        # a busy day had only 1.98 GB free (9 Sep)

# A direction to the reader, not a word: stripped before the text is printed
# back. Kept out of the f-string below, which a backslash would break on any
# Python before 3.12.
_DIRECTION = re.compile(r"\s*\[[^\]]*\]")


# -- scoring -------------------------------------------------------------------

def words(text: str) -> list[str]:
    """Lower case; accents, punctuation and [directions] gone; hyphens as spaces.
    So "Champs-Élysées," and "champs elysees" are the same two words."""
    text = re.sub(r"\[[^\]]*\]", " ", text).replace("’", "'")
    text = unicodedata.normalize("NFKD", text)
    text = "".join(c for c in text if not unicodedata.combining(c))
    found = re.findall(r"[a-z0-9']+", text.lower().replace("-", " "))
    return [w.strip("'") for w in found if w.strip("'")]


def edits(said: list[str], heard: list[str]) -> int:
    """Wrong, missing and extra words: the fewest one-word changes that turn one
    list into the other (Levenshtein distance, counted in words)."""
    row = list(range(len(heard) + 1))
    for i, s in enumerate(said, 1):
        diagonal, row[0] = row[0], i
        for j, h in enumerate(heard, 1):
            diagonal, row[j] = row[j], min(row[j] + 1, row[j - 1] + 1, diagonal + (s != h))
    return row[-1]


def occurrences(phrase: list[str], text: list[str]) -> int:
    n = len(phrase)
    return sum(text[i:i + n] == phrase for i in range(len(text) - n + 1))


def score(said: list[str], heard: list[str]) -> dict:
    """Word error rate over all texts, the share of names right, names invented."""
    said_words = [words(t) for t in said]
    heard_words = [words(t) for t in heard]
    errors = sum(edits(s, h) for s, h in zip(said_words, heard_words))
    named = right = invented = 0
    for target in TARGETS:
        phrase = words(target)
        for s, h in zip(said_words, heard_words):
            in_said, in_heard = occurrences(phrase, s), occurrences(phrase, h)
            named += in_said
            right += min(in_said, in_heard)
            invented += max(0, in_heard - in_said)
    return {"wer": errors / sum(len(s) for s in said_words),
            "names": right / max(named, 1), "invented": invented}


def verdict(name: str, r: dict, base: dict) -> str:
    if name == "base.en":
        return "today's model"
    misses = []
    if not (r["wer"] <= base["wer"] * (1 - MIN_FEWER_ERRORS)
            or (r["names"] > base["names"] and r["wer"] <= base["wer"])):
        misses.append("does not hear clearly better")
    if r["invented"]:
        misses.append("invents names")
    if r["speech"] > base["speech"] + MAX_EXTRA_SECONDS:
        misses.append(f"{r['speech'] - base['speech']:+.1f} s slower")
    if r["peak_mb"] > MAX_PEAK_MB:
        misses.append("too much memory")
    return "WINS" if not misses else "no: " + ", ".join(misses)


# -- memory (Windows) ------------------------------------------------------------

class _Counters(ctypes.Structure):
    _fields_ = [("cb", wt.DWORD), ("PageFaultCount", wt.DWORD),
                ("PeakWorkingSetSize", ctypes.c_size_t), ("WorkingSetSize", ctypes.c_size_t),
                ("QuotaPeakPagedPoolUsage", ctypes.c_size_t),
                ("QuotaPagedPoolUsage", ctypes.c_size_t),
                ("QuotaPeakNonPagedPoolUsage", ctypes.c_size_t),
                ("QuotaNonPagedPoolUsage", ctypes.c_size_t),
                ("PagefileUsage", ctypes.c_size_t), ("PeakPagefileUsage", ctypes.c_size_t)]


class _Status(ctypes.Structure):
    _fields_ = [("dwLength", wt.DWORD), ("dwMemoryLoad", wt.DWORD),
                ("ullTotalPhys", ctypes.c_uint64), ("ullAvailPhys", ctypes.c_uint64),
                ("ullTotalPageFile", ctypes.c_uint64), ("ullAvailPageFile", ctypes.c_uint64),
                ("ullTotalVirtual", ctypes.c_uint64), ("ullAvailVirtual", ctypes.c_uint64),
                ("ullAvailExtendedVirtual", ctypes.c_uint64)]


def peak_mb() -> int:
    """The most physical memory this process has held. The argument types must
    be declared: undeclared, the call returned zeros (2026-09-19)."""
    kernel32 = ctypes.WinDLL("kernel32")
    kernel32.GetCurrentProcess.restype = wt.HANDLE
    kernel32.K32GetProcessMemoryInfo.argtypes = [wt.HANDLE, ctypes.POINTER(_Counters),
                                                 wt.DWORD]
    counters = _Counters(cb=ctypes.sizeof(_Counters))
    kernel32.K32GetProcessMemoryInfo(kernel32.GetCurrentProcess(), ctypes.byref(counters),
                                     counters.cb)
    return counters.PeakWorkingSetSize // 2**20


def free_mb() -> int:
    """Physical memory free now: Task Manager's "Available"."""
    status = _Status(dwLength=ctypes.sizeof(_Status))
    ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(status))
    return status.ullAvailPhys // 2**20


# -- the three commands ----------------------------------------------------------

def download() -> None:
    # The default download transport is reset on this network; see WhisperBackend.load.
    os.environ.setdefault("HF_HUB_DISABLE_XET", "1")
    from faster_whisper import WhisperModel

    for model in sorted({c["model"] for c in CONTENDERS.values()}):
        print(f"{model}: fetching, if it is not here already ...", flush=True)
        WhisperModel(model, device=sibyl.STT_DEVICE, compute_type=sibyl.STT_COMPUTE)
    print("Done.")


def record(only: int | None) -> None:
    RECORDINGS.mkdir(parents=True, exist_ok=True)
    recorder = sibyl.Recorder()   # the same microphone path as real use
    print(f"Recordings are kept in {RECORDINGS}\n")
    for n, text in enumerate(TEXTS, 1):
        if only and n != only:
            continue
        while True:
            print(f"Text {n} of {len(TEXTS)}:\n\n    {text}\n")
            input("  Press Enter, then read it aloud. ")
            recorder.start()
            input("  Listening. Press Enter when you have finished. ")
            wav = recorder.stop()
            if wav is None:
                print("  Nothing was recorded. Once more.\n")
                continue
            wav.replace(RECORDINGS / f"{n:02d}.wav")
            # The words it was read from, kept beside it, so a text changed later
            # is caught by stale_recordings rather than scored as a mishearing.
            (RECORDINGS / f"{n:02d}.txt").write_text(text, encoding="utf-8")
            again = input(f"  Kept, {recorder.last_duration:.0f} s. Enter for the next "
                          "text, or r and Enter to read it again: ")
            if again.strip().lower() != "r":
                print()
                break


def listen(name: str, runs: int) -> None:
    """One contender, in its own process so its peak memory is its own. Prints
    one line of JSON for --score to read."""
    from faster_whisper import WhisperModel

    settings = dict(CONTENDERS[name])
    model = WhisperModel(settings.pop("model"), device=sibyl.STT_DEVICE,
                         compute_type=sibyl.STT_COMPUTE)
    wavs = sorted(RECORDINGS.glob("*.wav"))

    def hear(wav):
        # WhisperBackend.transcribe's settings, plus the hint. Keep in step.
        segments, info = model.transcribe(str(wav), beam_size=1, vad_filter=True,
                                          language="en", **settings)
        return " ".join(s.text.strip() for s in segments).strip(), info

    hear(wavs[0])   # untimed: the first call pays one-off costs, as at Sibyl's start
    texts, kept, seconds = [], [], [0.0] * len(wavs)
    for run in range(runs):
        for i, wav in enumerate(wavs):
            started = time.perf_counter()
            text, info = hear(wav)
            seconds[i] += (time.perf_counter() - started) / runs
            if run == 0:
                texts.append(text)
                kept.append(info.duration_after_vad / info.duration)
    print(json.dumps({"texts": texts, "seconds": seconds, "kept": kept,
                      "peak_mb": peak_mb()}))


def stale_recordings(folder) -> list:
    """The numbers of the texts whose recording was read from different words
    than the text says now, or whose words were never kept beside it. Scored,
    such a recording makes a changed text look like the model mishearing it
    (review, 2026-10-03: the rename changed three texts, not their recordings)."""
    stale = []
    for n, text in enumerate(TEXTS, 1):
        try:
            read = (folder / f"{n:02d}.txt").read_text(encoding="utf-8")
        except OSError:
            read = None
        if read != text:
            stale.append(n)
    return stale


def score_all(runs: int) -> None:
    wavs = sorted(RECORDINGS.glob("*.wav"))
    if len(wavs) != len(TEXTS):
        sys.exit(f"Found {len(wavs)} of {len(TEXTS)} recordings in {RECORDINGS}.\n"
                 "Record them first:  python eval_stt.py --record")
    stale = stale_recordings(RECORDINGS)
    if stale:
        sys.exit("These recordings were read from different words than their texts say "
                 "now, so scoring them would count the change as a mishearing. Record "
                 "them again:\n" + "\n".join(f"  python eval_stt.py --record {n}"
                                             for n in stale))

    results = {}
    for name in CONTENDERS:
        print(f"Listening with {name} ...", flush=True)
        done = subprocess.run(
            [sys.executable, __file__, "--listen", name, "--runs", str(runs)],
            capture_output=True, text=True, encoding="utf-8",
            env=dict(os.environ, HF_HUB_OFFLINE="1"),   # measuring never uses the network
        )
        if done.returncode != 0:
            sys.exit(f"{name} failed:\n{done.stderr[-1500:]}")
        r = json.loads(done.stdout.strip().splitlines()[-1])
        r["speech"] = sum(r["seconds"]) / len(wavs)
        r.update(score(TEXTS, r["texts"]))
        results[name] = r

    # The clean-up's seconds per recording, measured once on base.en's words:
    # its time follows the length of what it is given, near enough the same for
    # every contender. Primed first, as Sibyl primes it at start.
    cleanup = 0.0
    problem = sibyl.ollama_problem(sibyl.CLEANUP_MODEL)
    if problem:
        print(f"{problem}: Total s is the speech model alone.")
    else:
        print("Timing the clean-up ...", flush=True)
        sibyl.warm_up(sibyl.CLEANUP_MODEL)
        sibyl.clean_transcript("okay")
        # A failed call is not a measurement. Its seconds are the seconds up to
        # the failure, so averaging them in would quietly move the one number
        # this file exists to produce. Failures are counted and named instead.
        timed = failed = 0
        for text in results["base.en"]["texts"]:
            started = time.perf_counter()
            try:
                sibyl.clean_transcript(text)
            except sibyl.OllamaError as exc:
                failed += 1
                print(f"  A clean-up failed, so it is not counted: "
                      f"{sibyl.private(str(exc), '(hidden)')}")
                continue
            cleanup += time.perf_counter() - started
            timed += 1
        cleanup = cleanup / timed if timed else 0.0
        if failed and timed:
            print(f"  {failed} of {len(wavs)} clean-ups failed. "
                  "Total s is the average of the rest.")
        elif failed:
            print("  Every clean-up failed. Total s is the speech model alone.")

    base = results["base.en"]
    print(f"\nFree memory now, with the clean-up model loaded: {free_mb():,} MB")
    print(f"Silence removed before listening: "
          f"{1 - sum(base['kept']) / len(base['kept']):.0%} of the audio, on average\n")
    header = (f"{'Contender':26} {'WER':>6} {'Names':>6} {'Invented':>8} "
              f"{'Speech s':>8} {'Total s':>7} {'Peak MB':>7}  Verdict")
    print(header)
    print("-" * len(header))
    for name, r in results.items():
        print(f"{name:26} {r['wer']:6.1%} {r['names']:6.0%} {r['invented']:8} "
              f"{r['speech']:8.2f} {r['speech'] + cleanup:7.2f} {r['peak_mb']:7,}  "
              f"{verdict(name, r, base)}")

    print("\nWhat each contender wrote, wherever one got a word wrong:")
    for i, said in enumerate(TEXTS):
        heard = {name: r["texts"][i] for name, r in results.items()}
        if all(edits(words(said), words(h)) == 0 for h in heard.values()):
            continue
        read_aloud = _DIRECTION.sub("", said)
        print(f"\n  Text {i + 1}, read:  {read_aloud}")
        for name, h in heard.items():
            print(f"    {name:26} {h}")
    (RECORDINGS / "results.json").write_text(json.dumps(results, indent=1),
                                             encoding="utf-8")


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Roadmap step 7: compare speech models on your own recordings.")
    parser.add_argument("--download", action="store_true",
                        help="fetch every contender's model once")
    parser.add_argument("--record", nargs="?", type=int, const=0, metavar="N",
                        help="read the texts aloud; with N, only text N")
    parser.add_argument("--score", action="store_true", help="print the comparison table")
    parser.add_argument("--runs", type=int, default=3, help="timing runs per recording")
    parser.add_argument("--listen", metavar="CONTENDER", help=argparse.SUPPRESS)
    args = parser.parse_args()
    if args.download:
        download()
    elif args.record is not None:
        record(args.record or None)
    elif args.score:
        score_all(args.runs)
    elif args.listen:
        listen(args.listen, args.runs)
    else:
        parser.print_help()


if __name__ == "__main__":
    main()
