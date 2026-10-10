"""Launching like an app: no console, a log file, and problems shown in red.

Roadmap step 6. Started from the desktop icon, Sibyl runs under pythonw.exe,
which has no console, so what used to print goes to a log file instead. This
file proves:

  * under pythonw.exe, what is printed lands in the log, with the time;
  * a big log is moved aside at start, so it cannot grow for ever;
  * after a dictation, the log holds none of its words, none of the words the
    check objected to, and not the title of the window it went to;
  * with a console, those lines still show in full, for debugging;
  * Ollama not answering at start is waited for, then shown red, and stays red;
  * Ollama answering late, within the wait, ends ready as normal.

No microphone, no models, no keyboard: the recorder, the recogniser, the model
and the paste are fakes. The log goes to a temporary folder, never the real one.

Run:  python test_launch.py
"""
import os
import pathlib
import re
import subprocess
import sys
import tempfile
import threading
import warnings
import logging as python_logging   # the name `logging` is a loop's below

HERE = pathlib.Path(__file__).resolve().parents[0]
sys.path.insert(0, str(HERE))
import sibyl

results = []


def check(name, ok, detail=""):
    print(f"  {'PASS' if ok else 'FAIL'}  {name}" + (f"\n        {detail}" if detail and not ok else ""))
    results.append(ok)


real_sleep = sibyl.time.sleep
real_problem = sibyl.ollama_problem   # the boot tests below stand in for it
real_chat = sibyl.ollama_chat         # and the log tests for this
real_warm = sibyl.warm_up
real_showwarning = warnings.showwarning


def python_defaults():
    """open_log shortens Python's own reports, for the log; this file wants
    them whole, and the next open_log adds its own once more."""
    sys.excepthook, threading.excepthook = sys.__excepthook__, threading.__excepthook__
    sys.unraisablehook = sys.__unraisablehook__
    warnings.showwarning = real_showwarning
    python_logging.getLogger().handlers.clear()
# Noted before this file makes anything there, so the last check can tell what
# this run left behind: 220 such folders had piled up by 2026-10-04.
temp_before = set(pathlib.Path(tempfile.gettempdir()).glob("sibyl_test_*"))
tmp = pathlib.Path(tempfile.mkdtemp(prefix="sibyl_test_"))


# -- pythonw.exe, for real -------------------------------------------------------

print("Under pythonw.exe")
pythonw = pathlib.Path(sys.executable).with_name("pythonw.exe")
env = dict(os.environ, LOCALAPPDATA=str(tmp / "appdata"))
done = subprocess.run([str(pythonw), str(HERE / "sibyl.py"), "--test-ollama"],
                      env=env, cwd=str(HERE), timeout=60)
log = tmp / "appdata" / "Sibyl" / "sibyl.log"
text = log.read_text(encoding="utf-8") if log.exists() else ""
# Without this, a run that printed its first line and then crashed would pass
# every check below, because the traceback lands in the same file.
check("the run under pythonw finished cleanly", done.returncode == 0,
      f"exit code {done.returncode}")
check("what is printed goes to the log in %LOCALAPPDATA%\\Sibyl", "Probing" in text, text[:200])
check("each line starts with the date and time",
      text[:4].isdigit() and text[4] == "-" and text[13] == ":", text[:40])


# -- the log, in this process ----------------------------------------------------

print("\nThe log file")
sibyl.LOG_FILE = tmp / "Sibyl" / "sibyl.log"
sibyl.LOG_FILE.parent.mkdir(parents=True)
sibyl.LOG_FILE.write_text("x" * (sibyl.LOG_MAX_BYTES + 1), encoding="utf-8")
real_out, real_err = sys.stdout, sys.stderr
sibyl.open_log()
print("[idle] ready")
sys.stderr.write("an error, as Python reports one\n")
sys.stdout, sys.stderr = real_out, real_err
old = sibyl.LOG_FILE.with_name("sibyl.old.log")
check("a log over the size limit is moved to sibyl.old.log",
      old.exists() and old.stat().st_size > sibyl.LOG_MAX_BYTES)
text = sibyl.LOG_FILE.read_text(encoding="utf-8")
check("and a fresh log starts", "[idle] ready" in text and "x" not in text, text)
check("errors go to the log too", "an error, as Python reports one" in text, text)

# Two threads printing at once. print() sends its text and its newline in two
# separate writes, so a log that locks per write merges lines: 98 of 6,000 on
# 2026-09-19, found in review.
busy = tmp / "Sibyl" / "busy.log"
sys.stdout = sibyl._Stamped(open(busy, "w", encoding="utf-8"))


def spam(tag):
    for i in range(2000):
        print(f"[{tag}] line {i} end")


threads = [threading.Thread(target=spam, args=(tag,)) for tag in "AB"]
for thread in threads:
    thread.start()
for thread in threads:
    thread.join()
sys.stdout.flush()
sys.stdout = real_out
lines = busy.read_text(encoding="utf-8").splitlines()
whole = [line for line in lines
         if line[:4].isdigit() and line.count("[") == 1 and line.endswith("end")]
check("two threads printing at once never share a line",
      len(lines) == 4000 and len(whole) == 4000,
      f"{len(lines)} lines, {len(whole)} of them whole")

check("the log can give its file number, as libraries expect of a stream",
      isinstance(sibyl._Stamped(open(busy, "a", encoding="utf-8")).fileno(), int))

# Two copies starting together both try to move a full log aside; the loser
# used to die before it could say anything.
sibyl.LOG_FILE.write_text("x" * (sibyl.LOG_MAX_BYTES + 1), encoding="utf-8")
real_replace = pathlib.Path.replace
pathlib.Path.replace = lambda *a, **k: (_ for _ in ()).throw(FileNotFoundError("gone"))
try:
    sibyl.open_log()
    sys.stdout, sys.stderr = real_out, real_err
    check("a log another copy has already moved aside still opens", True)
