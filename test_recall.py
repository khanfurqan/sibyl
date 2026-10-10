"""Phase 3 job 3: can the last output be sent again, somewhere else?

The problem this closes. Sibyl cannot tell whether a paste landed. It restores
focus to the window you started speaking in and sends Ctrl+V, and if the thing
with focus is a sidebar, a file list or a page with no cursor in a text box, the
keystroke does nothing at all. The log still says "sent 517 chars", because from
inside the process that is all we know. A user has gone looking for text that
was never anywhere.

The roadmap's answer was a focus detector. Windows will not reliably say whether
the focused element accepts text, and is blindest inside browsers and Electron
apps, which is where the work happens. So this does not ask the question. It
makes the answer stop mattering: the output is kept and can be sent again
wherever the cursor is now. F9 did that until 2026-09-11, when it was removed as
hard to remember; the notch's "Paste again" button takes it over, and the tray
keeps "Copy last output again" in the meantime. The logic tested here is the
same whichever of them calls it.

Six things to prove, and the first is the only one that is the feature:

  1. Recall pastes the stored text, unchanged.
  2. It pastes with NO target window. Everywhere else in Sibyl the paste is
     restored to the window you started speaking in, which is the most
     important fix in the project. Here that would defeat the point: you have
     moved, deliberately, to somewhere the text can actually go.
  3. The output is stored even when the paste FAILED. That is the case the
     whole feature exists for.
  4. Recall does not consume it. Recalling into the wrong window is something
     you fix by recalling again, not something that costs you the text.
  5. It refuses while a capture is running, rather than fighting it for the
     clipboard and the paste.
  6. It releases the busy flag afterwards, so a refusal now is not a lock-up
     forever.

Needs no microphone, no models, and no keyboard.

Run:  python test_recall.py
"""
import pathlib
import sys
import time

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[0]))
import sibyl

results = []


def check(name, ok, detail=""):
    print(f"  {'PASS' if ok else 'FAIL'}  {name}" + (f"\n        {detail}" if detail else ""))
    results.append(ok)


def wait_idle(app, timeout=3.0):
    """Recall hands off to a worker thread, because blocking inside a pynput
    callback freezes the listener. So the test waits for that thread rather
    than assuming it has run."""
    deadline = time.time() + timeout
    while app._busy and time.time() < deadline:
        time.sleep(0.01)
    return not app._busy


# -- fakes ------------------------------------------------------------------
# Nothing here talks to the operating system. inject() is the boundary: it is
# the only thing that touches the clipboard or sends a keystroke, so replacing
# it is what makes the rest of this runnable without a desktop in front of it.

injected = []      # every (text, target_hwnd) pair inject was asked for
copied = []        # every text copy_only was asked for


def fake_inject(text, target_hwnd=None):
    injected.append((text, target_hwnd))
    return True


def fake_copy_only(text):
    copied.append(text)


real_copy_only = sibyl.copy_only   # the clipboard tests at the end need it
sibyl.inject = fake_inject
sibyl.copy_only = fake_copy_only
sibyl.beep = lambda *_a, **_k: None
sibyl.capture_target_window = lambda: 4242
sibyl.window_title = lambda hwnd: "Some Other Window"

OUTPUT = "I want to test recall."


print("Recall, no microphone and no models")

# -- nothing captured yet ---------------------------------------------------
app = sibyl.Sibyl()
app.recall()
check("refuses when nothing has been captured yet",
      not injected and not app._busy,
      f"pastes attempted: {len(injected)}")

# -- the feature ------------------------------------------------------------
app._last_output = OUTPUT
app.recall()
finished = wait_idle(app)
check("the worker thread finished and released the busy flag", finished)
check("recall pastes the stored text, unchanged",
      len(injected) == 1 and injected[0][0] == OUTPUT,
      f"got: {injected[0][0][:40]!r}..." if injected else "nothing was pasted")
check("recall pastes with NO target window, so it lands where you are now",
      bool(injected) and injected[0][1] is None,
      f"target was {injected[0][1]!r}" if injected else "nothing was pasted")

# -- it is not consumed -----------------------------------------------------
app.recall()
wait_idle(app)
check("the output survives a recall, so it can be recalled again",
      len(injected) == 2 and injected[1][0] == OUTPUT,
      f"second paste: {len(injected)} total")

