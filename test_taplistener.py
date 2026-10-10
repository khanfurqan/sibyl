"""Drive TapListener with synthetic key events. No keyboard needed.

The listener decides when a recording starts, and whether letting go sends it
(a hold), keeps listening (a tap) or throws it away (another key joined in).
Getting that wrong either loses words or sends something never meant for
Sibyl, so it gets tested properly.
"""
import sys, time, pathlib
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[0]))
import sibyl

CTRL_R, ALT_R, C_KEY, CTRL_L = 163, 165, 67, 162


class FakeKey:
    def __init__(self, vk, name="k"):
        self.vk = vk
        self.name = name
    def __repr__(self):
        return f"<{self.name}>"


PROMPT = frozenset({CTRL_R, ALT_R})
FLOW = frozenset({CTRL_R})


def run(sequence, max_hold=0.8):
    """sequence: list of ('down'|'up', vk) or ('sleep', seconds)."""
    fired = []
    listener = sibyl.TapListener(
        {PROMPT: lambda: fired.append("prompt"), FLOW: lambda: fired.append("flow")},
        max_hold=max_hold,
    )
    for action, arg in sequence:
        if action == "sleep":
            time.sleep(arg)
        elif action == "down":
            listener._on_press(FakeKey(arg))
        else:
            listener._on_release(FakeKey(arg))
    return fired


CASES = [
    ("lone Right Ctrl tap -> flow",
     [("down", CTRL_R), ("up", CTRL_R)], ["flow"]),

    ("Ctrl then Alt -> prompt",
     [("down", CTRL_R), ("down", ALT_R), ("up", ALT_R), ("up", CTRL_R)], ["prompt"]),

    ("Alt then Ctrl (opposite finger order) -> prompt",
     [("down", ALT_R), ("down", CTRL_R), ("up", CTRL_R), ("up", ALT_R)], ["prompt"]),

    ("Ctrl+Alt, released Ctrl first -> prompt",
     [("down", CTRL_R), ("down", ALT_R), ("up", CTRL_R), ("up", ALT_R)], ["prompt"]),

    ("Ctrl+C must NOT fire",
     [("down", CTRL_R), ("down", C_KEY), ("up", C_KEY), ("up", CTRL_R)], []),

    ("lone Right Alt must NOT fire (it is not a binding on its own)",
     [("down", ALT_R), ("up", ALT_R)], []),

    ("Ctrl+Alt+C must NOT fire",
     [("down", CTRL_R), ("down", ALT_R), ("down", C_KEY),
      ("up", C_KEY), ("up", ALT_R), ("up", CTRL_R)], []),

    ("held too long must NOT fire",
     [("down", CTRL_R), ("sleep", 0.35), ("up", CTRL_R)], []),

    ("phantom Left Ctrl from AltGr must NOT turn a lone Alt into flow",
     [("down", CTRL_L), ("down", ALT_R), ("up", ALT_R), ("up", CTRL_L)], []),

    ("two taps in a row -> flow twice (state resets cleanly)",
     [("down", CTRL_R), ("up", CTRL_R), ("down", CTRL_R), ("up", CTRL_R)],
     ["flow", "flow"]),

    ("prompt then flow, back to back",
     [("down", CTRL_R), ("down", ALT_R), ("up", ALT_R), ("up", CTRL_R),
      ("down", CTRL_R), ("up", CTRL_R)], ["prompt", "flow"]),
]

# The cases above use two bindings to exercise the general gesture logic. The
# app itself has one binding since 2026-09-11; the hold cases below use it.

CHORD = frozenset({CTRL_R, ALT_R})


def run_hold(sequence, max_hold=0.3, stop_after=None):
    """Hold-to-talk events, in order: down (recording starts), tap (quick
    release: hands-free), hold (long release: send), abort (another key
    joined in: discard). stop_after cuts the sequence short, to see what has
    fired before the keys come back up."""
    events = []
    listener = sibyl.TapListener({CHORD: lambda: events.append("tap")},
                                 max_hold=max_hold)
    listener.on_chord_down = lambda g: events.append("down")
    listener.on_hold_release = lambda g: events.append("hold")
    listener.on_abort = lambda g: events.append("abort")
    for action, arg in sequence[:stop_after]:
        if action == "sleep":
            time.sleep(arg)
        elif action == "down":
            listener._on_press(FakeKey(arg))
        else:
            listener._on_release(FakeKey(arg))
    return events


HOLD_CASES = [
    ("recording starts the instant both keys are down, before any release",
     [("down", CTRL_R), ("down", ALT_R), ("up", ALT_R), ("up", CTRL_R)], ["down"], 2),

    ("a quick tap of the chord -> start, then hands-free",
     [("down", CTRL_R), ("down", ALT_R), ("up", ALT_R), ("up", CTRL_R)],
     ["down", "tap"], None),

    ("held past the threshold -> start, then send on release",
     [("down", CTRL_R), ("down", ALT_R), ("sleep", 0.35), ("up", ALT_R), ("up", CTRL_R)],
     ["down", "hold"], None),

    ("held, opposite finger order -> start, then send",
     [("down", ALT_R), ("down", CTRL_R), ("sleep", 0.35), ("up", CTRL_R), ("up", ALT_R)],
     ["down", "hold"], None),

    ("Ctrl+Alt+C while holding -> start, then abort, never send",
     [("down", CTRL_R), ("down", ALT_R), ("down", C_KEY), ("up", C_KEY),
      ("up", ALT_R), ("up", CTRL_R)], ["down", "abort"], None),

    ("a long Ctrl+Alt+C is still an abort, not a send",
     [("down", CTRL_R), ("down", ALT_R), ("down", C_KEY), ("sleep", 0.35),
      ("up", C_KEY), ("up", ALT_R), ("up", CTRL_R)], ["down", "abort"], None),

    ("Ctrl+C with Right Ctrl alone -> nothing starts",
     [("down", CTRL_R), ("down", C_KEY), ("up", C_KEY), ("up", CTRL_R)], [], None),

    ("another key BEFORE the chord completes -> nothing starts",
     [("down", CTRL_R), ("down", C_KEY), ("down", ALT_R),
      ("up", ALT_R), ("up", C_KEY), ("up", CTRL_R)], [], None),

    ("lone Right Alt -> nothing starts",
     [("down", ALT_R), ("up", ALT_R)], [], None),

    ("two holds in a row -> each starts and sends once",
     [("down", CTRL_R), ("down", ALT_R), ("sleep", 0.35), ("up", ALT_R), ("up", CTRL_R),
      ("down", CTRL_R), ("down", ALT_R), ("sleep", 0.35), ("up", ALT_R), ("up", CTRL_R)],
     ["down", "hold", "down", "hold"], None),
]

failures = 0
total = len(CASES) + len(HOLD_CASES)
print("Tap gestures")
for name, seq, expected in CASES:
    hold = 0.3 if "too long" in name else 0.8
    got = run(seq, max_hold=hold)
    ok = got == expected
    failures += not ok
    print(f"  {'PASS' if ok else 'FAIL'}  {name}")
    if not ok:
        print(f"        expected {expected}, got {got}")

print("\nHold-to-talk")
for name, seq, expected, stop in HOLD_CASES:
    got = run_hold(seq, stop_after=stop)
    ok = got == expected
    failures += not ok
    print(f"  {'PASS' if ok else 'FAIL'}  {name}")
    if not ok:
        print(f"        expected {expected}, got {got}")

print(f"\n{total - failures}/{total} passed")
sys.exit(1 if failures else 0)