except Exception as exc:
    sys.stdout, sys.stderr = real_out, real_err
    check("a log another copy has already moved aside still opens", False,
          f"{type(exc).__name__}: {exc}")
finally:
    pathlib.Path.replace = real_replace
python_defaults()


# -- a dictation, with the log on ------------------------------------------------

SAID = "um so zanzibar quokka meeting is on thursday"      # "zanzibar", "quokka"
ANSWER = "The zanzibar quokka flamingo meeting is on Thursday."   # "flamingo" added
TITLE = "Invoice for Marlowe Street - Mail"


class Wav:
    def unlink(self, missing_ok=False):
        pass


class FakeRecorder:
    is_recording = False
    last_duration = 3.0

    def stop(self):
        return Wav()


sibyl.beep = lambda *_a, **_k: None
sibyl.transcribe = lambda wav: SAID
sibyl.ollama_chat = lambda *_a, **_k: ANSWER
sibyl.inject = lambda text, hwnd=None: True
sibyl.window_title = lambda hwnd: TITLE


def dictate():
    """One capture through the real _finish_capture; returns what it printed."""
    out = tmp / "capture.log"
    out.unlink(missing_ok=True)
    stream = open(out, "w", encoding="utf-8")
    sys.stdout = stream
    try:
        app = sibyl.Sibyl()
        app.recorder = FakeRecorder()
        app._finish_capture()
    finally:
        sys.stdout = real_out
        stream.close()
    return out.read_text(encoding="utf-8")


print("\nA dictation")
sibyl._logging = True
text = dictate()
check("the log records the capture and its timings",
      "heard (" in text and "cleaned (" in text and "[done]" in text, text)
check("the check's objection is logged, as a reason", "added" in text, text)
for word in ["zanzibar", "quokka", "thursday", "flamingo"]:
    check(f"the log never holds {word!r}", word not in text.lower())
check("nor the title of the window it went to", "Marlowe" not in text, text)

sibyl._logging = False
text = dictate()
check("in a console, the words still show, for debugging",
      "zanzibar quokka" in text and "flamingo" in text and "Marlowe" in text, text)


# -- Ollama at start -------------------------------------------------------------

class FakeSTT:
    load_error = None
    download_failed = False

    def load(self, on_download=None):
        pass


def boot(answers):
    """Run _boot with Ollama giving these answers in turn; return the states."""
    states = []
    sibyl.get_stt = lambda: FakeSTT()
    sibyl.warm_up = lambda model: states.append(("warmed", model, "", False))
    sibyl.clean_transcript = lambda text, on_token=None: states.append(
        ("practised", text, "", False)) or text
    sibyl.time.sleep = lambda s: real_sleep(0.05)   # 2 s in the app, shortened
    queue = list(answers)
    sibyl.ollama_problem = lambda model: queue.pop(0) if len(queue) > 1 else queue[0]
    app = sibyl.Sibyl()
    app._set_state = lambda state, note="", short="", stay=False: states.append(
        (state, note, short, stay))
    app._boot()
    return states


print("\nOllama at start")
sibyl.OLLAMA_WAIT_AT_START = 0.2
states = boot(["Ollama not running"])
check("while waiting, the notch says so",
      any(s[2] == "Waiting for Ollama" for s in states), str(states))
last = states[-1]
check("after the wait, it is red and says what is wrong",
      last[0] == "failed" and last[2] == "Ollama not running", str(last))
check("and it stays red, rather than fading back to grey", last[3] is True, str(last))
check("the model is not warmed when there is no Ollama",
      not any(s[0] == "warmed" for s in states), str(states))

sibyl.OLLAMA_WAIT_AT_START = 60
states = boot(["Ollama not running", "Ollama not running", ""])
check("Ollama answering late, within the wait, ends ready",
      states[-1][:2] == ("idle", "ready"), str(states[-1]))
check("and the model is warmed", any(s[0] == "warmed" for s in states), str(states))
check("and does one practice clean-up before ready, so the first dictation is quick",
      [s[0] for s in states][-3:-1] == ["practised", "loading"]
      and states[-1][0] == "idle", str(states))

states = boot(["Model not installed"])
check("a missing model is shown at once, without waiting",
      states[-1][2] == "Model not installed"
      and not any(s[2] == "Waiting for Ollama" for s in states), str(states))

# Answering is not the same as working. Until this was found in review, a
# practice clean-up that failed left the dot grey and saying "ready".
def explode(text, on_token=None):
    raise sibyl.OllamaError("model requires more system memory")


states = []
sibyl.get_stt = lambda: FakeSTT()
sibyl.warm_up = lambda model: None
sibyl.ollama_problem = lambda model: ""
sibyl.clean_transcript = explode
app = sibyl.Sibyl()
app._set_state = lambda state, note="", short="", stay=False: states.append(
    (state, note, short, stay))
app._boot()
check("a practice clean-up that fails ends red, not ready",
      states[-1][0] == "failed" and states[-1][2] == "Clean-up failed"
      and states[-1][3] is True, str(states[-1]))

# A half-downloaded base.en left the recogniser dead while the notch said
# "ready", and only said so once someone had spoken: 2026-09-25. Unlike a broken
# clean-up, there is no falling back to your own words here, because without
# the recogniser there are no words.
class DeadSTT(FakeSTT):
    load_error = "IncompleteSnapshotError: 2 file(s) are missing"


states = []
sibyl.get_stt = lambda: DeadSTT()
sibyl.warm_up = lambda model: None
sibyl.ollama_problem = lambda model: ""
sibyl.clean_transcript = lambda text, on_token=None: text
app = sibyl.Sibyl()
app._set_state = lambda state, note="", short="", stay=False: states.append(
    (state, note, short, stay))
app._boot()
check("a speech model that fails to load ends red, not ready",
      states[-1][0] == "failed", str(states[-1]))
check("and it stays red, rather than fading back to grey",
      states[-1][3] is True, str(states[-1]))