# -- refuses while busy -----------------------------------------------------
before = len(injected)
app._busy = True
app.recall()
check("refuses while a capture is in flight", len(injected) == before)
app._busy = False


class FakeRecorder:
    """Only the two attributes recall reads. The real one opens an audio
    device, which this test has no business needing."""
    is_recording = True
    last_duration = 0.0


app.recorder = FakeRecorder()
before = len(injected)
app.recall()
check("refuses while still recording", len(injected) == before)
check("a refusal leaves the busy flag clear, so it is not a lock-up",
      not app._busy)

# -- the tray path ----------------------------------------------------------
# It copies and deliberately does NOT paste: a tray click has already taken the
# foreground, so pasting would fire the text into whatever the click left in
# front, which is the bug the focus work fixed.
app.recorder = FakeRecorder()
app.recorder.is_recording = False
before = len(injected)
app.recopy()
check("the tray item puts the text back on the clipboard",
      copied and copied[-1] == OUTPUT,
      f"copied: {len(copied)}")
check("the tray item never pastes", len(injected) == before)

# -- stored even when the paste failed --------------------------------------
# The case the feature exists for. A capture whose paste was refused must still
# leave the text recallable, so the store has to happen before the attempt.


class FakeWav:
    def unlink(self, missing_ok=False):
        pass


fresh = sibyl.Sibyl()
fresh.recorder = FakeRecorder()
fresh.recorder.is_recording = False
fresh.recorder.stop = lambda: FakeWav()
sibyl.transcribe = lambda wav: "i want to test recall"
sibyl.fix_technical_terms = lambda text: text
sibyl.clean_transcript = lambda raw, on_token=None: OUTPUT
sibyl.inject = lambda text, target_hwnd=None: False   # paste refused
fresh._finish_capture()
check("the output is stored even when the paste was refused",
      fresh._last_output == OUTPUT,
      f"stored: {fresh._last_output[:40]!r}")

sibyl.inject = fake_inject   # put the recording fake back

# -- a busy clipboard says so, here too (E2) -----------------------------------
# The user guide promises "Clipboard busy" whenever another program holds the
# clipboard. Paste again and the tray's copy said "Paste failed" and "Copy
# failed" instead (review of batch E).


def busy(*_a, **_k):
    raise sibyl.ClipboardBusy("the clipboard is in use by another program")


shorts = []
held = sibyl.Sibyl()
held._last_output = OUTPUT
held._set_state = lambda state, note="", short="", stay=False: shorts.append(
    (state, short))
sibyl.inject, sibyl.copy_only = busy, busy
try:
    held.recall()
    wait_idle(held)
    check("Paste again with the clipboard held says so",
          shorts[-1:] == [("failed", "Clipboard busy")], str(shorts[-1:]))
    held.recopy()
    check("and so does the tray's copy",
          shorts[-1:] == [("failed", "Clipboard busy")], str(shorts[-1:]))
finally:
    sibyl.inject, sibyl.copy_only = fake_inject, fake_copy_only

# -- the clipboard itself ----------------------------------------------------
# Windows can keep a history of everything copied (Win+V) and sync it to your
# other devices. Microsoft names three markers that tell it to skip an entry
# ("Clipboard Formats", learn.microsoft.com, checked 2026-09-26). They must go on
# in the same clipboard operation as the text: history takes its copy when the
# clipboard is closed. This uses the real clipboard, and puts back what was on it.
import subprocess         # noqa: E402

import pyperclip          # noqa: E402


