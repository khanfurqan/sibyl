"""Hold to talk, tap for hands-free: does the app do the right thing with each?

test_taplistener.py proves the keyboard listener reports the right moments. This
proves what the app DOES with them, which is where a mistake would cost you:

  * a HOLD sends when you let go, exactly once;
  * a quick TAP keeps listening, and the next tap sends;
  * a long press also ends a hands-free recording, because that is what a person
    who has forgotten they were in hands-free will do;
  * another key joining in (Ctrl+Alt+something) DISCARDS the recording and sends
    nothing, because it was never meant for Sibyl;
  * but another key during a hands-free recording does not throw it away,
    because by then you are dictating and may well touch the keyboard;
  * a press while a capture is still being processed starts nothing;
  * a microphone that will not open says so, and the hotkey keeps working.
    An error escaping a keyboard callback stops the listener for good.

No microphone, no models, no keyboard: the recorder and the send are fakes.

Run:  python test_talk.py
"""
import os
import pathlib
import subprocess
import sys
import tempfile
import time
import types

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[0]))
import sibyl

results = []
temp_before = set(pathlib.Path(tempfile.gettempdir()).glob("sibyl_*.wav"))


def check(name, ok, detail=""):
    print(f"  {'PASS' if ok else 'FAIL'}  {name}" + (f"\n        {detail}" if detail else ""))
    results.append(ok)


class FakeWav:
    def __init__(self, log):
        self.log = log

    def unlink(self, missing_ok=False):
        self.log.append("wav deleted")


class FakeRecorder:
    """Only what the app touches. The real one opens the microphone."""

    def __init__(self, log):
        self.log = log
        self.is_recording = False
        self.last_duration = 1.0

    def start(self, on_timeout=None, on_warning=None):
        self.is_recording = True
        self.log.append("start")

    def stop(self):
        self.is_recording = False
        self.log.append("stop")
        return FakeWav(self.log)


REAL_BEEP = sibyl.beep   # kept for the test at the end, before it is silenced
sibyl.beep = lambda *_a, **_k: None
sibyl.capture_target_window = lambda: 4242
sibyl.window_title = lambda hwnd: "Some Window"


def fresh():
    """An app whose send is replaced by a note in the log."""
    log = []
    app = sibyl.Sibyl()
    app.recorder = FakeRecorder(log)

    def fake_finish():
        app.recorder.stop()
        log.append("SENT")
        with app._lock:
            app._busy = False

    app._finish_capture = fake_finish
    return app, log


def settle(app, timeout=2.0):
    """Opening the microphone, the send and the cancel run on worker threads,
    as they must: blocking in a keyboard callback freezes the listener. Wait
    for them to finish."""
    deadline = time.time() + timeout
    while (app._busy or app._starting) and time.time() < deadline:
        time.sleep(0.01)


print("Hold to talk, tap for hands-free")

# -- hold: press, speak, let go ---------------------------------------------
app, log = fresh()
app.on_chord_down()
settle(app)
check("pressing the chord starts recording", log == ["start"], str(log))
app.on_hold_release()
settle(app)
check("letting go after a hold sends, once", log.count("SENT") == 1, str(log))
check("and the recorder is stopped", not app.recorder.is_recording)

# -- tap: hands-free ----------------------------------------------------------
app, log = fresh()
app.on_chord_down()
app.on_tap()
settle(app)
check("a quick tap keeps listening instead of sending",
      app.recorder.is_recording and "SENT" not in log, str(log))
app.on_chord_down()
check("the second press does not start a second recording",
      log.count("start") == 1, str(log))
app.on_tap()
settle(app)
check("the second tap sends", log.count("SENT") == 1, str(log))

# -- hands-free, ended by a long press ----------------------------------------
app, log = fresh()
app.on_chord_down()
app.on_tap()
app.on_chord_down()
app.on_hold_release()
settle(app)
check("a long press also ends a hands-free recording", log.count("SENT") == 1, str(log))

# -- another key joins in: discard -------------------------------------------
app, log = fresh()
app.on_chord_down()
app.on_abort()
settle(app)
check("Ctrl+Alt+something discards the recording and sends nothing",
      "SENT" not in log and "stop" in log, str(log))