check("and the notch names the speech model, not the clean-up",
      "Speech" in states[-1][2], str(states[-1]))
check("a healthy Ollama does not hide a dead recogniser",
      not any(s[:2] == ("idle", "ready") for s in states), str(states))


# -- the speech model: from disk first, and a download that says so (E6) ----------
# The first start fetched base.en (about 340 MB) behind a plain "Waking up", and
# a half-finished fetch was only noticed later. Now the model's folder is
# checked first, and loaded straight from it when every file is there; anything
# missing is fetched, with the notch saying so. The folder is looked in rather
# than an error trusted, because measured 2026-10-02 a partial folder fails three
# different ways: "not found", a missing model.bin, or a missing tokenizer.json,
# which faster-whisper quietly fetches from somewhere else.
import tempfile  # noqa: E402
import types     # noqa: E402

from huggingface_hub.errors import LocalEntryNotFoundError  # noqa: E402

print("\nThe speech model at start")


def model_folder(*names):
    folder = tempfile.mkdtemp(dir=tmp)   # inside tmp, so it goes when tmp goes
    for name in names:
        pathlib.Path(folder, name).write_text("x")
    return folder


COMPLETE = ("config.json", "model.bin", "tokenizer.json", "vocabulary.txt")


FETCHED = model_folder(*COMPLETE)   # where a fetch that worked leaves the model


def load_with(find, build=None, fetch=lambda: FETCHED):
    """Load the real WhisperBackend with faster-whisper faked: `find` stands in
    for looking in the cache, `fetch` for downloading, `build` for loading the
    model. Returns the backend, the folders it built from, and whether it
    announced a download."""
    built, announced = [], []

    def whisper_model(where, **_k):
        built.append(where)
        if build is not None:
            build(where)

    def download_model(name, local_files_only=False, **_k):
        return find() if local_files_only else fetch()

    real = {k: sys.modules.get(k) for k in ("faster_whisper", "faster_whisper.utils")}
    sys.modules["faster_whisper"] = types.SimpleNamespace(WhisperModel=whisper_model)
    sys.modules["faster_whisper.utils"] = types.SimpleNamespace(
        download_model=download_model)
    try:
        stt = sibyl.WhisperBackend()
        stt.load(on_download=lambda: announced.append(True))
    finally:
        for k, v in real.items():
            if v is None:
                sys.modules.pop(k, None)
            else:
                sys.modules[k] = v
    return stt, built, bool(announced)


def not_found():
    raise LocalEntryNotFoundError("nothing cached")


folder = model_folder(*COMPLETE)
stt, built, announced = load_with(lambda: folder)
check("a model already on disk loads from its folder, with no download",
      built == [folder] and not announced and not stt.download_failed
      and not stt.load_error, f"{built} {announced} {stt.load_error}")

stt, built, announced = load_with(not_found)
check("a model never downloaded is fetched, and the notch is told first",
      built == [FETCHED] and announced and not stt.load_error,
      f"{built} {announced} {stt.load_error}")

for missing in ("model.bin", "tokenizer.json", "vocabulary.txt"):
    folder = model_folder(*(n for n in COMPLETE if n != missing))
    stt, built, announced = load_with(lambda: folder)
    check(f"a half-downloaded model missing {missing} is fetched again, not loaded",
          built == [FETCHED] and announced, f"{built} {announced}")


def no_network():
    raise OSError("connection reset")


stt, built, announced = load_with(not_found, fetch=no_network)
check("a download that fails is a load error, and known to be the download",
      stt.load_error and stt.download_failed and built == [],
      f"{stt.load_error} {stt.download_failed} {built}")


def out_of_memory(where):
    raise RuntimeError("out of memory")


# Found in the review of batch E: the first version marked "a download was
# needed", so a model that downloaded and then could not load blamed the
# connection, and dropped the one hint that shows the real error.
stt, built, announced = load_with(not_found, out_of_memory)
check("a download that worked, then a model that will not load, is not the download",
      stt.load_error and not stt.download_failed,
      f"{stt.load_error} {stt.download_failed}")


def bad_name():
    raise ValueError("Invalid model size 'base.eng'")


stt, built, announced = load_with(bad_name)
check("any other problem is reported as it is, with no download tried",
      stt.load_error and "ValueError" in stt.load_error and not announced
      and not stt.download_failed and built == [],
      f"{stt.load_error} {built} {announced}")


class FetchingSTT(FakeSTT):
    def load(self, on_download=None):
        on_download()


def boot_with(stt):
    states = []
    sibyl.get_stt = lambda: stt
    sibyl.warm_up = lambda model: None
    sibyl.ollama_problem = lambda model: ""
    sibyl.clean_transcript = lambda text, on_token=None: text
    app = sibyl.Sibyl()
    app._set_state = lambda state, note="", short="", stay=False: states.append(
        (state, note, short, stay))
    app._boot()
    return states


states = boot_with(FetchingSTT())
check("while it downloads, the notch says so",
      any(s[0] == "loading" and s[2] == "Downloading speech model" for s in states),
      str(states))
check("and a download that worked ends ready", states[-1][:2] == ("idle", "ready"),
      str(states[-1]))


class FailedFetchSTT(FetchingSTT):
    load_error = "OSError: connection reset"
    download_failed = True


states = boot_with(FailedFetchSTT())
check("a download that failed ends red, names the download, and stays",
      states[-1][0] == "failed" and states[-1][2] == "Speech model download failed"
      and states[-1][3] is True, str(states[-1]))


class FetchedButBrokenSTT(FetchingSTT):
    load_error = "RuntimeError: out of memory"


states = boot_with(FetchedButBrokenSTT())
check("a model that downloaded but will not load is named as the model, with the "
      "command that shows why",
      states[-1][2] == "Speech model failed" and "--test-stt" in states[-1][1],
      str(states[-1]))
states = boot_with(FakeSTT())
check("a model on disk never shows the download",
      not any("Downloading" in s[2] for s in states), str(states))


