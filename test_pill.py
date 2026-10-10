"""Ask Windows what the notch actually is. No microphone, no models.

`--test-pill` shows the notch by eye. This proves it by assertion, which is the
half that survives being run six months from now by someone who was not there.

The check that matters most is that the notch never becomes the foreground
window. Sibyl pastes back into whichever window was in front when you started
speaking; a status window that took focus would quietly *become* that window,
and the failure would look exactly like the focus bug that was already fixed.
Since roadmap step 5 the notch answers clicks, so this must hold for a click
too. That property is invisible in normal use right up until it costs you a
paragraph, so it is asserted rather than trusted.

Four parts:
  0. The arithmetic behind moving it, with no window: a drag told from a
     click, kept inside the screen, the panel above or below, the saved spot,
     and the microphone's colour on each disc.
  1. The notch on its own, driven with update() from the main thread: always
     on screen, where it sits, and its colour and word in every state.
  2. Real mouse clicks and drags through the real app, with a fake microphone
     and a fake paste. Your pointer moves for a few seconds and is put back. A grey
     backdrop belonging to this test sits under the notch, so a click that
     missed lands there and is counted, never in one of your windows.
  3. pystray detached and tkinter on main together, driven from a worker
     thread, which is the arrangement real captures actually run in.
"""
import atexit
import contextlib
import ctypes
import ctypes.wintypes as wt
import io
import os
import pathlib
import shutil
import subprocess
import sys
import tempfile
import threading
import time

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[0]))
import sibyl

# Where the notch remembers being dragged to, moved to a folder of this test's
# own before anything is built. Every notch below then starts at the default
# spot, over the backdrop, and a drag here never moves your real one.
temp_before = set(pathlib.Path(tempfile.gettempdir()).glob("sibyl_test_*"))
tmp = pathlib.Path(tempfile.mkdtemp(prefix="sibyl_test_"))
# Removed however the run stops early. The normal end removes it itself, since
# os._exit there skips this.
atexit.register(shutil.rmtree, tmp, True)
sibyl.NOTCH_SPOT_FILE = tmp / "notch.json"

GWL_EXSTYLE = -20
WS_EX_NOACTIVATE = 0x08000000
WS_EX_TOOLWINDOW = 0x00000080

user32 = ctypes.windll.user32
results = []


def check(name, ok, detail=""):
    print(f"  {'PASS' if ok else 'FAIL'}  {name}" + (f"   {detail}" if detail else ""))
    results.append(bool(ok))


def handle(window):
    """The real top-level window handle behind a Tk window."""
    hwnd = window.winfo_id()
    return user32.GetParent(hwnd) or hwnd


# Set if the notch, or its panel, is ever seen holding the foreground, at any
# instant during any pump. Sampled continuously rather than checked at the end,
# because a window that grabbed focus and gave it back would still have stolen
# your keystrokes.
notch_ever_foreground = False


def pump(pill, seconds):
    """Run the Tk loop from here rather than handing over the main thread, so
    assertions can run between pumps. update() processes the after() callbacks
    that _drain schedules, so the queue really is being drained.

    Also watches the foreground while it waits. The question is only ever
    whether the NOTCH took focus, never whether the foreground changed: other
    applications take focus all the time on a machine somebody is using, and an
    earlier version of this file failed intermittently because it could not tell
    those two things apart. The window it was blaming turned out to be the
    Claude desktop app.
    """
    global notch_ever_foreground
    end = time.time() + seconds
    while time.time() < end:
        pill.root.update()
        mine = {handle(pill.root)}
        if pill._panel is not None:
            mine.add(handle(pill._panel))
        if user32.GetForegroundWindow() in mine:
            notch_ever_foreground = True
        time.sleep(0.02)