check("the discarded recording's audio file is deleted", "wav deleted" in log, str(log))
check("and Sibyl is ready again", not app.recorder.is_recording and not app._busy)

# -- another key during hands-free: keep going --------------------------------
app, log = fresh()
app.on_chord_down()
app.on_tap()
app.on_abort()
settle(app)
check("a stray key during hands-free does not throw the dictation away",
      app.recorder.is_recording and "SENT" not in log, str(log))

# -- busy ---------------------------------------------------------------------
app, log = fresh()
app._busy = True
app.on_chord_down()
check("a press while a capture is still processing starts nothing",
      log == [], str(log))
app._busy = False

# A refused press used to beep and print, and change nothing you could see. On a
# model that has hung, that is indistinguishable from dead hardware for up to two
# minutes (U30). The notch now says it is still working, and keeps the seconds
# counter it already had, because the count is the reassurance.
app, log = fresh()
seen = []
notes = []
app.pill = types.SimpleNamespace(
    set_state=lambda state, short="", stay=False: seen.append((state, short)))
real_set_state = app._set_state


def watched(state, note="", short="", stay=False):
    notes.append(note)
    real_set_state(state, note, short, stay)


app._set_state = watched
app._busy = True
app.on_chord_down()
check("a press while still working says so on the notch, not only the console",
      any(st == "thinking" and word for st, word in seen), str(seen))
# _busy is one flag for six operations, and on_abort, on_notch_cancel and recall
# are not captures. Naming a capture would be specific, plausible and sometimes
# false, so the words stay operation-neutral.
check("and does not claim a capture, since a cancel and Paste again share the flag",
      not any("capture" in word.lower() for _st, word in seen)
      and not any("capture" in (n or "").lower() for n in notes), str(seen + notes))
app._busy = False

# -- a recogniser that will never work ----------------------------------------
# Recording would only waste your breath: every capture fails the same way until
# Sibyl restarts, and there are no words to fall back to (U14).
app, log = fresh()
real_get_stt = sibyl.get_stt
sibyl.get_stt = lambda: types.SimpleNamespace(load_error="IncompleteSnapshotError")
seen = []
app.pill = types.SimpleNamespace(
    set_state=lambda state, short="", stay=False: seen.append((state, short, stay)))
app.on_chord_down()
check("a dead recogniser refuses to record instead of wasting your breath",
      log == [] and not app.recorder.is_recording, str(log))
check("and says why, in red, and stays",
      any(st == "failed" and "Speech" in word and stay for st, word, stay in seen),
      str(seen))
sibyl.get_stt = real_get_stt

# -- back to back ---------------------------------------------------------------
app, log = fresh()
app.on_chord_down()
app.on_hold_release()
settle(app)
app.on_chord_down()
app.on_hold_release()
settle(app)
check("two holds in a row make two recordings and two sends",
      log.count("start") == 2 and log.count("SENT") == 2, str(log))

# -- the microphone will not open ------------------------------------------------


class BrokenRecorder(FakeRecorder):
    def start(self, on_timeout=None, on_warning=None):
        raise OSError("no input device")


app, log = fresh()
app.recorder = BrokenRecorder(log)
notes = []
app._set_state = lambda state, note="", short="": notes.append(note)
try:
    app.on_chord_down()
    escaped = None
except Exception as exc:
    escaped = exc
settle(app)
check("a microphone that will not open does not crash the key listener",
      escaped is None, repr(escaped))
check("and Sibyl is neither busy nor recording",
      not app._busy and not app.recorder.is_recording)
check("and says why", any("could not start recording" in n for n in notes), str(notes))
app.recorder = FakeRecorder(log)
app.on_chord_down()
settle(app)
check("the next press, with the microphone back, records", log == ["start"], str(log))

# -- a window title the output cannot spell ----------------------------------------
# With output captured to a file or a pipe, Windows prints in cp1252, which has no
# zero-width space; a Chrome tab title held one (2026-10-04). Printing that title
# raised, and the press showed "Microphone failed". Run in a second Python whose
# output is strict cp1252 from the start, because that is when the bug exists.