# get_stt() raises before load() is ever reached when STT_BACKEND names a
# backend that does not exist. _boot runs on a daemon thread, so an escape
# there kills boot in silence and leaves the notch on "loading" for ever.
def exploding_get_stt():
    raise RuntimeError("Unknown STT_BACKEND 'no-such-backend'.")


states = []
crashed = None
sibyl.get_stt = exploding_get_stt
sibyl.warm_up = lambda model: None
sibyl.ollama_problem = lambda model: ""
sibyl.clean_transcript = lambda text, on_token=None: text
app = sibyl.Sibyl()
app._set_state = lambda state, note="", short="", stay=False: states.append(
    (state, note, short, stay))
try:
    app._boot()
except Exception as exc:
    crashed = f"{type(exc).__name__}: {exc}"
check("a backend that cannot be built ends red at boot, not stuck on loading",
      crashed is None and states and states[-1][0] == "failed",
      f"raised {crashed}; states {states}")


# With no recogniser, no capture can work at all, so waiting a minute for Ollama
# and then warming it only delays news Sibyl already has (U13). The wait budget
# here is the same 0.2s that makes the healthy run above show "Waiting for
# Ollama" at least once; a dead recogniser must not show it even once.
sibyl.OLLAMA_WAIT_AT_START = 0.2
states = []
sibyl.get_stt = lambda: DeadSTT()
sibyl.warm_up = lambda model: states.append(("warmed", model, "", False))
sibyl.ollama_problem = lambda model: "Ollama not running"
sibyl.time.sleep = lambda s: real_sleep(0.05)
app = sibyl.Sibyl()
app._set_state = lambda state, note="", short="", stay=False: states.append(
    (state, note, short, stay))
app._boot()
check("a dead recogniser does not wait for Ollama first",
      not any(s[2] == "Waiting for Ollama" for s in states), str(states))
check("nor warms a model no capture will reach",
      not any(s[0] == "warmed" for s in states), str(states))
check("and still ends red, naming the recogniser",
      states[-1][0] == "failed" and states[-1][2] == "Speech model failed",
      str(states[-1]))

# A healthy Ollama is where the waste actually is: warming the model and running
# the practice clean-up cost about 17s, measured 2026-09-19, and no capture will
# ever reach either one while the recogniser is dead.
states = []
sibyl.ollama_problem = lambda model: ""
sibyl.clean_transcript = lambda text, on_token=None: states.append(
    ("practised", text, "", False)) or text
app = sibyl.Sibyl()
app._set_state = lambda state, note="", short="", stay=False: states.append(
    (state, note, short, stay))
app._boot()
check("a healthy Ollama is not warmed either, when nothing can reach it",
      not any(s[0] in ("warmed", "practised") for s in states), str(states))
check("and that boot still ends red rather than ready",
      states[-1][0] == "failed" and states[-1][2] == "Speech model failed",
      str(states[-1]))
sibyl.OLLAMA_WAIT_AT_START = 60

# The boot warning survives exactly one keypress: recording replaces it, and an
# ordinary failure fades back to the same grey dot a healthy Sibyl shows. A dead
# recogniser is not an ordinary failure. It fails identically every time, and
# there are no words to fall back to, so the dictation is simply lost.
#
# These drive the real _set_state, recording what reaches the notch, because the
# rule that keeps a failure on screen lives inside it. Stubbing _set_state here
# would test the stub.
class FakePill:
    def __init__(self):
        self.seen = []

    def set_state(self, state, short="", stay=False):
        self.seen.append((state, short, stay))


def dead_ear_capture(recorder):
    """One capture with a recogniser that will not load. Returns the notch's
    states, newest last."""
    app = sibyl.Sibyl()
    app.recorder = recorder
    app.pill = FakePill()
    app._finish_capture()
    return app.pill.seen


sibyl.get_stt = lambda: DeadSTT()
sibyl.beep = lambda *a, **k: None


def no_ear(wav_path):
    raise RuntimeError("Could not load the STT model. IncompleteSnapshotError")


sibyl.transcribe = no_ear
fd, name = tempfile.mkstemp(suffix=".wav", prefix="sibyl_test_")
os.close(fd)


class FakeRecorder:
    last_duration = 3.0

    def stop(self):
        return pathlib.Path(name)


seen = dead_ear_capture(FakeRecorder())
check("a capture with a dead recogniser ends red and stays red",
      seen[-1][0] == "failed" and seen[-1][2] is True, str(seen[-1]))
check("and the notch names it, so it cannot fade back to looking healthy",
      "Speech" in seen[-1][1], str(seen[-1]))

# Every other way out of a failing capture must keep that property, not just the
# one the fix was written for. A recording that will not delete says so from the
# `finally` block, after the failure above has already been set.
class StuckWav(type(pathlib.Path(name))):
    def unlink(self, missing_ok=False):
        raise PermissionError("file in use")


class StuckRecorder:
    last_duration = 3.0

    def stop(self):
        return StuckWav(name)


seen = dead_ear_capture(StuckRecorder())
check("a recording that will not delete cannot un-stick the dead-ear warning",
      seen[-1][0] == "failed" and seen[-1][2] is True, str(seen[-1]))

# The dead-ear check is read from inside exception handlers, so it must never
# raise. An STT_BACKEND naming a backend that does not exist makes the real
# get_stt() raise every time it is called.
crashed = None
sibyl.get_stt = exploding_get_stt
try:
    seen = dead_ear_capture(FakeRecorder())
except Exception as exc:
    seen = []
    crashed = f"{type(exc).__name__}: {exc}"
check("a backend that cannot even be built still reports a failure",
      crashed is None and any(s[0] == "failed" for s in seen),
      f"raised {crashed}; states {seen}")

sibyl.get_stt = lambda: DeadSTT()
sibyl.time.sleep = real_sleep


# -- what Ollama's trouble is called ---------------------------------------------

print("\nNaming the trouble")


sibyl.ollama_problem = real_problem