def dot_colour(pill):
    """The colour actually drawn on the disc, read from the picture handed to
    Windows. Left of the middle, because the middle is the microphone."""
    h = pill._image.height
    return pill._image.getpixel((h // 2 - round(7 * pill._scale), h // 2))[:3]


def mic_colour(pill):
    """The colour drawn at the middle of the microphone's head."""
    h = pill._image.height
    return pill._image.getpixel((h // 2, h // 2 - round(2 * pill._scale)))[:3]


def close_to(rgb, other, tolerance=8):
    return all(abs(a - b) <= tolerance for a, b in zip(rgb, other))


def near(rgb, state, tolerance=8):
    """Is this the state's colour, give or take the smoothing?"""
    return all(abs(a - b) <= tolerance
               for a, b in zip(rgb, sibyl.STATE_COLOURS[state]))


def word(pill):
    """What the chip says, as drawn."""
    return pill._text


class RECT(ctypes.Structure):
    _fields_ = [("left", ctypes.c_long), ("top", ctypes.c_long),
                ("right", ctypes.c_long), ("bottom", ctypes.c_long)]


class POINT(ctypes.Structure):
    _fields_ = [("x", ctypes.c_long), ("y", ctypes.c_long)]


def window_rect(hwnd):
    rect = RECT()
    user32.GetWindowRect(hwnd, ctypes.byref(rect))
    return rect


def work_area():
    work = RECT()
    user32.SystemParametersInfoW(0x0030, 0, ctypes.byref(work), 0)  # SPI_GETWORKAREA
    return work


def off_centre(hwnd):
    rect, work = window_rect(hwnd), work_area()
    return (rect.left + rect.right) // 2 - (work.left + work.right) // 2


# ---------------------------------------------------------------------------
# Part 0: the arithmetic behind moving it, no window
# ---------------------------------------------------------------------------

print("Moving the notch, worked out without a window")

limit = (4, 4)
check("a press that moves 4 px or less is still a click",
      not any(sibyl._past_drag_limit(dx, dy, limit)
              for dx, dy in [(0, 0), (4, 0), (0, -4), (-4, 4), (3, -2)]))
check("a press that moves further either way is a drag",
      all(sibyl._past_drag_limit(dx, dy, limit)
          for dx, dy in [(5, 0), (-5, 0), (0, 5), (0, -5), (40, 40)]))

# A taskbar on the left, so the usable area does not start at 0.
area = (60, 0, 1920, 1140)


def inside(x, y, w, h, area):
    return area[0] <= x and x + w <= area[2] and area[1] <= y and y + h <= area[3]


h = 35   # the chip's height at 125%
far = [(-900, 500), (5000, 500), (900, -900), (900, 5000), (-900, -900), (5000, 5000)]
escaped = [(w, a) for w in range(h, 287) for a in far
           if not inside(*sibyl._keep_inside(a, (w, h), area), w, h, area)]
check("a chip of every width, up to 286 px, stays inside all four edges",
      escaped == [], f"{len(escaped)} escaped, first {escaped[:1]}")
x, y = sibyl._keep_inside((900, 500), (200, h), area)
check("away from the edges it is centred on where it rests",
      x == 800 and y in (482, 483), f"{x},{y}")
check("near an edge it grows toward the side with room",
      sibyl._keep_inside((60 + h / 2, 500), (200, h), area)[0] == 60
      and sibyl._keep_inside((1920 - h / 2, 500), (200, h), area)[0] == 1720)

panel = (450, 200)
x, y = sibyl._panel_spot((900, 1100, 35, 35), panel, 10, area)
check("the panel opens above the notch when there is room",
      y + 200 <= 1100 - 10 and inside(x, y, *panel, area), f"{x},{y}")
x, y = sibyl._panel_spot((900, 20, 35, 35), panel, 10, area)
check("and below it when there is not",
      y >= 20 + 35 + 10 and inside(x, y, *panel, area), f"{x},{y}")
x1, _ = sibyl._panel_spot((62, 1100, 35, 35), panel, 10, area)
x2, _ = sibyl._panel_spot((1880, 1100, 35, 35), panel, 10, area)
check("and stays inside the screen at the left and right edges",
      x1 == 60 and x2 == 1920 - 450, f"{x1}, {x2}")

def printed(fn, *args):
    """What fn printed, and whether it raised instead of reporting."""
    out = io.StringIO()
    try:
        with contextlib.redirect_stdout(out):
            fn(*args)
    except Exception as exc:
        return f"RAISED {type(exc).__name__}"
    return out.getvalue()


spot = tmp / "spot" / "notch.json"   # a folder that does not exist yet
sibyl.save_spot(spot, (0.25, 0.75))
check("a dropped spot is saved, and read back exactly",
      sibyl.load_spot(spot) == (0.25, 0.75), str(sibyl.load_spot(spot)))

# Found by review, 2026-10-05: the file was written in place, so a save cut
# short (a full disk, Sibyl killed mid-write) left half a file and lost the
# last good spot. A failure part-way is made here by a write that stops halfway.
real_write = pathlib.Path.write_text


def half_then_fail(self, text, *args, **kwargs):
    real_write(self, text[:len(text) // 2], *args, **kwargs)
    raise OSError("disk full")


pathlib.Path.write_text = half_then_fail
try:
    printed(sibyl.save_spot, spot, (0.6, 0.6))
finally:
    pathlib.Path.write_text = real_write
check("a save cut short keeps the last good spot",
      sibyl.load_spot(spot) == (0.25, 0.75), str(sibyl.load_spot(spot)))

broken, unreported = [], []
for text in ["", "not json", "[0.5, 0.5]", '{"x": 0.5}', '{"x": "a", "y": 0.5}',
             '{"x": 1.5, "y": 0.5}', '{"x": -0.1, "y": 0.5}', '{"x": NaN, "y": 0.5}']:
    spot.write_text(text, encoding="utf-8")
    try:
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            if sibyl.load_spot(spot) is not None:
                broken.append(text)
        if "[pill]" not in out.getvalue():
            unreported.append(text)
    except Exception as exc:
        broken.append(f"{text}: {type(exc).__name__}")
check("an unreadable or out-of-range file means the default spot, never an error",
      broken == [], str(broken))
# Also found by review: a damaged file looked exactly like a first run.
check("and each one says so in the log", unreported == [], str(unreported))
sibyl.save_spot(spot, None)
check("forgetting the spot removes the file, and a missing file is the default, "
      "without a word", not spot.exists() and sibyl.load_spot(spot) is None
      and printed(sibyl.load_spot, spot) == "")

# A failed save and a failed forget used to print the same line, so a "Move
# back to the bottom" that could not delete the file read as a failed save,
# and the notch came back to the old spot after a restart.
blocked = tmp / "blocked.json"
blocked.mkdir()   # a folder where the file should be: neither can succeed
said = printed(sibyl.save_spot, blocked, (0.5, 0.5)), printed(sibyl.save_spot, blocked, None)
check("a save that fails says it could not save, and a forget that it could not forget",
      "could not save" in said[0] and "could not forget" in said[1], str(said))
# The error's own text names the file, and the file's path names your Windows
# account, in a log you might paste into a public issue. The folder's name is
# searched for, not its path: the error doubles every backslash.
said += (printed(sibyl.load_spot, blocked),)   # a file that cannot be read
check("and neither they nor a failed read say where the file is",
      "could not read" in said[2] and tmp.name not in "".join(said), str(said))

wrong = []
for state, disc in sibyl.STATE_COLOURS.items():
    mic = sibyl._mic_colour(disc)
    dark = state in ("loading", "thinking", "done")
    if sibyl._contrast(mic, disc) < 3 or (mic == sibyl.NOTCH_BG) != dark:
        wrong.append(f"{state} {sibyl._contrast(mic, disc):.1f}:1")
check("the microphone is dark on yellow and green, light on red and slate, "
      "and at least 3:1 on every disc", wrong == [], str(wrong))

# ---------------------------------------------------------------------------
# Part 1: the notch on its own
# ---------------------------------------------------------------------------

print("The notch on its own")

# Found by review, 2026-10-05: nothing proved that a spot saved before a
# restart is where the notch comes back. So this notch starts with one.
sibyl.save_spot(sibyl.NOTCH_SPOT_FILE, (0.25, 0.4))
pill = sibyl.Pill()
check("build() succeeds", pill.build())
if pill.root is None:
    print("\nCannot continue without a window.")
    sys.exit(1)
hwnd = handle(pill.root)
check("did not take the foreground as it was built",
      user32.GetForegroundWindow() != hwnd)

pump(pill, 0.4)
rect, work = window_rect(hwnd), work_area()
want = (work.left + 0.25 * (work.right - work.left),
        work.top + 0.4 * (work.bottom - work.top))
got = ((rect.left + rect.right) / 2, (rect.top + rect.bottom) / 2)
check("after a restart it comes back where it was dropped",
      abs(got[0] - want[0]) <= 2 and abs(got[1] - want[1]) <= 2,
      f"at {got}, dropped at {want}")
sibyl.save_spot(sibyl.NOTCH_SPOT_FILE, None)
pill._spot, pill._drawn = None, None   # back to the default for what follows
pump(pill, 0.3)
check("on screen at once, as the resting dot, before anything has happened",
      bool(user32.IsWindowVisible(hwnd)))
style = user32.GetWindowLongW(hwnd, GWL_EXSTYLE)
check("WS_EX_NOACTIVATE is set", bool(style & WS_EX_NOACTIVATE),
      f"exstyle=0x{style & 0xffffffff:08x}")
check("WS_EX_TOOLWINDOW is set, so it stays out of Alt-Tab",
      bool(style & WS_EX_TOOLWINDOW))
check("at rest it is a grey circle with no word",
      word(pill) == "" and near(dot_colour(pill), "idle")
      and pill.root.winfo_width() == pill.root.winfo_height(),
      f"{pill.root.winfo_width()}x{pill.root.winfo_height()}, {dot_colour(pill)}")
check("with a light microphone drawn on the grey disc",
      close_to(mic_colour(pill), sibyl.NOTCH_FG, 24), str(mic_colour(pill)))
# Added 2026-09-14, after the notch was seen as pixelated. Drawn without
# smoothing, the picture has three see-through levels: the corners, the dark
# hairline and the solid chip. Smoothed, the edge pixels take many in between.
levels = sum(1 for count in pill._image.getchannel("A").histogram() if count)
check("its edges are smooth: many see-through levels, not just on and off",
      levels > 10, f"{levels} levels")

# -- where it actually is ---------------------------------------------------
# Added 2026-09-11, after the pill was found two thirds of the way up the screen
# instead of just above the taskbar. None of the checks above could see that:
# they ask whether the window exists and behaves, never where it is.
#
# The cause was an ordering accident. pyautogui switches the whole process into
# real-pixel mode the moment it is imported, and the real app imports it at the
# end of boot, AFTER the window has been placed. On a 125% screen the same
# numbers then point somewhere else. So this imports it here, after build,
# exactly as the app does, and only then asks where the notch is.
import pyautogui  # noqa: E402,F401  (the import is the point: it flips the mode)

pump(pill, 0.3)
rect, work = window_rect(hwnd), work_area()
gap = work.bottom - rect.bottom
check("sits just above the taskbar, after the paste helper has loaded",
      0 <= gap <= 40, f"{gap}px between the notch and the top of the taskbar")
check("is centred horizontally", abs(off_centre(hwnd)) <= 2,
      f"{off_centre(hwnd):+d}px from the centre of the screen")

# -- every state: its colour and its word -------------------------------------

for state, text, name in [("loading", "Waking up", "yellow"),
                          ("recording", "Listening", "red"),
                          ("done", "Done", "green")]:
    pill.set_state(state)
    pump(pill, 0.3)
    mic = sibyl._mic_colour(sibyl.STATE_COLOURS[state])
    check(f"{state}: a {name} disc, its microphone, and the word {text!r}",
          word(pill) == text and near(dot_colour(pill), state)
          and close_to(mic_colour(pill), mic, 24),
          f"{word(pill)!r}, {dot_colour(pill)}, microphone {mic_colour(pill)}")

pill.set_state("recording")
pump(pill, 0.3)
check("while listening, the tick and the cross are shown",
      bool(pill._canvas.find_withtag("send"))
      and bool(pill._canvas.find_withtag("cancel")))
# E3: all seven reviewers found them 22 px across with 4 px between, so a click
# a little off missed or hit the other one. The drawing barely changes; what
# answers a click grows. 24 px is the accessibility minimum for a target.
s, h = pill._scale, pill._lay["h"]
send_area = pill._canvas.coords(pill._canvas.find_withtag("send")[0])
cancel_area = pill._canvas.coords(pill._canvas.find_withtag("cancel")[0])
check("the tick and the cross each answer a click at least 24 px wide",
      all(x2 - x1 >= 24 * s for x1, _, x2, _ in (send_area, cancel_area)),
      f"{send_area} {cancel_area}")
check("and the whole height of the chip",
      all(y1 <= 0 and y2 >= h for _, y1, _, y2 in (send_area, cancel_area)),
      f"{send_area} {cancel_area}, chip {h}px")
check("and the two areas never overlap", send_area[2] <= cancel_area[0],
      f"{send_area} {cancel_area}")
lay = pill._lay
check("the drawn tick and cross have more room between them than the old 4 px",
      lay["cancel"] - lay["send"] - 2 * lay["r"] >= 8 * s,
      f"{(lay['cancel'] - lay['send'] - 2 * lay['r']) / s:.0f}px")
check("the chip stays centred as it grows", abs(off_centre(hwnd)) <= 2,
      f"{off_centre(hwnd):+d}px")

# Dropped against an edge, the listening chip, the widest there is, must grow
# toward the room rather than off the screen.
outside = []
for spot in [(1.0, 0.5), (0.0, 0.5), (0.5, 0.0), (0.5, 1.0)]:
    pill._spot, pill._drawn = spot, None
    pump(pill, 0.2)
    rect, work = window_rect(hwnd), work_area()
    if not (work.left <= rect.left and rect.right <= work.right
            and work.top <= rect.top and rect.bottom <= work.bottom):
        outside.append(f"{spot}: {rect.left},{rect.top},{rect.right},{rect.bottom}")
check("dropped against any edge, the listening chip stays on the screen",
      outside == [], str(outside))

# Decided 2026-10-05: at a spot you chose, the microphone stays where you
# dropped it and the chip grows to its right. It grew both ways, which moved
# the microphone about 77 px left at every click.
pill.set_state("idle")
pill._spot, pill._drawn = (0.5, 0.5), None
pump(pill, 0.3)
resting = window_rect(hwnd).left
pill.set_state("recording")
pump(pill, 0.3)
listening = window_rect(hwnd).left
check("at a dropped spot the microphone stays put as the chip grows",
      abs(listening - resting) <= 1, f"the microphone moved {listening - resting:+d}px")

# Found by review, 2026-10-05: a drag measured from where the notch rests, not
# from where it is drawn, so a wide chip squeezed against the right edge sat
# still for the first 74 px of a drag and then trailed the pointer.
pill._spot, pill._drawn = (1.0, 0.5), None
pump(pill, 0.3)
before = window_rect(hwnd)


class At:
    def __init__(self, x, y):
        self.x_root, self.y_root = x, y


pill._grab(At(before.left + h // 2, before.top + h // 2))
pill._drag(At(before.left + h // 2 - 40, before.top + h // 2))
pump(pill, 0.1)
pill._grip = None   # let go without saving: the app below must start at the default
moved = window_rect(hwnd).left - before.left
check("a wide chip dragged off the right edge follows the pointer at once",
      abs(moved + 40) <= 1, f"moved {moved:+d}px for a 40 px drag")

# Found by review, 2026-10-05: the drag kept the stored spot on screen as if
# it were the disc, so a wide chip pushed against the right edge stopped there
# while the stored spot went on with the pointer. When the chip shrank, the
# microphone reappeared up to a chip's width from where it was last seen.
pill._spot, pill._drawn = (1.0, 0.5), None
pump(pill, 0.3)
before = window_rect(hwnd)
pill._grab(At(before.left + h // 2, before.top + h // 2))
pill._drag(At(before.left + h // 2 + 120, before.top + h // 2))
pump(pill, 0.1)
pill._grip = None
drawn = window_rect(hwnd).left
pill.set_state("idle")
pump(pill, 0.4)
check("a wide chip pushed against the right edge leaves the microphone where it was",
      abs(window_rect(hwnd).left - drawn) <= 1,
      f"the microphone moved {window_rect(hwnd).left - drawn:+d}px as the chip shrank")
pill.set_state("recording")
pill._spot, pill._drawn = None, None
pump(pill, 0.2)

pill.set_state("thinking")
pump(pill, 1.3)
check("working counts the seconds, and shows none of your words",
      word(pill) == "Working 1s", repr(word(pill)))
check("and the tick and the cross are gone",
      not pill._canvas.find_withtag("send"))

pill.set_state("failed", "Microphone failed")
pump(pill, 0.3)
check("a failure is red and says what failed",
      word(pill) == "Microphone failed" and near(dot_colour(pill), "failed"),
      f"{word(pill)!r}, {dot_colour(pill)}")

pill.set_state("done", "Done, your words")
pump(pill, 0.3)
check("a paste of your own words says so", word(pill) == "Done, your words",
      repr(word(pill)))
pump(pill, sibyl.NOTCH_DONE_MS / 1000 + 0.5)
check("then the chip shrinks back to the grey dot",
      word(pill) == "" and near(dot_colour(pill), "idle"),
      f"{word(pill)!r}, {dot_colour(pill)}")
check("and the dot stays on screen", bool(user32.IsWindowVisible(hwnd)))

# Found 2026-10-04: a click now counts when the button comes up, and a chip
# that grows or shrinks rebuilds the shapes Tk tells clicks apart with. A
# click on the Done chip just as it shrank pressed one shape and let go after
# it was gone, so the release went nowhere and the click was lost.
clicks = []
pill.on_click = lambda: clicks.append("click")
pill.set_state("done")
pump(pill, 0.3)
x0, y0 = pill.root.winfo_rootx() + h // 2, pill.root.winfo_rooty() + h // 2


class Press:
    x_root, y_root = x0, y0


pill._grab(Press)
pill.set_state("idle")   # the chip shrinks under the held button
pump(pill, 0.3)
pill._canvas.event_generate("<ButtonRelease-1>", x=h // 2, y=h // 2,
                            rootx=x0, rooty=y0)
pump(pill, 0.2)
check("a click still counts when the chip shrinks under the button",
      clicks == ["click"], str(clicks))
check("the notch never held the foreground, at any instant",
      not notch_ever_foreground,
      "" if not notch_ever_foreground else
      "THE NOTCH TOOK FOCUS. This is the failure that breaks the paste target.")

pill.close()
check("closes without leaving a stray callback behind", pill.root is None)

# ---------------------------------------------------------------------------
# Part 2: real clicks, through the real app
# ---------------------------------------------------------------------------

print("\nReal clicks on the notch")


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


sibyl.beep = lambda *_a, **_k: None
pasted = []
sibyl.inject = lambda text, target: pasted.append((text, target)) or True

log = []
app = sibyl.Sibyl()
app.recorder = FakeRecorder(log)


def fake_finish():
    """The send, minus the transcription and the model."""
    app.recorder.stop()
    log.append("SENT")
    app._set_state("done")
    with app._lock:
        app._busy = False


app._finish_capture = fake_finish
app._last_output = "The text from the last dictation."

# The backdrop: a grey window under where the notch will sit, so a click that
# misses lands there and is counted, never in one of your windows. It belongs
# to another process, started first so the notch is built on top of it: a
# window of this test's own kept coming out above the notch. Another process is
# also what a hand's pointer really crosses from. Given the no-activate style,
# so it never takes the foreground either.
BACKDROP = r'''
import ctypes, sys, threading, time, tkinter as tk
ctypes.windll.user32.SetProcessDPIAware()
u = ctypes.windll.user32


class RECT(ctypes.Structure):
    _fields_ = [("left", ctypes.c_long), ("top", ctypes.c_long),
                ("right", ctypes.c_long), ("bottom", ctypes.c_long)]


work = RECT()
u.SystemParametersInfoW(0x0030, 0, ctypes.byref(work), 0)
r = tk.Tk()
r.overrideredirect(True)
r.attributes("-topmost", True)
r.configure(bg="#3a3a3a")
r.geometry(f"700x420+{(work.left + work.right) // 2 - 350}+{work.bottom - 420}")
missed = []
r.bind("<ButtonPress>", lambda e: missed.append(e.num))
r.update_idletasks()
top = u.GetParent(r.winfo_id()) or r.winfo_id()
u.SetWindowLongW(top, -20, u.GetWindowLongW(top, -20) | 0x08000000 | 0x80)
r.withdraw()
r.deiconify()
r.update()
stop = threading.Event()
threading.Thread(target=lambda: (sys.stdin.readline(), stop.set()), daemon=True).start()
print("up", flush=True)
while not stop.is_set():
    r.update()
    time.sleep(0.02)
print(len(missed), flush=True)
'''
backdrop = subprocess.Popen([sys.executable, "-c", BACKDROP], text=True,
                            stdin=subprocess.PIPE, stdout=subprocess.PIPE)
backdrop.stdout.readline()   # "up": its window is on screen
time.sleep(0.3)

check("the notch builds inside the app", app.pill.build())
pill = app.pill
notch_ever_foreground = False
pump(pill, 0.4)
hwnd = handle(pill.root)


class MOUSEINPUT(ctypes.Structure):
    _fields_ = [("dx", wt.LONG), ("dy", wt.LONG), ("mouseData", wt.DWORD),
                ("dwFlags", wt.DWORD), ("time", wt.DWORD),
                ("dwExtraInfo", ctypes.POINTER(ctypes.c_ulong))]


class INPUT(ctypes.Structure):
    _fields_ = [("type", wt.DWORD), ("mi", MOUSEINPUT)]


MOVE, LEFT_DOWN, LEFT_UP, RIGHT_DOWN, RIGHT_UP, ABSOLUTE = (
    0x0001, 0x0002, 0x0004, 0x0008, 0x0010, 0x8000)
SCREEN_W, SCREEN_H = user32.GetSystemMetrics(0), user32.GetSystemMetrics(1)


def mouse(flags, x, y):
    """Real mouse input at a point on the screen, as a hand makes it."""
    inp = INPUT(0, MOUSEINPUT(x * 65535 // (SCREEN_W - 1),
                              y * 65535 // (SCREEN_H - 1),
                              0, flags | MOVE | ABSOLUTE, 0, None))
    user32.SendInput(1, ctypes.byref(inp), ctypes.sizeof(INPUT))


def click(pill, x, y, right=False):
    """Glide in over the backdrop and click. Real input through SendInput, in
    the form that reached a test notch 10 times out of 10 on 2026-09-13."""
    for step in range(6):
        mouse(0, x - 100 + 20 * step, y - 50 + 10 * step)
        pump(pill, 0.02)
    down, up = (RIGHT_DOWN, RIGHT_UP) if right else (LEFT_DOWN, LEFT_UP)
    mouse(down, x, y)
    mouse(up, x, y)
    pump(pill, 0.4)


def drag(pill, x, y, dx, dy):
    """Glide in, press, move by (dx, dy) in steps of about 10 px, let go.
    A mouse reports about 8 px a step at an ordinary pace. Steps of 25 px let
    the pointer get a disc's width ahead of the notch whenever this machine
    paused, and the backdrop under it then counted a press (2026-10-04)."""
    for step in range(6):
        mouse(0, x - 100 + 20 * step, y - 50 + 10 * step)
        pump(pill, 0.02)
    mouse(LEFT_DOWN, x, y)
    pump(pill, 0.05)
    steps = max(abs(dx), abs(dy)) // 10
    for step in range(1, steps + 1):
        mouse(0, x + dx * step // steps, y + dy * step // steps)
        pump(pill, 0.015)
    mouse(LEFT_UP, x + dx, y + dy)
    pump(pill, 0.4)


def centre(pill, tag):
    x1, y1, x2, y2 = pill._canvas.bbox(tag)
    return (pill.root.winfo_rootx() + (x1 + x2) // 2,
            pill.root.winfo_rooty() + (y1 + y2) // 2)


def settle(app, pill, timeout=2.0):
    """The send, the cancel and the paste run on worker threads. Wait for them,
    pumping, so their state changes reach the notch."""
    deadline = time.time() + timeout
    while app._busy and time.time() < deadline:
        pump(pill, 0.05)
    pump(pill, 0.2)


under = user32.WindowFromPoint(POINT(*centre(pill, "dot")))
check("the notch is on top of the test's backdrop",
      under == pill._canvas.winfo_id(), f"window under the dot: {under}")

front = user32.GetForegroundWindow()
saved = POINT()
user32.GetCursorPos(ctypes.byref(saved))
try:
    click(pill, *centre(pill, "dot"))
    check("a click on the dot starts listening", app.recorder.is_recording, str(log))
    check("hands-free: a click has no hold, so it keeps listening",
          app._hands_free and "SENT" not in log, str(log))
    check("the paste target is the window you were in, never the notch",
          app._target_hwnd == front and app._target_hwnd != hwnd,
          repr(sibyl.window_title(app._target_hwnd)))
    check("the notch turns red and says Listening",
          word(pill) == "Listening" and near(dot_colour(pill), "recording"),
          repr(word(pill)))

    click(pill, *centre(pill, "send"))
    settle(app, pill)
    check("the tick sends, once",
          log.count("SENT") == 1 and not app.recorder.is_recording, str(log))

    click(pill, *centre(pill, "dot"))
    check("a click on the green Done chip starts the next one",
          app.recorder.is_recording, str(log))
    click(pill, *centre(pill, "cancel"))
    settle(app, pill)
    check("the cross throws it away: nothing sent, the recording deleted",
          log.count("SENT") == 1 and "wav deleted" in log
          and not app.recorder.is_recording, str(log))
    check("and the chip shrinks back to the dot", word(pill) == "", repr(word(pill)))

    click(pill, *centre(pill, "dot"), right=True)
    shown = pill._panel_text if pill._panel else None
    check("a right-click opens the last text", shown == app._last_output, repr(shown))
    corner = pill._panel_image.getpixel((0, 0))[3] if pill._panel else None
    check("the panel has round corners: its corner pixel is see-through",
          corner == 0, f"see-through level {corner}")
    if pill._panel is not None:
        x1, y1, x2, y2 = pill._panel_canvas.bbox("paste")
        click(pill, pill._panel.winfo_rootx() + (x1 + x2) // 2,
              pill._panel.winfo_rooty() + (y1 + y2) // 2)
        settle(app, pill)
    check("Paste again pastes it with no target window, so it lands where you are",
          pasted == [(app._last_output, None)], str(pasted))
    check("and the panel closes", pill._panel is None)

    check("Quit Sibyl is wired to Sibyl's own quit", pill.on_quit == app.quit)
    quits = []
    pill.on_quit = lambda: quits.append(True)   # the real one ends the process
    click(pill, *centre(pill, "dot"), right=True)
    if pill._panel is not None:
        x1, y1, x2, y2 = pill._panel_canvas.bbox("quit")
        click(pill, pill._panel.winfo_rootx() + (x1 + x2) // 2,
              pill._panel.winfo_rooty() + (y1 + y2) // 2)
    check("the panel's Quit Sibyl quits, and the panel closes",
          quits == [True] and pill._panel is None, str(quits))

    # -- dragging it --------------------------------------------------------
    # Paste again left the Done chip up, and it shrinks back to the disc after
    # NOTCH_DONE_MS. A click aimed at the chip as it shrank landed where the
    # disc had been (2026-10-04), so the drags start from the disc at rest:
    # drawn at its own width, not merely marked idle, which comes up to 60 ms
    # before the shrinking starts.
    deadline = time.time() + sibyl.NOTCH_DONE_MS / 1000 + 1
    while pill._shown_w != pill._lay["h"] and time.time() < deadline:
        pump(pill, 0.05)
    before, starts = window_rect(hwnd), log.count("start")
    drag(pill, *centre(pill, "dot"), -200, -150)
    after = window_rect(hwnd)
    moved = (after.left - before.left, after.top - before.top)
    check("a drag moves the notch with the pointer",
          abs(moved[0] + 200) <= 3 and abs(moved[1] + 150) <= 3, f"moved {moved}")
    check("and starts nothing", log.count("start") == starts
          and not app.recorder.is_recording, str(log))
    saved_spot = sibyl.load_spot(sibyl.NOTCH_SPOT_FILE)
    check("the spot is saved when it is dropped",
          saved_spot is not None and saved_spot == pill._spot, str(saved_spot))

    mark = len(log)
    click(pill, *centre(pill, "dot"))
    check("after a drag, a click still starts listening",
          app.recorder.is_recording, str(log))
    before = window_rect(hwnd)
    drag(pill, *centre(pill, "dot"), 100, -60)
    after = window_rect(hwnd)
    check("the listening chip drags too, and keeps listening",
          abs(after.left - before.left - 100) <= 3 and app.recorder.is_recording
          and "SENT" not in log[mark:], str(log))
    click(pill, *centre(pill, "cancel"))
    settle(app, pill)

    click(pill, *centre(pill, "dot"), right=True)
    if pill._panel is not None and pill._panel_canvas.find_withtag("home"):
        x1, y1, x2, y2 = pill._panel_canvas.bbox("home")
        click(pill, pill._panel.winfo_rootx() + (x1 + x2) // 2,
              pill._panel.winfo_rooty() + (y1 + y2) // 2)
    rect, work = window_rect(hwnd), work_area()
    check("Move back to the bottom puts it back, centred above the taskbar",
          abs(off_centre(hwnd)) <= 2 and 0 <= work.bottom - rect.bottom <= 40
          and pill._panel is None, f"{off_centre(hwnd):+d}px, "
          f"{work.bottom - rect.bottom}px above the taskbar")
    check("and forgets the saved spot", not sibyl.NOTCH_SPOT_FILE.exists()
          and pill._spot is None)
except Exception as exc:
    check("the clicks ran to the end", False, f"{type(exc).__name__}: {exc}")
finally:
    user32.SetCursorPos(saved.x, saved.y)

missed = backdrop.communicate("stop\n", timeout=10)[0].strip()
check("no click missed the notch", missed == "0",
      f"{missed} landed on the backdrop" if missed != "0" else "")
check("the notch never held the foreground, not even when clicked",
      not notch_ever_foreground,
      "" if not notch_ever_foreground else
      "THE NOTCH TOOK FOCUS. This is the failure that breaks the paste target.")
now_front = user32.GetForegroundWindow()
if now_front != front:
    # Information, not a failure. Somebody else's window is not the notch's fault.
    print(f"        (note: {sibyl.window_title(now_front)!r} holds the foreground "
          f"now. Not the notch, and not a failure.)")
pill.close()

# ---------------------------------------------------------------------------
# Part 3: both event loops at once, which is how it really runs
# ---------------------------------------------------------------------------

print("\nBoth event loops together")

import pystray  # noqa: E402

app = sibyl.Sibyl()
app.tray = pystray.Icon("SibylTest", sibyl._make_icon_image("loading"),
                        "Sibyl test")
check("notch builds on the main thread", app.pill.build())
# What Sibyl.run does once the notch is built.
app._tray_shows_state = False
logo = app.tray.icon = sibyl._brand_icon_image()


def worker():
    """Stands in for _boot and _finish_capture. Every real state change comes
    from a background thread, so every assertion here does too."""
    time.sleep(1.0)
    check("tray is running detached", bool(app.tray.visible))

    app._set_state("recording")
    time.sleep(0.5)
    app._set_state("thinking", "cleaning up (qwen2.5:3b)")
    time.sleep(1.3)

    check("notch visible, driven entirely from a worker thread",
          bool(user32.IsWindowVisible(handle(app.pill.root))))
    check("the worker's state reached the notch",
          word(app.pill).startswith("Working"), repr(word(app.pill)))
    check("one _set_state call updated the tray too",
          "cleaning up" in (app.tray.title or ""), repr(app.tray.title))
    check("with the notch showing, the tray keeps the logo, not a coloured dot",
          app.tray.icon is logo, str(app.tray.icon))
    check("the notch still never held the foreground",
          user32.GetForegroundWindow() != handle(app.pill.root))

    app._set_state("done", "sent 517 chars to Terminal in 12.3s")
    time.sleep(0.5)
    check("both loops still alive at the end",
          bool(app.tray.visible) and app.pill.root is not None)

    shutil.rmtree(tmp, ignore_errors=True)   # if it fails, the check below says so
    left = sorted(p.name for p in set(pathlib.Path(tempfile.gettempdir())
                                      .glob("sibyl_test_*")) - temp_before)
    check("this run leaves nothing behind in the temp folder", left == [], str(left))

    passed = sum(results)
    print(f"\n{passed}/{len(results)} passed")
    # os._exit because Tk's mainloop owns the main thread and cannot be stopped
    # cleanly from here. It skips the buffer flush, hence the explicit one.
    sys.stdout.flush()
    os._exit(0 if passed == len(results) else 1)


threading.Thread(target=worker, daemon=True).start()
app.tray.run_detached()
app.pill.loop()