PRESS = r'''
import time, sibyl
sibyl.beep = lambda *a, **k: None
sibyl.capture_target_window = lambda: 4242
sibyl.window_title = lambda hwnd: "Inbox\u200b - Chrome"
class Recorder:
    is_recording = False
    def start(self, on_timeout=None, on_warning=None):
        self.is_recording = True
app = sibyl.Sibyl()
app.recorder = Recorder()
app.on_chord_down()
deadline = time.time() + 2
while app._starting and time.time() < deadline:
    time.sleep(0.01)
print("RECORDING" if app.recorder.is_recording else "NOT RECORDING")
'''
child = subprocess.run([sys.executable, "-c", PRESS], capture_output=True,
                       cwd=str(pathlib.Path(__file__).resolve().parent),
                       env={**os.environ, "PYTHONIOENCODING": "cp1252"}, timeout=60)
said = child.stdout.decode("cp1252", "replace")
check("a window title the output cannot spell does not stop the recording",
      "RECORDING" in said.splitlines(), said[-400:] + child.stderr.decode("cp1252", "replace")[-400:])
check("and the title is still printed, the odd character as its code",
      "will paste back into: Inbox\\u200b - Chrome" in said, said[-400:])

# -- the microphone takes its time ------------------------------------------------
# Opening it took 187 to 429 ms, measured, and it used to happen inside the
# keyboard callback, on the thread Windows times: a hook that answers too slowly
# is silently removed (review round 3, batch B). Now the callback only notes the
# window and hands the opening to a worker. Whatever you do with the keys while
# the microphone opens is kept and applied, in order, once it is listening.


class SlowRecorder(FakeRecorder):
    def start(self, on_timeout=None, on_warning=None):
        time.sleep(0.5)
        super().start(on_timeout)


def slow():
    app, log = fresh()
    app.recorder = SlowRecorder(log)
    return app, log


app, log = slow()
began = time.perf_counter()
app.on_chord_down()
took = time.perf_counter() - began
check("the key handler returns at once while the microphone opens",
      took < 0.02, f"{took * 1000:.0f} ms")
check("and nothing is recording yet", log == [], str(log))
app.on_chord_down()   # a second press while it opens
settle(app)
check("the microphone then opens, once, and listens",
      log == ["start"] and app.recorder.is_recording, str(log))

app, log = slow()
app.on_chord_down()
app.on_hold_release()
settle(app)
check("a hold let go while the microphone opens still sends, once",
      log == ["start", "stop", "SENT"], str(log))

app, log = slow()
app.on_chord_down()
app.on_tap()
settle(app)
check("a tap while the microphone opens becomes hands-free",
      app.recorder.is_recording and app._hands_free and "SENT" not in log, str(log))
app.on_chord_down()
app.on_tap()
settle(app)
check("and the next tap sends", log.count("SENT") == 1, str(log))

app, log = slow()
app.on_chord_down()
app.on_abort()
settle(app)
check("another key while the microphone opens discards the recording",
      "SENT" not in log and "wav deleted" in log
      and not app.recorder.is_recording and not app._busy, str(log))

app, log = slow()
app.on_chord_down()
app.on_tap()
app.on_tap()
settle(app)
check("two taps while it opens: hands-free, then send", log.count("SENT") == 1, str(log))

# Paste again while the microphone opens would race the capture for the clipboard.
app, log = slow()
app._last_output = "the last text"
injected = []
real_inject = sibyl.inject
sibyl.inject = lambda text, target: injected.append(text)
app.on_chord_down()
app.recall()
settle(app)
sibyl.inject = real_inject
check("Paste again while the microphone opens pastes nothing", injected == [], str(injected))

app, log = slow()
app._last_output = "the last text"
copied = []
real_copy_only = sibyl.copy_only
sibyl.copy_only = lambda text: copied.append(text)
app.on_chord_down()
app.recopy()
settle(app)
sibyl.copy_only = real_copy_only
check("and the tray's copy again leaves the clipboard alone", copied == [], str(copied))