class FakeReply:
    def __init__(self, status, payload=None):
        self.status_code = status
        self._payload = payload

    def json(self):
        if self._payload is None:
            raise ValueError("not JSON")
        return self._payload


import requests   # noqa: E402  (after sibyl, whose import brings it in)

real_get = requests.get
cases = [
    ("nothing listening", lambda *a, **k: (_ for _ in ()).throw(OSError("refused")),
     "Ollama not running"),
    ("an error code", lambda *a, **k: FakeReply(500), "Ollama error 500"),
    ("a reply that is not JSON", lambda *a, **k: FakeReply(200), "Ollama answered oddly"),
    ("the model missing", lambda *a, **k: FakeReply(200, {"models": [{"name": "other"}]}),
     "Model not installed"),
    ("all well", lambda *a, **k: FakeReply(200, {"models": [{"name": sibyl.CLEANUP_MODEL}]}),
     ""),
]
for name, fake, expected in cases:
    requests.get = fake
    got = sibyl.ollama_problem(sibyl.CLEANUP_MODEL)
    check(f"{name}: {expected or 'no problem'}", got == expected, f"got {got!r}")
requests.get = real_get


# -- the address Sibyl calls ------------------------------------------------------

print("\nThe address of the clean-up model")
# Measured 2026-09-19: "localhost" costs about 2.07 s a call on Windows, which
# tries IPv6 first and only falls back to IPv4 after retrying. Ollama listens
# on IPv4 alone, so every clean-up paid it.
check("the default address is written out, never 'localhost'",
      "localhost" not in sibyl.OLLAMA_HOST, sibyl.OLLAMA_HOST)

# OLLAMA_HOST is Ollama's own setting too. "0.0.0.0:11434" there means "let
# other computers in" and broke every clean-up; any other computer there would
# have sent your words off this one. Sibyl reads only SIBYL_OLLAMA_HOST, and
# only ever talks to this computer (review round 3, C1). Proved on the real
# start-up path, in a fresh process, before the reader is tested on its own.
SHOW = "import sibyl; print(sibyl.OLLAMA_HOST); print(repr(sibyl.OLLAMA_ADDRESS_PROBLEM))"


def started_with(**settings):
    env = {k: v for k, v in os.environ.items()
           if k not in ("OLLAMA_HOST", "SIBYL_OLLAMA_HOST")}
    env.update(settings, LOCALAPPDATA=str(tmp / "appdata"))
    return subprocess.run([sys.executable, "-c", SHOW], env=env, cwd=str(HERE),
                          capture_output=True, text=True, timeout=60).stdout.splitlines()


out = started_with(OLLAMA_HOST="0.0.0.0:11434")
check("Ollama's own OLLAMA_HOST is ignored: Sibyl still calls this computer",
      out[:1] == ["http://127.0.0.1:11434"], str(out))
check("and it is not a problem", out[1:2] == ["''"], str(out))

env = {k: v for k, v in os.environ.items() if k != "SIBYL_OLLAMA_HOST"}
env.update(SIBYL_OLLAMA_HOST="http://192.168.1.20:11434", LOCALAPPDATA=str(tmp / "appdata"))
probe = subprocess.run([sys.executable, str(HERE / "sibyl.py"), "--test-ollama"], env=env,
                       cwd=str(HERE), capture_output=True, text=True, timeout=60).stdout
check("another computer in SIBYL_OLLAMA_HOST is refused, and --test-ollama says why",
      "SIBYL_OLLAMA_HOST" in probe and "192.168.1.20" in probe
      and "Probing" not in probe, probe[:300])

DEFAULT = "http://127.0.0.1:11434"
for setting, expected in [(None, DEFAULT), ("", DEFAULT),
                          ("http://127.0.0.1:11434/", DEFAULT),
                          ("http://127.0.0.1:11500", "http://127.0.0.1:11500"),
                          # rebuilt from the port, never passed on as typed
                          ("http://127.0.0.1:011434", DEFAULT)]:
    got = sibyl.ollama_address(setting)
    check(f"{setting!r} is this computer: {expected}", got == (expected, ""), str(got))

for setting in ["http://localhost:11434",           # this computer, but 2 s a call
                "http://0.0.0.0:11434",             # Ollama's "let others in"
                "0.0.0.0:11434",
                "127.0.0.1:11434",                  # Ollama's own form, no http://
                "http://192.168.1.20:11434",
                "https://127.0.0.1:11434",
                "http://someone@127.0.0.1:11434",
                "http://127.0.0.1:11434@192.168.1.20",   # not a .com: the gate
                                                         # reads that as an email
                "http://127.0.0.1.example.com:11434",
                "http://127.0.0.1:99999",
                "http://127.0.0.1:11434/api",
                "http://127.0.0.1:11434/?x=1",
                "http://127.0.0.1:11434#x"]:
    address, problem = sibyl.ollama_address(setting)
    check(f"{setting!r} is refused, and the address stays this computer",
          address == DEFAULT and "SIBYL_OLLAMA_HOST" in problem, f"{address} {problem!r}")

# Refused means nothing is sent at all: the clean-up fails as if Ollama were
# down, so your own words are pasted, and the start-up check says why in red.
real_post, sent = requests.post, []
requests.get = requests.post = lambda *a, **k: sent.append(a) or FakeReply(200, {})
sibyl.OLLAMA_ADDRESS_PROBLEM = "SIBYL_OLLAMA_HOST is 'http://192.168.1.20:11434'"
try:
    try:
        real_chat(sibyl.CLEANUP_MODEL, "system", "some words")
        raised = None
    except Exception as exc:
        raised = exc
    problem = sibyl.ollama_problem(sibyl.CLEANUP_MODEL)
finally:
    sibyl.OLLAMA_ADDRESS_PROBLEM = ""
    requests.get, requests.post = real_get, real_post
check("a refused address makes the clean-up a model failure, so your words are pasted",
      isinstance(raised, sibyl.OllamaError) and "SIBYL_OLLAMA_HOST" in str(raised),
      repr(raised))