def clipboard_tests():
    """Windows only: the markers are Windows' own. macOS copies with pyperclip."""
    import ctypes
    from ctypes import wintypes as wt

    u32 = ctypes.WinDLL("user32", use_last_error=True)
    k32 = ctypes.WinDLL("kernel32", use_last_error=True)
    u32.OpenClipboard.argtypes = [wt.HWND]
    u32.GetClipboardData.argtypes = [wt.UINT]
    u32.GetClipboardData.restype = wt.HANDLE
    u32.RegisterClipboardFormatW.argtypes = [wt.LPCWSTR]
    u32.RegisterClipboardFormatW.restype = wt.UINT
    k32.GlobalLock.argtypes = [wt.HGLOBAL]
    k32.GlobalLock.restype = wt.LPVOID
    k32.GlobalUnlock.argtypes = [wt.HGLOBAL]
    k32.GlobalSize.argtypes = [wt.HGLOBAL]
    k32.GlobalSize.restype = ctypes.c_size_t

    def markers():
        """Each opt-out name, and the bytes stored under it (None if absent)."""
        found = {}
        if not u32.OpenClipboard(None):
            return found
        try:
            for name in ("ExcludeClipboardContentFromMonitorProcessing",
                         "CanIncludeInClipboardHistory", "CanUploadToCloudClipboard"):
                handle = u32.GetClipboardData(u32.RegisterClipboardFormatW(name))
                if not handle:
                    found[name] = None
                    continue
                pointer = k32.GlobalLock(handle)
                found[name] = ctypes.string_at(pointer, k32.GlobalSize(handle))[:4]
                k32.GlobalUnlock(handle)
        finally:
            u32.CloseClipboard()
        return found

    def empty():
        if u32.OpenClipboard(None):
            u32.EmptyClipboard()
            u32.CloseClipboard()

    try:
        saved = pyperclip.paste()   # "" when what you copied was not text
    except Exception:
        saved = ""
    TEXT = "Café, crème brûlée — 🙂\nand a second line."
    try:
        real_copy_only(TEXT)
        back = pyperclip.paste()
        seen = markers()
        # A marker Windows refuses (an empty name), after the real three. The
        # text must not be left behind without them: that is the exposure the
        # markers exist to prevent (review, C2).
        real_names = sibyl.CLIPBOARD_OPT_OUT
        empty()
        sibyl.CLIPBOARD_OPT_OUT = real_names + ("",)
        try:
            real_copy_only(TEXT)
            refused = None
        except Exception as exc:
            refused = exc
        finally:
            sibyl.CLIPBOARD_OPT_OUT = real_names
        left = pyperclip.paste()
        # Another program holding the clipboard: the copy must give up and say
        # so, not freeze the capture that is waiting to paste.
        holder = subprocess.Popen(
            [sys.executable, "-c",
             "import ctypes, sys, time; ctypes.windll.user32.OpenClipboard(None); "
             "print('held', flush=True); time.sleep(3)"],
            stdout=subprocess.PIPE, text=True)
        holder.stdout.readline()
        began = time.perf_counter()
        try:
            real_copy_only("never lands")
            busy = None
        except Exception as exc:
            busy = exc
        waited = time.perf_counter() - began
        holder.wait()
    finally:
        # Your text goes back. Anything else you had copied (a picture, a file)
        # cannot be rebuilt here, so the clipboard is emptied rather than left
        # holding this test's sentence.
        if saved:
            pyperclip.copy(saved)
        else:
            empty()
    # Never the text itself in the detail: if the copy failed, what reads back
    # is whatever was on your clipboard before, which could be anything.
    check("the text reads back exactly: accents, a dash, an emoji, a new line",
          back == TEXT, "matched" if back == TEXT
          else f"did not match ({len(back)} characters, not shown)")
    check("and history is told to skip it",
          seen.get("ExcludeClipboardContentFromMonitorProcessing") is not None, str(seen))
    check("and history is told no, with a zero",
          seen.get("CanIncludeInClipboardHistory") == b"\0\0\0\0", str(seen))
    check("and the cloud clipboard is told no, with a zero",
          seen.get("CanUploadToCloudClipboard") == b"\0\0\0\0", str(seen))
    check("a marker that cannot be written fails, and leaves no unmarked text behind",
          refused is not None and left != TEXT,
          "the text was left on the clipboard" if left == TEXT else repr(refused))
    check("a clipboard held by another program fails, and says so, within a second",
          isinstance(busy, sibyl.ClipboardBusy) and waited < 1.0, f"{busy!r} after {waited:.2f}s")


print("\nThe clipboard itself")
if sys.platform == "win32":
    clipboard_tests()
else:
    print("  (Windows only: macOS copies with pyperclip, which has no markers to test)")

passed = sum(results)
print(f"\n{passed}/{len(results)} passed")
sys.exit(0 if passed == len(results) else 1)