# A click has no hold, so the dot starts hands-free. A second click while the
# microphone opens must not count as the tap that sends.
app, log = slow()
app.on_notch_click()
app.on_notch_click()
settle(app)
check("a double-click on the dot listens hands-free and sends nothing",
      app.recorder.is_recording and app._hands_free and "SENT" not in log, str(log))


class SlowBrokenRecorder(FakeRecorder):
    def start(self, on_timeout=None, on_warning=None):
        time.sleep(0.5)
        raise OSError("no input device")


app, log = fresh()
app.recorder = SlowBrokenRecorder(log)
seen = []
app.pill = types.SimpleNamespace(
    set_state=lambda state, short="", stay=False: seen.append((state, short)))
app.on_chord_down()
app.on_tap()   # a tap that arrives while the doomed microphone opens
settle(app)
check("a microphone that fails while opening leaves Sibyl idle, not busy",
      not app._busy and not app._starting
      and not app.recorder.is_recording, str(log))
check("and says Microphone failed", ("failed", "Microphone failed") in seen, str(seen))
app.recorder = FakeRecorder(log)
app.on_chord_down()
settle(app)
check("and the tap it swallowed does not carry over to the next press",
      app.recorder.is_recording and not app._hands_free, str(log))

# If Windows will not make the worker thread, the error must not escape the key
# callback (pynput would stop listening for good), and "starting" must not stay
# set, or every later press would be ignored in silence.


class NoThreads:
    def __init__(self, *_a, **_k):
        pass

    def start(self):
        raise RuntimeError("can't start new thread")


app, log = fresh()
seen = []
app.pill = types.SimpleNamespace(
    set_state=lambda state, short="", stay=False: seen.append((state, short)))
real_thread = sibyl.threading.Thread
sibyl.threading.Thread = NoThreads
try:
    app.on_chord_down()
    escaped = None
except Exception as exc:
    escaped = exc
finally:
    sibyl.threading.Thread = real_thread
check("a worker thread that cannot start does not crash the key listener",
      escaped is None, repr(escaped))
check("and Sibyl is not left starting, so the next press works",
      not app._starting and not app._busy, str(log))
check("and says Microphone failed", ("failed", "Microphone failed") in seen, str(seen))

# The real Recorder, with the sound library replaced: if the stream opens but
# will not start, it must be closed, not left holding the microphone.


class StubbornStream:
    def __init__(self, **_kwargs):
        self.closed = False
        opened.append(self)

    def start(self):
        raise OSError("device busy")

    def close(self):
        self.closed = True


opened = []
real_sd = sys.modules.get("sounddevice")
sys.modules["sounddevice"] = types.SimpleNamespace(InputStream=StubbornStream)
recorder = sibyl.Recorder()
try:
    recorder.start()
except OSError:
    pass
if real_sd is None:
    del sys.modules["sounddevice"]
else:
    sys.modules["sounddevice"] = real_sd
check("a stream that will not start is closed again",
      len(opened) == 1 and opened[0].closed, str([s.closed for s in opened]))
check("and the recorder does not keep hold of it",
      recorder._stream is None and not recorder.is_recording)

# -- the time limit (E5) -----------------------------------------------------------
# The first test that lets the cap fire. A capture used to stop at two minutes in
# silence, mid-sentence. Now a tone warns ten seconds before, and the chip says
# why it stopped. The cap is set to 1 second and the warning to half of one; the
# real Recorder runs, with a fake microphone that hands over half a second of
# silence the moment it starts.
import numpy as np  # noqa: E402