check("and the start-up check shows it in red", problem == "Ollama address refused",
      repr(problem))
check("and nothing was sent anywhere", sent == [], str(sent))

# A proxy setting, common on work laptops, sends web traffic through another
# machine, and requests applies it even to 127.0.0.1: measured 2026-09-26, a
# call to Ollama went to the proxy (ProxyError). That would carry every
# dictation off the laptop. So every call to Ollama says "no proxy". Proved
# against a stand-in Ollama on this computer, with a proxy that answers nothing.
import contextlib        # noqa: E402
import http.server       # noqa: E402
import io                # noqa: E402
import json              # noqa: E402

reached = []


class StandIn(http.server.BaseHTTPRequestHandler):
    def _reply(self, body):
        self.send_response(200)
        self.end_headers()
        self.wfile.write(body.encode())

    def do_GET(self):
        reached.append(self.path)
        self._reply(json.dumps({"models": [{"name": sibyl.CLEANUP_MODEL}]}))

    def do_POST(self):
        reached.append(self.path)
        self.rfile.read(int(self.headers["Content-Length"]))
        self._reply(json.dumps({"message": {"content": "tidied"}, "done": True,
                                "done_reason": "stop"}) + "\n")

    def log_message(self, *_a):
        pass


server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), StandIn)
threading.Thread(target=server.serve_forever, daemon=True).start()
PROXY_NAMES = ("HTTP_PROXY", "http_proxy", "HTTPS_PROXY", "https_proxy", "NO_PROXY", "no_proxy")
saved_env = {k: os.environ.get(k) for k in PROXY_NAMES}
real_host = sibyl.OLLAMA_HOST
os.environ.update(HTTP_PROXY="http://127.0.0.1:9", http_proxy="http://127.0.0.1:9")
for k in ("NO_PROXY", "no_proxy"):
    os.environ.pop(k, None)
sibyl.OLLAMA_HOST = f"http://127.0.0.1:{server.server_address[1]}"
try:
    problem = real_problem(sibyl.CLEANUP_MODEL)
    try:
        answer = real_chat(sibyl.CLEANUP_MODEL, "system", "some words")
    except Exception as exc:
        answer = repr(exc)
    real_warm(sibyl.CLEANUP_MODEL)
    printed = io.StringIO()
    with contextlib.redirect_stdout(printed):
        sibyl.test_ollama()
finally:
    sibyl.OLLAMA_HOST = real_host
    for k, v in saved_env.items():
        if v is None:
            os.environ.pop(k, None)
        else:
            os.environ[k] = v
    server.shutdown()
check("with a proxy set, the start-up check still reaches Ollama directly",
      problem == "", repr(problem))
check("and so does the clean-up", answer == "tidied", answer)
check("and the warm-up", "/api/generate" in reached, str(reached))
check("and --test-ollama", "server is up" in printed.getvalue(), printed.getvalue()[:200])

# The same promise for any call added later: every requests call to Ollama, in
# Sibyl and in the eval, carries the no-proxy setting. Counted in any form
# requests offers, and the three ways of hiding a call from the count (a
# Session, a renamed import, a from-import) are refused outright (review, C1).
for name in ("sibyl.py", "eval_cleanup.py"):
    source = (HERE / name).read_text(encoding="utf-8")
    calls = len(re.findall(r"\brequests\.(?:get|post|put|patch|delete|head|options|request)\(",
                           source))
    routed = len(re.findall(r"proxies=(?:sibyl\.)?NO_PROXIES", source))
    check(f"every call to Ollama in {name} bypasses proxies ({routed} of {calls})",
          calls > 0 and routed == calls, f"{routed} of {calls}")
    hidden = re.findall(r"requests\.Session|import requests as|from requests import", source)
    check(f"and no call in {name} is hidden from that count", hidden == [], str(hidden))


# -- a missing logo ---------------------------------------------------------------

print("\nThe tray's logo")
real_file = sibyl.__file__
sibyl.__file__ = str(tmp / "nowhere" / "sibyl.py")
sys.stdout = open(os.devnull, "w")       # the "no logo" line is not the point
missing = sibyl._brand_icon_image()
sys.stdout.close()
sys.stdout = real_out
sibyl.__file__ = real_file
check("a missing sibyl.ico gives None, so the tray keeps the coloured dot",
      missing is None, repr(missing))
check("and the real one still loads", sibyl._brand_icon_image() is not None)


# -- the notch keeps a problem on show -------------------------------------------

class FakeRoot:
    def __init__(self):
        self.jobs = []

    def after(self, ms, fn):
        self.jobs.append(ms)
        return len(self.jobs)

    def after_cancel(self, job):
        pass


print("\nThe notch")
pill = sibyl.Pill()
pill.root = FakeRoot()
pill._apply_state("failed", "Microphone failed")
check("an ordinary failure fades back to the dot", pill.root.jobs != [], str(pill.root.jobs))
pill.root = FakeRoot()
pill._apply_state("failed", "Ollama not running", True)
check("a failure found at start stays", pill.root.jobs == [] and pill._state == "failed",
      str(pill.root.jobs))

# A refused press relabels the working chip (U30), and must not restart the
# count: _apply_state anchors it only on entering the state, and a press two
# minutes into a hung model would otherwise read "Still working 0s".
pill = sibyl.Pill()
pill.root = FakeRoot()
pill._apply_state("thinking", "Transcribing")
was = pill._since
real_sleep(0.05)
pill._apply_state("thinking", "Still working")
check("relabelling the working chip keeps its seconds counter honest",
      pill._since == was, f"{was} then {pill._since}")


print("\nA recording that will not delete")
# Where it is gets said, so you can delete it by hand. That path holds your
# Windows account name, in a log you might paste into a public issue. The
# recording sits in this test's own folder, and that folder's NAME is searched
# for, not its path: Python doubles every backslash when it prints a path
# inside an error, so a search for the path would pass with the leak in place.
import numpy as np  # noqa: E402