class QuietStream:
    def __init__(self, callback=None, **_kwargs):
        self.callback = callback

    def start(self):
        self.callback(np.zeros((sibyl.SAMPLE_RATE // 2, 1), dtype="int16"), 0, None, None)

    def stop(self):
        pass

    def close(self):
        pass


real_cap, real_lead = sibyl.MAX_RECORD_SECONDS, sibyl.CAP_WARNING_SECONDS
real_sd = sys.modules.get("sounddevice")
sys.modules["sounddevice"] = types.SimpleNamespace(InputStream=QuietStream)
sibyl.MAX_RECORD_SECONDS, sibyl.CAP_WARNING_SECONDS = 1, 0.5
try:
    fired = []
    began = time.time()
    recorder = sibyl.Recorder()
    recorder.start(on_timeout=lambda: fired.append(("cap", time.time() - began)),
                   on_warning=lambda: fired.append(("warning", time.time() - began)))
    time.sleep(1.4)
    wav = recorder.stop()
    if wav is not None:
        wav.unlink(missing_ok=True)
    order = [what for what, _ in fired]
    check("the warning comes before the cap, and both come",
          order == ["warning", "cap"], str(fired))
    check("the warning comes the set time before the cap",
          len(fired) == 2 and abs(fired[1][1] - fired[0][1] - 0.5) < 0.15, str(fired))

    fired.clear()
    recorder.start(on_timeout=lambda: fired.append("cap"),
                   on_warning=lambda: fired.append("warning"))
    time.sleep(0.2)
    wav = recorder.stop()
    time.sleep(1.1)
    if wav is not None:
        wav.unlink(missing_ok=True)
    check("letting go early cancels both", fired == [], str(fired))

    # The whole app: hold the keys past the cap, and never let go.
    beeps, states = [], []
    sibyl.beep = lambda kind="start": beeps.append(kind)
    real_steps = (sibyl.transcribe, sibyl.clean_or_fall_back, sibyl.inject)
    sibyl.transcribe = lambda wav: "hello there"
    sibyl.clean_or_fall_back = lambda raw: ("Hello there.", "")
    sibyl.inject = lambda text, hwnd=None: True
    try:
        app = sibyl.Sibyl()
        app._set_state = lambda state, note="", short="", stay=False: states.append(
            (state, short))
        app.on_chord_down()
        deadline = time.time() + 3
        while ("done", "") not in states and time.time() < deadline:
            time.sleep(0.02)
        settle(app)
    finally:
        sibyl.transcribe, sibyl.clean_or_fall_back, sibyl.inject = real_steps
        sibyl.beep = lambda *_a, **_k: None
    check("held past the cap, the warning tone plays first",
          "warning" in beeps and beeps.index("warning") < beeps.index("stop"), str(beeps))
    working = [short for state, short in states if state == "thinking"]
    check("and at the cap the chip says why it is sending, the whole time it works",
          working and all(w == "Time limit, sending" for w in working), str(states))
    check("and it is sent", ("done", "") in states and not app._busy, str(states))
finally:
    sibyl.MAX_RECORD_SECONDS, sibyl.CAP_WARNING_SECONDS = real_cap, real_lead
    if real_sd is None:
        del sys.modules["sounddevice"]
    else:
        sys.modules["sounddevice"] = real_sd
check("the warning tone is its own, not one of the others",
      sibyl._TONES.get("warning") not in (sibyl._TONES["start"], sibyl._TONES["stop"],
                                          sibyl._TONES["error"], None),
      str(sibyl._TONES.get("warning")))

# -- a beep never holds up the keyboard ------------------------------------------
# winsound.Beep blocks for as long as the tone plays: 90 ms to start, 220 ms for
# an error. It was played inside the keyboard callback, on the thread Windows
# times, and Windows silently removes a keyboard hook that answers too slowly
# (review round 3). A fake winsound plays a slow tone.

tones = []


def slow_tone(freq, ms):
    time.sleep(0.3)
    tones.append(freq)


real_winsound = sys.modules.get("winsound")
sys.modules["winsound"] = types.SimpleNamespace(Beep=slow_tone)
began = time.perf_counter()
REAL_BEEP("error")
took = time.perf_counter() - began
deadline = time.time() + 2
while not tones and time.time() < deadline:
    time.sleep(0.01)
if real_winsound is None:
    del sys.modules["winsound"]
else:
    sys.modules["winsound"] = real_winsound
check("a beep returns at once, not when the tone ends", took < 0.05, f"{took * 1000:.0f} ms")
check("and the tone still plays", tones == [220], str(tones))

left = sorted(p.name for p in set(pathlib.Path(tempfile.gettempdir()).glob("sibyl_*.wav")) - temp_before)
check("this run leaves nothing behind in the temp folder", left == [], str(left))

passed = sum(results)
print(f"\n{passed}/{len(results)} passed")
sys.exit(0 if passed == len(results) else 1)