stuck = tmp / "sibyl_stuck.wav"
stuck.write_bytes(b"")


class LockedWav(type(stuck)):
    def unlink(self, missing_ok=False):
        raise PermissionError(13, "file in use", str(self))


class LockedRecorder:
    is_recording = False
    last_duration = 3.0

    def stop(self):
        return LockedWav(stuck)


def no_room(*_a, **_k):
    raise OSError("disk full")


def unsaved():
    """The real Recorder.stop, with a save that fails and a delete that fails."""
    rec = sibyl.Recorder()
    rec._frames = [np.zeros((sibyl.SAMPLE_RATE, 1), dtype=np.int16)]
    real_soundfile = sys.modules.get("soundfile")
    real_tempdir, real_unlink = tempfile.tempdir, pathlib.Path.unlink
    sys.modules["soundfile"] = types.SimpleNamespace(write=no_room)
    tempfile.tempdir = str(tmp)
    pathlib.Path.unlink = LockedWav.unlink
    try:
        rec.stop()
    except OSError:
        pass
    finally:
        pathlib.Path.unlink = real_unlink
        tempfile.tempdir = real_tempdir
        if real_soundfile is None:   # absent before: absent again, not None
            sys.modules.pop("soundfile", None)
        else:
            sys.modules["soundfile"] = real_soundfile


def locked_app():
    app = sibyl.Sibyl()
    app.recorder = LockedRecorder()
    app.pill = FakePill()
    return app


def quitting():
    app = locked_app()
    app._wav = LockedWav(stuck)
    real_exit = sibyl.os._exit
    sibyl.os._exit = lambda code: None
    try:
        app.quit()
    finally:
        sibyl.os._exit = real_exit


def printed(action):
    out = tmp / "stuck.log"
    out.unlink(missing_ok=True)
    stream = open(out, "w", encoding="utf-8")
    sys.stdout = stream
    try:
        action()
    finally:
        sys.stdout = real_out
        stream.close()
    return out.read_text(encoding="utf-8")


sibyl.transcribe = no_ear
ways = {
    "a recording that could not be saved": (unsaved, "remove it by hand"),
    "a cancel whose delete failed": (lambda: locked_app()._cancel_capture(),
                                     "cancel failed"),
    "a capture whose delete failed": (lambda: locked_app()._finish_capture(),
                                      "remove it by hand"),
    "a quit whose delete failed": (quitting, "remove it by hand"),
}
for logging in (True, False):
    sibyl._logging = logging
    for name, (action, line) in ways.items():
        text = printed(action)
        if logging:
            check(f"{name}: the log never names the folder it is in",
                  line in text and tmp.name not in text, text)
            check("but it still says which file is left, and where to look",
                  "in your temp folder" in text, text)
        else:
            check(f"{name}: a console still names it",
                  line in text and tmp.name in text, text)
sibyl._logging = False


print("\nAn error's own words")
# An error's text is written by whichever library failed, and libraries name
# folders: a broken Tcl install listed seven under the user folder. So a log
# line names the error's kind and keeps its text for a console (Part 2's rule).
# Each failure below carries this test folder's name in its text.
import tkinter  # noqa: E402

secret = f"C:/Users/{tmp.name}/tcl"
sibyl.get_stt = lambda: FakeSTT()
sibyl.capture_target_window = lambda: None


def fails(kind=RuntimeError):
    def fail(*_a, **_k):
        raise kind(f"broken in {secret}")
    return fail


class NoTk:
    def __init__(self, *_a, **_k):
        fails(tkinter.TclError)()


def no_window():
    real_tk, tkinter.Tk = tkinter.Tk, NoTk
    try:
        sibyl.Pill().build()
    finally:
        tkinter.Tk = real_tk


def no_pillow():
    broken = types.ModuleType("PIL")
    broken.__getattr__ = fails(ImportError)
    real_pil = sys.modules.get("PIL")
    sys.modules["PIL"] = broken
    try:
        sibyl.Pill().build()
    finally:
        if real_pil is None:
            sys.modules.pop("PIL", None)
        else:
            sys.modules["PIL"] = real_pil


class NoHandle:
    def winfo_id(self):
        fails(OSError)()


def drawing_pill():
    """A notch whose picture Windows refuses: the drawing itself fails."""
    pill = sibyl.Pill()
    pill.root = FakeRoot()
    pill._layout = lambda runs: {"w": 10}
    pill._frame = fails()
    return pill


class NoThread:
    def __init__(self, *_a, **_k):
        pass

    start = fails()


def no_worker():
    app = locked_app()
    real_thread, sibyl.threading.Thread = sibyl.threading.Thread, NoThread
    try:
        app.on_chord_down()
    finally:
        sibyl.threading.Thread = real_thread


class DeafRecorder(LockedRecorder):
    start = fails(OSError)


def no_microphone():
    app = locked_app()
    app.recorder = DeafRecorder()
    app._open_microphone()


def no_practice():
    sibyl.warm_up = lambda model: None
    sibyl.ollama_problem = lambda model: ""
    sibyl.clean_transcript = fails(sibyl.OllamaError)
    app = sibyl.Sibyl()
    app._set_state = lambda *a, **k: None
    app._boot()


ways = {
    "the notch's window cannot be built": (no_window, "TclError"),
    "tkinter or Pillow will not load": (no_pillow, "ImportError"),
    "the notch cannot refuse focus":
        (lambda: sibyl.Pill()._refuse_focus(NoHandle()), "OSError"),
    "the notch cannot draw": (lambda: drawing_pill()._drain(), "RuntimeError"),
    "a click on the notch fails": (lambda: sibyl.Pill()._hand_on(fails()),
                                   "RuntimeError"),
    "the recording cannot be started": (no_worker, "RuntimeError"),
    "the microphone will not open": (no_microphone, "OSError"),
    "the practice clean-up fails": (no_practice, "OllamaError"),
}
for logging in (True, False):
    sibyl._logging = logging
    for name, (action, kind) in ways.items():
        text = printed(action)
        if logging:
            check(f"{name}: the log names the error's kind, not its text",
                  kind in text and tmp.name not in text, text)
        else:
            check(f"{name}: a console still shows its text",
                  kind in text and tmp.name in text, text)
sibyl._logging = False

# The drawing runs every 60 ms: a fault that stays would write a thousand lines
# a minute. Said once, then again only after a drawing that worked.
# The chip's seconds change its label every second, so each one is a fresh
# drawing, and a tick in between has nothing to draw: neither is a success.
pill = drawing_pill()
seconds = iter(range(1000))


def ticks(n):
    for _ in range(n):
        pill._apply_state("thinking", f"Working {next(seconds)}s")
        pill._drain()
        pill._drain()


text = printed(lambda: ticks(3))
check("a display error that repeats is said once", text.count("display error") == 1, text)
pill._frame = lambda width: None
text = printed(lambda: ticks(1))
pill._frame = fails()
text += printed(lambda: ticks(1))
check("and said again once the drawing has worked in between",
      text.count("display error") == 1, text)


print("\nAn error nobody caught")
# Python's own report lists every file it passed through, with its full path,
# then the error's text. In the log it is the kind and the file names alone.
# The folder searched for is the one sibyl.py lives in.
def uncaught():
    try:
        sibyl.parse_binding(secret)
    except Exception:
        return sys.exc_info()


crash_log = tmp / "Sibyl" / "crash.log"
sibyl.LOG_FILE = crash_log
sibyl.open_log()
crash_stream = sys.stdout
try:
    sys.excepthook(*uncaught())
    worker = threading.Thread(target=lambda: sibyl.parse_binding(secret))
    worker.start()
    worker.join()
    # Errors a library catches and reports itself: the tray's and the keyboard
    # listener's, through logging, with the traceback attached.
    python_logging.getLogger("pystray._win32").error(f"menu failed in {secret}",
                                                     exc_info=uncaught())
    # Hugging Face's library gives its logger an output of its own, on the
    # stderr of the moment, which is the log once the speech model loads.
    load_with(lambda: FETCHED)
    python_logging.getLogger("huggingface_hub.file_download").error(
        f"could not cache in {secret}")
    with warnings.catch_warnings():
        warnings.simplefilter("always", UserWarning)
        warnings.warn(f"deprecated in {secret}")

    class Leaky:
        def __del__(self):
            raise ValueError(secret)

    Leaky()   # gone at once, and its error is "ignored", not raised
    root = None

    class BuiltTk:
        def __init__(self, *_a, **_k):
            global root
            root = self

        def overrideredirect(self, *_a):
            raise RuntimeError("stop here")

    real_tk, tkinter.Tk = tkinter.Tk, BuiltTk
    try:
        sibyl.Pill().build()
    finally:
        tkinter.Tk = real_tk
finally:
    sys.stdout, sys.stderr = real_out, real_err
    crash_stream._stream.close()   # still held open, so the folder would stay
    python_defaults()
    sibyl._logging = False
text = crash_log.read_text(encoding="utf-8")
crashes = [line for line in text.splitlines() if "[crash] ValueError" in line]
check("an error nobody caught, on the main thread or another, is logged",
      len(crashes) == 2, text)
check("a library's own error report says which library, and the error's kind",
      "[pystray._win32] ERROR ValueError" in text, text)
check("and where it happened, by file name and line, as a crash does",
      any("[pystray._win32]" in line and "at sibyl.py:" in line
          for line in text.splitlines()), text)
check("Hugging Face's own output is removed, so its words reach only that report",
      python_logging.getLogger("huggingface_hub").handlers == []
      and "[huggingface_hub.file_download] ERROR (details not logged)" in text,
      str(python_logging.getLogger("huggingface_hub").handlers))
# In this process an earlier test has already loaded that part of the library,
# so the order is checked in a fresh one, as at a real start: the loader must
# bring the output in itself before removing it, or the download adds it after.
fresh = subprocess.run([sys.executable, "-c", f"""
import logging, sys, types
sys.path.insert(0, {str(HERE)!r})
import sibyl
print("before:", "huggingface_hub.utils.logging" in sys.modules)
sys.modules["faster_whisper"] = types.SimpleNamespace(WhisperModel=lambda *a, **k: None)
sys.modules["faster_whisper.utils"] = types.SimpleNamespace(download_model=None)
sibyl.WhisperBackend._on_disk = staticmethod(lambda: ".")
sibyl.WhisperBackend().load()
import huggingface_hub.utils.logging   # as the real download does, later
print("after:", logging.getLogger("huggingface_hub").handlers)
"""], capture_output=True, text=True, timeout=60)
check("and stays removed after the download loads the library, at a fresh start",
      "before: False" in fresh.stdout and "after: []" in fresh.stdout,
      fresh.stdout + fresh.stderr)
check("a warning says its kind and where, by file name",
      "UserWarning at test_launch.py:" in text, text)
check("an error Python could only ignore is logged as ignored",
      "[ignored] ValueError" in text, text)
check("with where it happened, by file name and line",
      all("sibyl.py:" in line for line in crashes), text)
check("and neither a folder nor the error's text",
      HERE.name not in text and tmp.name not in text, text)
check("the notch's window reports its own errors the same way",
      getattr(root, "report_callback_exception", None) is sibyl.report_crash,
      str(root and vars(root)))


print("\nTidying up")
import shutil  # noqa: E402
shutil.rmtree(tmp, ignore_errors=True)   # if it fails, the check below says so
left = sorted(p.name for p in set(pathlib.Path(tempfile.gettempdir()).glob("sibyl_test_*")) - temp_before)
check("this run leaves nothing behind in the temp folder", left == [], str(left))


print(f"\n{sum(results)}/{len(results)} passed")
sys.exit(0 if all(results) else 1)
