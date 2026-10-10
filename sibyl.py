"""
Sibyl - an open-source, locally hosted, privacy-first clean-up and formatting
tool for what you dictate.

Hold a key, speak, let go. Sibyl transcribes what you said, cleans it up into
what you meant to write, and pastes it into the window you were already in.
No dictation ever leaves this computer: the speech model and the language model
both run here, and while Sibyl runs, its only network call is to Ollama on
localhost. Setup downloads the two models once: the speech model from Hugging
Face on first run, the language model through `ollama pull`.

One pipeline:

    audio -> speech to text -> spelling fixes -> clean-up -> check -> paste

Run `python sibyl.py --help` for the staged self-tests. Each stage runs alone,
so a failure names its own layer instead of just saying "it didn't work".
"""

from __future__ import annotations

import argparse
import ctypes
import json
import logging
import os
import platform
import queue
import re
import subprocess
import sys
import tempfile
import threading
import time
import traceback
import unicodedata
import urllib.parse
import warnings
from pathlib import Path

# ---------------------------------------------------------------------------
# 1. CONFIG
# ---------------------------------------------------------------------------

from dotenv import load_dotenv

load_dotenv()

IS_MAC = platform.system() == "Darwin"

# -- Ollama ----------------------------------------------------------------
# Written out, never "localhost": Windows resolves that to IPv6 (::1) first,
# Ollama listens on IPv4 alone, and Windows retries for about two seconds
# before falling back. Measured 2026-09-19: 2.07 s a call against 0.03 s, on
# every clean-up, every warm-up and every check.
LOCAL_OLLAMA = "http://127.0.0.1:11434"


def ollama_address(setting: str | None) -> tuple[str, str]:
    """Where the clean-up goes, and why a setting was refused ("" if it was not).

    Always this computer: "nothing leaves the laptop" is true by code, not by
    default. The address is rebuilt from the port alone rather than passed on,
    so nothing can ride along in the part of it that was not checked. A refused
    setting leaves the address local and the clean-up switched off, so the
    refusal is seen rather than quietly worked around.
    """
    if not setting:
        return LOCAL_OLLAMA, ""
    try:
        parts = urllib.parse.urlsplit(setting.strip())
        port = parts.port   # raises on a port that cannot exist
    except ValueError:
        parts = None
    if (parts is None or parts.scheme != "http" or parts.hostname != "127.0.0.1"
            or parts.username is not None or parts.path not in ("", "/")
            or parts.query or parts.fragment):
        return LOCAL_OLLAMA, (f"SIBYL_OLLAMA_HOST is {setting!r}. Sibyl only talks "
                              f"to Ollama on this computer: write it as "
                              f"http://127.0.0.1:<port>, or leave it out")
    return f"http://127.0.0.1:{11434 if port is None else port}", ""


# Sibyl's own setting, never Ollama's OLLAMA_HOST: that one tells the Ollama
# server where to listen, and "0.0.0.0:11434" there (let other computers in)
# broke every clean-up when Sibyl read it too (review round 3, C1).
OLLAMA_HOST, OLLAMA_ADDRESS_PROBLEM = ollama_address(os.getenv("SIBYL_OLLAMA_HOST"))

# Passed to every call to Ollama. A proxy setting (common on work laptops, in
# the environment or in Windows' own settings) sends web traffic through
# another machine, and requests applies it even to 127.0.0.1: measured
# 2026-09-26, a call to Ollama went to the proxy. That would carry every
# dictation off the laptop. test_launch.py fails if a call leaves this out.
NO_PROXIES = {"http": None, "https": None}

# The one model, and the one job it does: clean up what was said. Chosen by
# measurement on 2026-09-11 with eval_cleanup.py, each model timed alone, three
# runs of ten cases:
#
#   qwen2.5:3b     9, 10, 10   4.7s   never dropped content
#   qwen2.5:1.5b   8,  8,  8   3.4s   dropped "walked to the station" every run
#   qwen3.5:0.8b   8,  8,  8   6.3s   kept a corrected-away budget every run
#
# The 3B costs 1.3s a capture over the 1.5B and is the only one that never
# loses what was said. Its memory came free when Prompt mode was cut.
CLEANUP_MODEL = os.getenv("CLEANUP_MODEL", "qwen2.5:3b")

# Hard ceiling on generated tokens. Ollama otherwise generates without limit,
# so a model that starts rambling turns a 5s wait into a minute. Measured
# output is ~65 tokens, so this is several times headroom: it stops a
# runaway, never truncates normal work.
MAX_TOKENS_CLEANUP = int(os.getenv("MAX_TOKENS_CLEANUP", "512"))

# Cold start: the first call of a session blocks while Ollama loads weights
# into RAM. Two tiers so a genuine hang is distinguishable from a normal wake.
OLLAMA_TIMEOUT_COLD = int(os.getenv("OLLAMA_TIMEOUT_COLD", "180"))
OLLAMA_TIMEOUT_WARM = int(os.getenv("OLLAMA_TIMEOUT_WARM", "120"))
# How long Sibyl waits at start for Ollama to answer before showing it red.
OLLAMA_WAIT_AT_START = int(os.getenv("OLLAMA_WAIT_AT_START", "60"))

# -- Speech to text --------------------------------------------------------
STT_BACKEND = os.getenv("STT_BACKEND", "faster-whisper")
STT_MODEL = os.getenv("STT_MODEL", "base.en")
STT_COMPUTE = os.getenv("STT_COMPUTE_TYPE", "int8")
STT_DEVICE = os.getenv("STT_DEVICE", "cpu")

# Names the recogniser is told to expect, because it cannot guess a spelling it
# has never heard. Chosen by measurement on 2026-09-20 with eval_stt.py, twelve
# recordings, four contenders, three runs each:
#
#   base.en                    10.0% word errors, 13% of names right, 0.88s
#   small.en                    7.7%              20%                 2.39s
#   base.en + initial_prompt    4.2%              80%                 0.91s
#   base.en + hotwords          4.2%              80%                 0.91s
#
# The hint more than halves the errors for 0.03s: "Chonzel Yise" became
# "Champs-Élysées", "CIOBAR" became "Siobhan", "the QN model" became "the Qwen
# model". small.en lost on speed (+1.5s) and memory (724 MB against 337).
#
# hotwords, not initial_prompt: the two tied on every recording, but Whisper
# hears in 30-second windows, and initial_prompt reaches only the first one
# before being pushed out by the growing transcript. A capture may run to
# MAX_RECORD_SECONDS, which is four windows.
#
# The risk of a hint is that the model writes a name nobody said. Measured over
# the same twelve recordings: none. Empty this to switch it off.
STT_HOTWORDS = os.getenv(
    "STT_HOTWORDS", "Sibyl, Qwen, Ollama, Champs-Élysées, Siobhan, Joaquin."
)

# -- Hotkey ----------------------------------------------------------------
# One key combination: Right Ctrl + Right Alt, held together, written with a
# '+'. It was Prompt mode's chord, kept when the product narrowed to one mode
# on 2026-09-11 because it is proven on this keyboard in both finger orders.
#
# Why two keys: tapping Alt on its own is how Windows opens a menu bar, so a
# lone Right Alt fought the OS. pynput reports that key as 'alt_gr', not
# 'alt_r', which once made a binding silently never match. Matching is by
# virtual-key code now, so both names work.
#
# Both keys are real modifiers, so a gesture only counts if the keys went down
# and back up together with nothing else in between. See TapListener.
#
# F9 recall was removed the same day: hard to remember. Sending the last
# output again moves to the notch, and the tray keeps "Copy last output again".
HOTKEY = os.getenv("HOTKEY", "ctrl_r+alt_r")
# The boundary between a tap and a hold, in seconds. Release the keys sooner
# and it was a tap: hands-free, keep listening. Later and it was a hold: send.
# Was 0.8 when a long press was simply ignored; lowered to 0.5 on 2026-09-11,
# when a long press started meaning "send", so that a quick word said while
# holding is not mistaken for a tap. The first dial to tune by hand.
TAP_MAX_HOLD = float(os.getenv("TAP_MAX_HOLD", "0.5"))

# -- Audio -----------------------------------------------------------------
SAMPLE_RATE = 16_000  # Whisper resamples to 16 kHz regardless. Higher is pure
CHANNELS = 1          # upload latency for zero accuracy gain.
MAX_RECORD_SECONDS = int(os.getenv("MAX_RECORD_SECONDS", "120"))
# A tone this long before the cap, so a long dictation is not cut off mid-
# sentence without warning (batch E5).
CAP_WARNING_SECONDS = 10

# -- The log ---------------------------------------------------------------
# Started from the desktop icon, Sibyl runs under pythonw.exe, which has no
# console, so print() has nowhere to go. open_log sends it to this file.
# The log holds timings, states and errors, never what was said or where it
# went: every dictation would otherwise sit on disk (review round, U36).
# Those lines pass through private(), which shows them only in a console.
LOG_FILE = Path(os.getenv("LOCALAPPDATA", tempfile.gettempdir())) / "Sibyl" / "sibyl.log"
LOG_MAX_BYTES = 1_000_000   # past this, the log becomes sibyl.old.log at start
_logging = False            # True once print() writes to LOG_FILE


class _Stamped:
    """A file that starts every line with the time.

    A line is held here until it is complete, then written in one piece. That
    is load-bearing: `print` writes its text and its newline as two separate
    calls, so a lock around one call does not keep a line whole. Measured
    2026-09-19 with two threads printing: 98 of 6,000 lines came out merged
    into each other. The boot wait prints while a capture can already be
    running, so this is normal use, not a stress test.
    """

    encoding = "utf-8"
    errors = "strict"

    def __init__(self, stream) -> None:
        self._stream = stream
        self._lock = threading.Lock()
        self._held = threading.local()   # the line each thread is still writing

    def write(self, text: str) -> int:
        whole, newline, rest = (getattr(self._held, "text", "") + text).rpartition("\n")
        # A progress bar redraws with \r and never sends a newline, so a held
        # line that grows past this is written out rather than kept for ever.
        if not newline and len(rest) > 4096:
            whole, rest = rest, ""
            newline = "\n"
        self._held.text = rest
        if newline:
            self._put(whole)
        return len(text)

    def _put(self, block: str) -> None:
        stamp = time.strftime("%Y-%m-%d %H:%M:%S  ")
        stamped = "".join(f"{stamp}{line}\n" for line in block.split("\n"))
        with self._lock:
            self._stream.write(stamped)
            self._stream.flush()

    def flush(self) -> None:
        held = getattr(self._held, "text", "")
        if held:
            self._held.text = ""
            self._put(held)
        self._stream.flush()

    def isatty(self) -> bool:
        return False

    def fileno(self) -> int:
        # Some libraries ask a stream for its file number, to hand it to a
        # child process. Giving them the real file is better than an error.
        return self._stream.fileno()


def open_log() -> None:
    """Send everything printed, errors included, to LOG_FILE."""
    global _logging
    LOG_FILE.parent.mkdir(parents=True, exist_ok=True)
    if LOG_FILE.exists() and LOG_FILE.stat().st_size > LOG_MAX_BYTES:
        try:
            LOG_FILE.replace(LOG_FILE.with_name("sibyl.old.log"))
        except OSError:
            # Two copies starting together: the other one moved it first.
            pass
    sys.stdout = sys.stderr = _Stamped(open(LOG_FILE, "a", encoding="utf-8"))
    _logging = True
    sys.excepthook = report_crash
    threading.excepthook = lambda crash: report_crash(
        crash.exc_type, crash.exc_value, crash.exc_traceback)
    # Three more ways Python and the libraries write to stderr, each with
    # full paths or an error's text: an error Python can only ignore (in a
    # __del__ or a ctypes callback), a warning, and a library's own report
    # through logging, which pystray and pynput send with the traceback.
    sys.unraisablehook = lambda lost: report_crash(
        lost.exc_type, lost.exc_value, lost.exc_traceback, "ignored")
    warnings.showwarning = lambda message, category, filename, lineno, *_: print(
        f"[warning] {category.__name__} at {Path(filename).name}:{lineno} "
        f"(details not logged)")
    logging.getLogger().addHandler(_LibraryReport())


def report_crash(kind, exc, trace, what="crash") -> None:
    """An error nobody caught, for the log. Python's own report gives each
    file's full path, which holds the Windows account name, and the error's
    text, which can hold anything. This gives the kind and where it happened,
    by file name and line, which is what fixing it needs."""
    print(f"[{what}] {kind.__name__} (details not logged), at {_where(trace)}")


def _where(trace) -> str:
    """Where an error happened, innermost first, by file name and line only."""
    return " < ".join(f"{Path(frame.filename).name}:{frame.lineno} in {frame.name}"
                      for frame in reversed(traceback.extract_tb(trace)))


class _LibraryReport(logging.Handler):
    """A library's warning or error, for the log: which library, how bad, and
    the error's kind and where, as a crash says it. Its message is library text,
    so it is left out, never even formatted: formatting can fail, and logging
    reports that in full."""

    def emit(self, record) -> None:
        kind = where = ""
        if record.exc_info and record.exc_info[0]:
            kind = f" {record.exc_info[0].__name__}"
            where = f", at {_where(record.exc_info[2])}"
        print(f"[{record.name}] {record.levelname}{kind} (details not logged){where}")


def private(text: str, instead: str) -> str:
    """`text` holds dictated words or a window's title: fine on a screen you are
    watching, never written to the log."""
    return instead if _logging else text


def private_path(recording: Path) -> str:
    """A recording's path holds the Windows account name; the log gets only its
    file name, which is random and enough to find it in the temp folder. Read
    from except blocks, so it asks only for what anything printable has."""
    return private(str(recording), f"{Path(str(recording)).name} in your temp folder")


# Output captured to a file or a pipe is written in Windows' old code page,
# which cannot spell every character a title or a transcript holds, and print
# then raises: a Chrome tab's zero-width space failed a press as "Microphone
# failed" (2026-10-04). Such a character is now printed as its code, \u200b,
# which is what Python already does on stderr. Under pythonw there is no stdout.
if sys.stdout is not None and hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(errors="backslashreplace")


# ---------------------------------------------------------------------------
# 2. AUDIO CUES
# ---------------------------------------------------------------------------
# The point is that you never look away from the editor, so state changes have
# to be audible.

_TONES = {
    "start": (880, 90),
    "stop": (523, 90),
    "error": (220, 220),
    "warning": (1047, 300),   # high and long: unlike any other, ten seconds left
}

_MAC_SOUNDS = {
    "start": "/System/Library/Sounds/Tink.aiff",
    "stop": "/System/Library/Sounds/Pop.aiff",
    "error": "/System/Library/Sounds/Basso.aiff",
    "warning": "/System/Library/Sounds/Ping.aiff",
}


def beep(kind: str = "start") -> None:
    """Non-fatal by design. A machine with no audio output should still run the
    pipeline; losing the cue is an annoyance, not a failure.

    Returns at once. winsound.Beep blocks for as long as the tone plays, and it
    was played inside the keyboard callback, on the thread Windows times: an
    error beep there took 220 ms per refused press, and Windows silently
    removes a keyboard hook that answers too slowly (review round 3)."""
    try:
        if IS_MAC:
            subprocess.Popen(
                ["afplay", _MAC_SOUNDS.get(kind, _MAC_SOUNDS["start"])],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
        else:
            import winsound

            freq, dur = _TONES.get(kind, _TONES["start"])
            threading.Thread(target=_tone, args=(winsound, freq, dur),
                             daemon=True).start()
    except Exception:
        pass


def _tone(winsound, freq: int, dur: int) -> None:
    """Play one tone on its own thread. Errors are swallowed for the same reason
    as in beep: a machine with no speakers still runs Sibyl."""
    try:
        winsound.Beep(freq, dur)
    except Exception:
        pass


# ---------------------------------------------------------------------------
# 3. RECORDER
# ---------------------------------------------------------------------------


class Recorder:
    """Captures microphone audio to a temp WAV.

    Frames accumulate in a list via the sounddevice callback rather than being
    written straight to disk, because the callback runs on a realtime audio
    thread where file I/O risks dropouts.
    """

    def __init__(self) -> None:
        self._frames: list = []
        self._stream = None
        self._timers: list[threading.Timer] = []
        self._started_at: float | None = None
        self.is_recording = False
        self.last_duration = 0.0

    def _callback(self, indata, frames, time_info, status) -> None:
        # indata is reused by PortAudio between calls, so it must be copied.
        self._frames.append(indata.copy())

    def start(self, on_timeout=None, on_warning=None) -> None:
        import sounddevice as sd

        self._frames = []
        self._started_at = time.time()
        stream = sd.InputStream(
            samplerate=SAMPLE_RATE,
            channels=CHANNELS,
            dtype="int16",
            callback=self._callback,
        )
        # Opened but refusing to start: close it, or nothing ever will.
        try:
            stream.start()
        except Exception:
            stream.close()
            raise
        self._stream = stream
        self.is_recording = True

        # Both timers live here so that stop() cancels both, however the
        # capture ends.
        for delay, action in ((MAX_RECORD_SECONDS, on_timeout),
                              (max(0, MAX_RECORD_SECONDS - CAP_WARNING_SECONDS),
                               on_warning)):
            if action is not None:
                timer = threading.Timer(delay, action)
                timer.daemon = True
                timer.start()
                self._timers.append(timer)

    def stop(self) -> Path | None:
        """Returns the WAV path, or None if nothing usable was captured."""
        import numpy as np
        import soundfile as sf

        for timer in self._timers:
            timer.cancel()
        self._timers = []

        if self._stream is not None:
            self._stream.stop()
            self._stream.close()
            self._stream = None
        self.is_recording = False

        self.last_duration = (
            time.time() - self._started_at if self._started_at else 0.0
        )
        self._started_at = None

        if not self._frames:
            return None

        audio = np.concatenate(self._frames, axis=0)
        self._frames = []

        # Guard against an accidental double-tap producing a 50ms clip that
        # burns a transcription pass on silence.
        if len(audio) < SAMPLE_RATE * 0.3:
            return None

        fd, name = tempfile.mkstemp(suffix=".wav", prefix="sibyl_")
        os.close(fd)
        path = Path(name)
        try:
            sf.write(path, audio, SAMPLE_RATE, subtype="PCM_16")
        except Exception:
            # A half-written recording of your voice, whose name the caller
            # never learns, so nothing else could delete it (review round 3).
            try:
                path.unlink(missing_ok=True)
            except OSError:
                print(f"[recorded] the recording could not be saved or deleted; "
                      f"remove it by hand: {private_path(path)}")
            raise
        return path


# ---------------------------------------------------------------------------
# 4. TRANSCRIPTION (local, pluggable)
# ---------------------------------------------------------------------------
# Behind a one-function interface on purpose. Research says Parakeet TDT and
# Moonshine both beat faster-whisper on CPU, but neither claim was measured on
# an i7-1165G7. Phase 2 runs the bake-off; this keeps the swap to one line.


class WhisperBackend:
    """faster-whisper via CTranslate2. The Phase 1 baseline: best documented,
    least likely to burn a session on toolchain problems."""

    name = "faster-whisper"

    def __init__(self) -> None:
        self._model = None
        self._ready = threading.Event()
        self._load_error: str | None = None
        self.download_failed = False   # the fetch itself, not what came after

    def load(self, on_download=None) -> None:
        """Called at boot, not per capture. Cold-loading on every capture is the
        single biggest avoidable source of latency.

        From disk when every file is there; otherwise `on_download` is called
        and the missing files are fetched (batch E6)."""
        try:
            # HuggingFace's xet transport gets connection-reset (WinError 10054)
            # on this network, which surfaces as a confusing "cannot find the
            # snapshot folder" error. The classic HTTP transport works. Only
            # matters for the one-time weight download; ignored once cached.
            os.environ.setdefault("HF_HUB_DISABLE_XET", "1")
            os.environ.setdefault("HF_HUB_DISABLE_SYMLINKS_WARNING", "1")

            from faster_whisper import WhisperModel
            from faster_whisper.utils import download_model
            # Hugging Face's library gives its logger an output of its own when
            # this loads, writing its full messages, web addresses and errors
            # naming your user folder, to stderr: the log. Removed, they reach
            # only _LibraryReport. Loaded here, not with the log: 0.6 s.
            from huggingface_hub.utils import logging as _hf_logging  # noqa: F401
            logging.getLogger("huggingface_hub").handlers.clear()

            where = self._on_disk()
            if where is None:
                if on_download is not None:
                    on_download()
                # Fetched as its own step, so a failure here, and only here,
                # is blamed on the download. A model that arrives and then
                # cannot load (memory, a corrupt file) is the model's fault,
                # and its message keeps the command that shows why (review).
                try:
                    where = download_model(STT_MODEL)   # fetches what is missing
                except Exception:
                    self.download_failed = True
                    raise
            self._model = WhisperModel(
                where, device=STT_DEVICE, compute_type=STT_COMPUTE
            )
        except Exception as exc:
            self._load_error = f"{type(exc).__name__}: {exc}"
        finally:
            self._ready.set()

    @staticmethod
    def _on_disk() -> str | None:
        """The model's folder if every file it needs is already there, else None.

        Looked in rather than trusted to fail: measured 2026-10-02, a partial
        folder fails three different ways, and one of them, a missing
        tokenizer.json, faster-whisper quietly fetches from another model's
        page. Loading from the folder also skips the check with Hugging Face
        that loading by name makes on every start."""
        if os.path.isdir(STT_MODEL):
            return STT_MODEL   # a folder you named: used as it is
        from faster_whisper.utils import download_model
        # From .utils, not .errors: .errors is newer than the oldest
        # huggingface_hub faster-whisper allows (0.21); both give one class.
        from huggingface_hub.utils import LocalEntryNotFoundError

        try:
            folder = download_model(STT_MODEL, local_files_only=True)
        except LocalEntryNotFoundError:
            return None
        have = os.listdir(folder)
        complete = (all(name in have for name in
                        ("config.json", "model.bin", "tokenizer.json"))
                    and any(name.startswith("vocabulary.") for name in have))
        return folder if complete else None

    @property
    def load_error(self) -> str | None:
        """Why the model did not load, or None. Read at boot: without this a
        half-downloaded base.en left a grey "ready" over a dead recogniser,
        and only said so once someone had spoken (2026-09-25)."""
        return self._load_error

    def transcribe(self, wav_path: Path) -> str:
        self._ready.wait(timeout=300)
        if self._load_error:
            raise RuntimeError("Could not load the STT model. " + self._load_error)
        if self._model is None:
            raise RuntimeError("STT model is not loaded.")

        segments, _info = self._model.transcribe(
            str(wav_path),
            beam_size=1,          # greedy. Beam search costs time for marginal
            vad_filter=True,      # accuracy gain on clean dictation.
            language="en" if STT_MODEL.endswith(".en") else None,
            # faster-whisper 1.2.1 treats "" and None alike, so `or None` only
            # says out loud that an empty setting means no hint.
            hotwords=STT_HOTWORDS or None,
        )
        return " ".join(seg.text.strip() for seg in segments).strip()


_BACKENDS = {"faster-whisper": WhisperBackend}
_stt = None


def get_stt():
    global _stt
    if _stt is None:
        cls = _BACKENDS.get(STT_BACKEND)
        if cls is None:
            raise RuntimeError(
                f"Unknown STT_BACKEND '{STT_BACKEND}'. "
                f"Available: {', '.join(_BACKENDS)}"
            )
        _stt = cls()
    return _stt


def stt_is_dead() -> bool:
    """True when the recogniser will fail every capture until Sibyl restarts.

    Read from inside exception handlers, so it must never raise: a backend that
    cannot even be built is certainly dead, and an answer of "dead" is the safe
    one. Swallowing here buys a reliable report; the reason is printed by
    whoever caught the original failure."""
    try:
        return bool(get_stt().load_error)
    except Exception:
        return True


def transcribe(wav_path: Path) -> str:
    return get_stt().transcribe(wav_path)


# ---------------------------------------------------------------------------
# 5. THE LLM PIPELINE (Ollama)
# ---------------------------------------------------------------------------

CLEANUP_PROMPT = """\
You turn dictated speech into the text the speaker meant to write. You are an
editor, not an assistant: you never reply, answer, explain or add anything.

Rules:
- Remove fillers (um, uh, like, you know, basically, a leading "so"), false
  starts and repeated words.
- When the speaker corrects themselves ("no wait", "sorry", "I mean"), keep only
  the corrected version, and keep everything else they said.
- Keep every fact: every name, number, amount, date, place, item and task. Never
  drop one. Never add one: no time, date, price or name that was not said.
- Keep the speaker's voice. "I" stays "I", "I think" stays "I think", and a
  question stays a question.
- Fix punctuation, capital letters and sentence breaks.

Layout:
- If the speaker lists separate items or steps (first, second, third; one, two,
  three), write a short lead-in line, then one line per item starting with "- ".
- If the speaker changes topic, start a new paragraph.
- Otherwise write plain sentences. A short dictation stays on one line. A list
  mentioned in passing stays inside its sentence. No headings, no bold.

Output only the cleaned text.
"""

# Ollama holds a model in RAM after first use. Tracked so the cold-start
# timeout applies only when it is actually warranted.
_warm_models: set[str] = set()


class OllamaError(RuntimeError):
    """Carries a message already written for a human, not a stack trace."""


# How much the model may hold at once, counted in word pieces. Derived rather
# than fixed, because both numbers that fill it are settings: raising
# MAX_RECORD_SECONDS or the answer cap while this stayed put would overflow the
# window, and an overflow is the worst failure shape this project has. Measured
# 2026-09-20 at num_ctx 512 with a 589-piece prompt: Ollama cut it to 482,
# returned no error, and the answer kept a filler the instructions forbid. The
# oldest pieces go first, and the oldest pieces are the instructions.
PROMPT_PIECES = 700      # instructions and examples: 571 measured, rounded up
                         # so adding an example does not eat the margin quietly
PIECES_A_MINUTE = 540    # dictation dense with names: 2.67 pieces a word
                         # against 1.14 for ordinary speech, both measured


def context_window(max_tokens: int) -> int:
    """Word pieces to give the model, rounded up to a whole 1,024 as Ollama
    sizes its own memory. At today's settings this is 3,072: Ollama reports
    2.1 GB there against 2.2 GB at 4,096, and 2,048 reports 2.1 GB too, so
    going below 3,072 saves nothing measurable and only risks the overflow."""
    needed = PROMPT_PIECES + MAX_RECORD_SECONDS * PIECES_A_MINUTE // 60 + max_tokens
    return -(-needed // 1024) * 1024


def ollama_chat(model: str, system: str, user: str, on_token=None,
                examples: list[tuple[str, str]] | None = None,
                max_tokens: int = 1024) -> str:
    """One streaming chat call.

    Streaming is not optional here. At 10-15 tokens/sec a non-streaming call is
    indistinguishable from a freeze for half a minute, and there is no way to
    show progress or tell a slow model from a hung one.
    """
    import requests

    if OLLAMA_ADDRESS_PROBLEM:
        raise OllamaError(OLLAMA_ADDRESS_PROBLEM)
    cold = model not in _warm_models
    read_timeout = OLLAMA_TIMEOUT_COLD if cold else OLLAMA_TIMEOUT_WARM

    messages = [{"role": "system", "content": system}]
    # Few-shot examples as real conversation turns, not prose in the system
    # prompt. A 1.5B model ignores "keep the first person" as an instruction but
    # copies the pattern reliably when it can see it done.
    for example_in, example_out in examples or []:
        messages.append({"role": "user", "content": example_in})
        messages.append({"role": "assistant", "content": example_out})
    messages.append({"role": "user", "content": user})

    payload = {
        "model": model,
        "stream": True,
        # Newer models (qwen3.5 and similar) "think" before answering by
        # default: measured 2026-09-11, qwen3.5:0.8b wrote 512 tokens of hidden
        # reasoning, hit the cap, and returned an EMPTY answer, so Sibyl would
        # have pasted the raw transcript. Clean-up is copying, not reasoning.
        # Off always; older models accept the flag and ignore it.
        "think": False,
        "keep_alive": "30m",
        "messages": messages,
        "options": {
            "temperature": 0.2,
            # No penalty for reusing a word: the speaker's own words are what
            # the model has just read, and penalising them pushes it towards
            # synonyms. Measured 2026-09-19 at 1.1: "maybe" came back as
            # "possibly" and "if the weather holds" as "depending on the
            # weather". Ollama's default behaves as 1.0 today; written down so
            # an update cannot change it quietly.
            "repeat_penalty": 1.0,
            # Belt and braces: the model ends its turn with these markers, and
            # writing them here stops a stray one starting a new turn.
            "stop": ["<|im_start|>", "<|im_end|>"],
            "num_ctx": context_window(max_tokens),
            # Ollama defaults to unlimited generation. A model that starts
            # rambling can turn a few-second wait into a minute. Measured
            # clean-up output is ~65 tokens, so the cap is several times
            # headroom: it never truncates normal output, it only stops a
            # runaway. Raised by two reviewers.
            "num_predict": max_tokens,
        },
    }

    try:
        with requests.post(
            f"{OLLAMA_HOST}/api/chat",
            json=payload,
            stream=True,
            timeout=(5, read_timeout),
            proxies=NO_PROXIES,
        ) as response:
            # Distinct messages per failure. One generic "Ollama error" tells
            # you nothing at 11pm.
            if response.status_code == 404:
                raise OllamaError(
                    f"Model '{model}' is not installed. Run:  ollama pull {model}"
                )
            if response.status_code != 200:
                # The server's reply can quote the request back, so it is
                # treated as dictated words: shown in a console, not logged.
                raise OllamaError(
                    f"Ollama returned {response.status_code}: "
                    + private(response.text[:200], "(reply not logged)")
                )

            parts: list[str] = []
            final = None
            for line in response.iter_lines():
                if not line:
                    continue
                try:
                    chunk = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if chunk.get("error"):
                    raise OllamaError(
                        "Ollama: " + private(str(chunk["error"]),
                                             "(error text not logged)"))
                piece = chunk.get("message", {}).get("content", "")
                if piece:
                    parts.append(piece)
                    if on_token is not None:
                        on_token(piece)
                if chunk.get("done"):
                    final = chunk
                    break
            # Three ways an answer used to arrive incomplete and be pasted as
            # if whole (review round, 2026-09-12): no final message, cut off at
            # the length limit, and a connection that breaks (caught below).
            # Each is now a failure, so it goes down the same fallback as a
            # server that is not running. `done_reason` is the field's name on
            # Ollama 0.33.3, checked with a 5-token call on 2026-09-12, and
            # re-checked on 0.34.4, streamed, on 2026-09-26.
            if final is None:
                raise OllamaError("Ollama's answer stopped before it finished")
            if final.get("done_reason") == "length":
                raise OllamaError(f"the model's answer was cut off at the "
                                  f"{max_tokens}-token limit")

    except requests.exceptions.ChunkedEncodingError:
        raise OllamaError("the connection to Ollama broke mid-answer")
    except requests.exceptions.ConnectionError:
        raise OllamaError(
            f"Ollama is not running at {OLLAMA_HOST}. Start it with:  ollama serve"
        )
    except requests.exceptions.ReadTimeout:
        stage = "loading the model" if cold else "generating"
        raise OllamaError(
            f"Ollama timed out after {read_timeout}s while {stage}. "
            f"Try a smaller model, or raise OLLAMA_TIMEOUT_"
            f"{'COLD' if cold else 'WARM'} in .env."
        )
    except requests.exceptions.RequestException as exc:
        # Everything else requests can raise, such as an address it cannot
        # use at all. "0.0.0.0:11434", Ollama's own network setting, raised
        # InvalidSchema, which escaped the fallback: every capture said
        # "Something went wrong" (review round 3).
        raise OllamaError(f"could not reach Ollama at {OLLAMA_HOST} "
                          f"({type(exc).__name__})")

    _warm_models.add(model)
    return "".join(parts).strip()


def warm_up(model: str) -> None:
    """Load a model into RAM at boot so the first real capture does not pay for
    it. Failures here are deliberately silent: warming is an optimisation, and
    the real error messages belong to the first genuine call."""
    import requests

    try:
        requests.post(
            f"{OLLAMA_HOST}/api/generate",
            json={"model": model, "prompt": "", "keep_alive": "30m"},
            timeout=OLLAMA_TIMEOUT_COLD,
            proxies=NO_PROXIES,
        )
        _warm_models.add(model)
    except Exception:
        pass


def ollama_problem(model: str) -> str:
    """What stops the clean-up working, in the notch's few words, or "" if
    nothing. Asked once at start, so a problem shows before the first capture
    rather than as a surprise after it."""
    import requests

    # Each cause says what it is. One label for all of them sent the boot
    # wait retrying "not running" while Ollama was answering, but oddly.
    if OLLAMA_ADDRESS_PROBLEM:
        return "Ollama address refused"
    try:
        response = requests.get(f"{OLLAMA_HOST}/api/tags", timeout=5,
                                proxies=NO_PROXIES)
    except Exception:
        return "Ollama not running"
    if response.status_code != 200:
        return f"Ollama error {response.status_code}"
    try:
        installed = {m.get("name", "") for m in response.json().get("models", [])}
    except Exception:
        return "Ollama answered oddly"
    if model not in installed and f"{model}:latest" not in installed:
        return "Model not installed"
    return ""


# -- Stage 1 ---------------------------------------------------------------


# Sentence-opening discourse markers. Measured: both qwen2.5:1.5b and
# qwen2.5-coder:3b reliably resolve self-corrections but both leave these in,
# so a regex does the mechanical half and the model does the judgement half.
# Cheaper and more reliable than asking a bigger model to be more obedient.
#
# Split into two tiers by how safe each word is to delete:
_ALWAYS_FILLER = r"um|uh+|er+|hmm"                       # never meaningful
# "So far", "I mean to", "You know the answer" carry meaning, so "so" is kept
# before far/much/many/long and "i mean"/"you know" need a comma (2026-09-12).
# "Okay, that works" says yes, so "okay" goes only before another filler, as in
# "Okay, so the meeting moved" (decided 2026-09-12).
# "So you know" and "so I mean" are fixed phrases meaning "for your
# information": found 2026-09-23, present since this rule was written. The
# chain at the end of _FILLER_HEAD stripped "So" as a soft filler and then
# "you know," as a comma filler, so "So you know, I will not be in tomorrow"
# was pasted as "I will not be in tomorrow", losing the whole preface. A comma
# after "so" is different ("So, you know, I think") and still strips.
_SOFT_FILLER = (r"so(?!\s+(?:far|much|many|long|you know|i mean)\b)"
                r"|basically|anyway")  # filler at sentence start
_COMMA_FILLER = r"right|well|now|look|i mean|you know"  # only filler before a comma
_CHAIN_FILLER = r"okay|ok"                              # only filler before another filler

_ANY_FILLER = f"{_ALWAYS_FILLER}|{_SOFT_FILLER}|{_COMMA_FILLER}|{_CHAIN_FILLER}"

_FILLER_HEAD = re.compile(
    r"^\s*(?:"
    rf"(?:{_ALWAYS_FILLER})\b[,\s]*"
    rf"|(?:{_SOFT_FILLER})\b[,\s]+"
    # A comma-tier word counts as filler when a comma follows it, or when
    # another filler does. "Well, the thing" and "Well so it crashes" both
    # strip; "Well done" and "Right alignment is broken" both survive.
    rf"|(?:{_COMMA_FILLER})\b(?:\s*,\s*|\s+(?=(?:{_ANY_FILLER})\b))"
    rf"|(?:{_CHAIN_FILLER})\b[,\s]+(?=(?:{_ANY_FILLER})\b)"
    r")+",
    re.IGNORECASE,
)

# The same comma-tier words again, in the one other place they sit: straight
# after a joining word, in the middle of a sentence. Measured 2026-09-20, three
# runs of three, the model returned "I'm not completely certain, but you know,
# I think the Leeds office might close in March." _FILLER_HEAD only looks at a
# sentence start, so that "you know" survived, and `faithful` has no filler
# rule, so the answer was trusted and pasted every time.
#
# Two words only, not the whole comma tier. At a sentence start "Now, the thing
# is" is a discourse marker, but after a joining word "and now, we wait" means
# at this moment, and "and look, over there" is an instruction: deleting either
# would change what was said, which is the one thing this project never does.
# "you know" and "i mean" carry no such second meaning once a comma follows.
#
# The comma is the whole guard, and it is the same guard _FILLER_HEAD already
# relies on: a meant "you know" is not followed by one. "because you know the
# answer" keeps its words; "because you know, the answer is late" loses the
# filler. The joining word is put back, so only the filler and its comma go.
# "but" only, and that is deliberate. A review round on 2026-09-23 traced the
# wider list: "So you know, I will not be in tomorrow" became "I will not be in
# tomorrow", losing the whole preface, because "so you know" is a fixed phrase
# meaning "for your information" rather than two fillers in a row. "And I mean,
# literally every one" lost its emphasis the same way. "but" carries no such
# idiom, and it is the one word measured failing. Widen this only from measured
# answers, never from imagination; the list above shows how fast it goes wrong.
_JOINED_ONLY = r"i mean|you know"
_JOINED_FILLER = re.compile(
    rf"\b(but)\s+(?:{_JOINED_ONLY})\b\s*,\s*",
    re.IGNORECASE,
)

# Remarks about dictating, removed from your own words when they are pasted in
# place of the model's answer (batch D4 of round 3, 2026-10-02). The model is
# told to remove them, so until now the one dictation in three that fell back
# kept them. Only these measured forms, taken from the dictation that set the
# rule on 2026-09-12 and the evaluation cases built from it; only at a sentence
# start; and only before a comma or a full stop (or an ellipsis). "Before I
# forget the password, write it down" has neither, and keeps its subject.
# "I want to get this down" on its own goes only before a full stop: the review
# of D4 found "I want to get this down, to under ten pages" losing its meant
# first half. Known costs: "I want to get this down." said of a box on a shelf,
# and "Before I forget it, put it in the bag", lose those words. Widen this
# only from measured dictation, like the filler lists above.
_REMARK = (r"i want to get this down before i forget(?: it)?"
           r"|before i forget(?: it)?"
           r"|(?:this is )?just me thinking out loud(?: for a minute)?"
           r"|just thinking out loud")
_REMARK_HEAD = re.compile(
    rf"^\s*(?:(?:{_REMARK})\s*[,.]|i want to get this down\s*\.)\.*\s*", re.IGNORECASE)


# Whisper spells technical terms the way they sound. Fixing them is a lookup,
# not a judgement, so a rule does it: deterministic, instant, and it runs
# before the model sees anything.
#
# This exists because of a measurement mistake worth remembering. When cleanup
# was dropped from Prompt mode, mis-transcriptions stopped being corrected, and
# the A/B test did not notice because its term matcher stripped whitespace, so
# "SQ Lite" counted as a match for "SQLite". The latency win was real; the
# quality claim was an artefact of a blind test. Same lesson as the filler
# regex: never hand a model a job a rule does perfectly.
#
# Deliberately conservative: a rule that mangles a real sentence is worse than
# a mis-spelled library name. Each entry must be a spelling that is essentially
# never valid in a sentence about code.
#
# Excluded for exactly that reason, after testing:
#   "fast API", "my SQL", "rest API"  - all ordinary English phrases.
#   "red is" -> Redis                 - broke "the red is too bright" in test.
#                                       Only the single word "redis" is matched.
#   react, flask, docker              - ordinary English ("packed a flask of
#                                       tea"). Removed 2026-09-12, review round.
_TERM_FIXES = [
    (r"\bsq[\s\-]?lite\b", "SQLite"),
    (r"\bsequel\s?light\b", "SQLite"),
    # Two names, two rules, the longer first: one rule with an optional
    # "ql" turned a correctly said PostgreSQL into Postgres (G2).
    (r"\bpost\s?gres\s?ql\b", "PostgreSQL"),
    (r"\bpost\s?gres\b", "Postgres"),
    (r"\bmy\s?sequel\b", "MySQL"),
    (r"\bpsycho\s?pg\s?(?:two|2)\b", "psycopg2"),
    (r"\bpola\s?rs\b", "Polars"),
    (r"\bnum\s?pie\b", "NumPy"),
    (r"\bpie\s?torch\b", "PyTorch"),
    (r"\bnode\s?js\b", "Node.js"),
    (r"\bmongo\s?db\b", "MongoDB"),
    (r"\bkuber\s?netes\b", "Kubernetes"),
    (r"\btype\s?script\b", "TypeScript"),
    (r"\bjava\s?script\b", "JavaScript"),
    (r"\bgit\s?hub\b", "GitHub"),
    (r"\boh\s?auth\b", "OAuth"),
    (r"\bredis\b", "Redis"),
    (r"\bdjang?o\b", "Django"),
    (r"\bpandas\b", "pandas"),
]

_TERM_FIXES = [(re.compile(p, re.IGNORECASE), r) for p, r in _TERM_FIXES]


def fix_technical_terms(text: str) -> str:
    """Correct how Whisper spells technology names."""
    for pattern, replacement in _TERM_FIXES:
        text = pattern.sub(replacement, text)
    return text


_LIST_MARKER = re.compile(r"\s*(?:[-*•]|\d+[.)])\s+")


def _strip_leading_fillers(text: str, remarks: bool = False) -> str:
    """Remove discourse markers from the start of each sentence, and with
    `remarks`, the remarks about dictating that `rule_clean` removes.

    Line by line, and that is load-bearing. Until 2026-09-11 this split the
    whole text into sentences and rejoined them with spaces, which turned every
    line break into a space: invisible while output was one paragraph, fatal
    once the clean-up wrote bullets and paragraphs. A bullet or number marker
    is set aside first, so "- So book the MOT." becomes "- Book the MOT."
    `test_fillers.py` covers it.
    """
    lines = []
    for line in text.split("\n"):
        marker = _LIST_MARKER.match(line)
        prefix = marker.group(0) if marker else ""
        out = []
        for sentence in re.split(r"(?<=[.!?]) +", line[len(prefix):]):
            sentence = _JOINED_FILLER.sub(r"\1 ", sentence)
            stripped = _FILLER_HEAD.sub("", sentence)
            # Never let a sentence vanish entirely; a filler-only sentence is
            # rare but deleting real content is worse than leaving a stray "So".
            if not stripped.strip():
                stripped = sentence
            if remarks:
                stripped = _REMARK_HEAD.sub("", stripped)
                if not stripped.strip():   # the sentence was only a remark
                    continue
            # Only a sentence something was removed from. "iPhone" and
            # "e.g. the" start in lower case on purpose.
            if stripped and stripped != sentence:
                stripped = stripped[0].upper() + stripped[1:]
            out.append(stripped)
        lines.append(prefix + " ".join(out))
    return "\n".join(lines).strip()


# Worked examples, shown to the model as real conversation turns. On a small
# model these do what instructions cannot: measured on 2026-09-05, a model that
# ignored four paragraphs of instruction followed three examples.
#
# Rewritten 2026-09-11, for two reasons. The old pair were about Flask and
# SQLite, left over from when this was a coding tool. And the second one
# taught the wrong lesson: "I tried Postgres, no wait, I used MySQL first"
# became "I used MySQL first, then tried Postgres", which shows a correction
# as REORDERING both options rather than REPLACING the first. The first live
# clean-up after the rewrite to one mode did exactly that with "bread, no
# wait, butter" and kept both.
#
# Each example shows one behaviour: a correction that replaces one item and
# keeps the rest; a spoken list becoming bullets; a topic change becoming a
# paragraph with "I think" kept; a question staying a question with a passing
# list left inline. The subjects (an allotment, a kayak trip, sourdough,
# garlic) are deliberately unlike anything real, so if one leaks into real
# output it is unmistakable. eval_cleanup.py watches for these words; keep its
# LEAK_WORDS in step with any change here.
CLEANUP_EXAMPLES = [
    (
        "um so this weekend I want to plant the courgettes at the allotment uh "
        "no wait not the courgettes the tomatoes and then fix the trellis by "
        "the shed",
        "This weekend I want to plant the tomatoes at the allotment, and then "
        "fix the trellis by the shed.",
    ),
    (
        "so for the kayak trip I need to sort out three things one hire a "
        "second paddle two check the tide times for the estuary and three buy "
        "a dry bag",
        "For the kayak trip I need to sort out three things:\n"
        "- Hire a second paddle.\n"
        "- Check the tide times for the estuary.\n"
        "- Buy a dry bag.",
    ),
    (
        "I think the sourdough needs a warmer spot because it barely rose "
        "yesterday um anyway totally separate thing the allotment committee "
        "meets on the second Tuesday and I still haven't paid the fee",
        "I think the sourdough needs a warmer spot, because it barely rose "
        "yesterday.\n\n"
        "The allotment committee meets on the second Tuesday, and I still "
        "haven't paid the fee.",
    ),
    (
        "uh is it too late in the year to plant garlic onions and leeks or "
        "should I wait until spring",
        "Is it too late in the year to plant garlic, onions and leeks, or "
        "should I wait until spring?",
    ),
]


def clean_transcript(raw: str, on_token=None) -> str:
    """The product: what you meant to write, not what you literally said."""
    cleaned = ollama_chat(
        CLEANUP_MODEL, CLEANUP_PROMPT, raw,
        on_token=on_token, examples=CLEANUP_EXAMPLES,
        max_tokens=MAX_TOKENS_CLEANUP,
    )
    # A small model occasionally returns nothing. That used to be replaced by
    # the raw transcript, which the check then compared with itself and
    # trusted, so "um" and "er" were pasted under a green "Done" (review round
    # 3). It is a model failure, and goes down the same fallback as one.
    if not cleaned:
        raise OllamaError("the model returned an empty answer")
    return _strip_leading_fillers(cleaned)


# -- The check after the model -------------------------------------------------
#
# Added 2026-09-12, from the second review round. Nothing compared the model's
# answer with what was said, and two critical defects came through that gap: a
# dictated instruction ("write me a polite email...") was carried out, 6 times
# out of 6, and hedges ("probably", "maybe") were dropped. Prompt wording had
# already failed to stop both. Comparing words is mechanical, so code does it:
# instant, no second model, and it cannot fail the way the model fails.
#
# When the check fails, or the model fails in any way, Sibyl pastes your own
# words with only the fixed rules applied: untidy but complete, never invented.
# Tuned toward false alarms, because a false alarm costs tidiness and a miss
# costs content. Every limit below was set from measured answers, never guessed;
# the measurement that set each one is cited beside it.

# Each must appear in the answer at least as often as it was said.
HEDGES = ["probably", "maybe", "might", "perhaps", "possibly", "as well", "also",
          "i think", "not sure"]

# The evaluation's growth rule since 2026-09-11, moved here so the app and the
# evaluation share one definition. Every dictated email and poem measured was
# 4 to 11 times longer than what was said.
GROWTH_LIMIT = 1.25

# The share of what was said, counting only words that carry content, that
# must survive into the answer. Measured 2026-09-12 over 78 answers: good ones
# kept 50% at the lowest ("just thinking out loud but we could possibly hire a
# temp" losing its remark about dictating, which is removed deliberately) and
# 67% for a correction; every failing answer that kept less had also grown. So
# this sits just under the lowest good answer. The hedge check, not this, is
# what caught every summary: they all kept 80-97%.
KEEP_AT_LEAST = 0.45

_NUMBER_WORDS = re.compile(
    r"\b(?:one|two|three|four|five|six|seven|eight|nine|ten|eleven|twelve|"
    r"twenty|thirty|forty|fifty|hundred|thousand|million|first|second|third|"
    r"half)\b")

# "I'll" and "I will" must count as the same words, so both sides are written
# out in full before they are compared.
_CONTRACTIONS = [
    (r"\bwon't\b", "will not"), (r"\bcan't\b", "can not"), (r"\bcannot\b", "can not"),
    (r"n't\b", " not"), (r"'m\b", " am"), (r"'re\b", " are"), (r"'ll\b", " will"),
    (r"'ve\b", " have"), (r"'d\b", " would"), (r"'s\b", " is"),
]

# Words whose loss says nothing about whether content was kept: grammar,
# fillers, correction markers, and numbers and currencies, which legitimately
# come back as digits and symbols.
_FUNCTION_WORDS = frozenset("""
a an the and or but so to of in on at for from by with about as into than then
i me my you your he him his she her it its we us our they them their this that
these those there here is are was were be been being am do does did have has had
will would can could should shall may must not no just like um uh er hmm okay ok
right well anyway basically mean know yeah sorry wait pounds dollars euros percent
""".split())

# Words the model may add without it counting as inventing something. Measured
# 2026-09-23 over 132 answers, every case four times, interleaved: the check
# rejected four good answers and every one blamed the same word, "that". No word
# was ever the only thing that caught a bad answer, so allowing it took the
# false alarms from 4 to 0 while all 31 bad answers were still caught.
#
# A false alarm is not free. It throws away a correct clean-up and pastes the
# raw transcript instead, so the speaker gets their untidy words when the model
# had already written what they meant.
#
# The plan proposed nineteen grammar words (a, the, is, are, was, were, and, to,
# of, in, on, at, for, with, it and friends). Scored against the same 132
# answers they did exactly as well as "that" alone and no better, so the other
# eighteen were left out: each would be a guess, and every word allowed here is
# a word the model may put in the speaker's mouth. Never "not", "no", "never",
# or a word that changes certainty ("will", "must", "should", "might"). Widen
# this the way it was made: from answers that were measured, not from a list
# that reads sensibly.
#
# The known cost, found by the review of 2026-09-23 and not seen in any of the
# 132 answers: "that" is two words in one. "tell her that the invoice" is the
# harmless kind; "he wants that raise" for "he wants a raise" is not, because it
# points at a particular raise. The check cannot tell them apart, and it does
# not notice a dropped "a" either, so that swap would now pass. Watch for it
# when reading answers; it is the one hole this word opens.
_HARMLESS = frozenset({"that"})


def _words(text: str) -> list[str]:
    # Accents off first: "Élysées" and "Elysees" are one word, the same word.
    # Before this, an accented letter split a word ("lys", "es"), and an
    # answer that dropped the accent was rejected for adding "elysees"
    # (found 2026-09-26; Champs-Élysées is one of STT_HOTWORDS' names).
    text = "".join(c for c in unicodedata.normalize("NFKD", text)
                   if not unicodedata.combining(c))
    text = text.lower().replace("’", "'")   # curly apostrophe to straight
    text = re.sub(r"(?<=\d),(?=\d)", "", text)   # 2,450 is one number
    for pattern, full in _CONTRACTIONS:
        text = re.sub(pattern, full, text)
    return re.findall(r"[a-z0-9]+", text)


# Batch D2 of round 3, 2026-09-30: two rules for an answer that keeps the words
# and reverses the meaning ("the report is not ready" written as "the report is
# ready"). D1 measured the check trusting 16 of 17 such answers; the model wrote
# none of them in 51 tries, and the rules were admitted anyway because they cost
# nothing and block a reversal. Both were chosen after every reviewer's design
# was shown to reject the flagship "no wait, not bread, I mean butter".

# A "no" negates only the word straight after it, and only a word that carries
# meaning: "no wait", "no sorry" and "no, that is fine" are not negations. A
# "not" or "never" reaches past small words ("do not have the keys") to the
# first word that does, looking this far ahead.
_NEGATORS = frozenset({"not", "never", "no"})
_NEGATION_REACH = 3


def _negations(words: list[str]) -> tuple[set[str], set[str]]:
    """The words said under a negation, and the words ever said without one."""
    negated_at = set()
    for i, word in enumerate(words):
        if word not in _NEGATORS:
            continue
        reach = 1 if word == "no" else _NEGATION_REACH
        for j in range(i + 1, min(i + 1 + reach, len(words))):
            if words[j] not in _FUNCTION_WORDS and words[j] not in _NEGATORS:
                negated_at.add(j)
                break
    negated = {words[j] for j in negated_at}
    plain = {w for j, w in enumerate(words) if j not in negated_at}
    return negated, plain


_UNITS = {w: n for n, w in enumerate(
    "zero one two three four five six seven eight nine ten eleven twelve thirteen "
    "fourteen fifteen sixteen seventeen eighteen nineteen".split())}
_TENS = {w: 10 * n for n, w in enumerate(
    "twenty thirty forty fifty sixty seventy eighty ninety".split(), start=2)}
_ORDINALS = {w: n for n, w in enumerate(
    "first second third fourth fifth sixth seventh eighth ninth tenth eleventh "
    "twelfth".split(), start=1)}
_SCALES = {"hundred": 100, "thousand": 1000, "million": 1000000}
# "Half past three" is written "3:30", so "half" is 30. "Three and a half"
# written "3.5" is then a false alarm: a known cost, no case measured.
_HALF = 30

# A numbered list's "1." is the model's layout, not a number it claims was said.
_NUMBERED_MARKER = re.compile(r"(?m)^\s*\d+[.)]\s+")


# Shorthand for a scale: "£50k", "£2m", "£1bn". A known cost: "a 5k run" said as
# "five k" is two words and 5, so a model writing "5k" is a false alarm.
_SHORTHAND = re.compile(r"(\d+)(k|m|bn)")
_SHORTHAND_SCALE = {"k": 1000, "m": 1000000, "bn": 1000000000}


def _numbers(words: list[str]) -> set[int]:
    """Every number in these words as a value, so "fifty thousand", "50,000",
    "50 thousand" and "50k" are one number, and "fifty" alone is another.

    The first draft also counted the pieces of a spoken number as said, so
    that "50k" would pass; the deep review of D2 found it then passed "£50" for
    "fifty thousand". Shorthand is read as its value instead."""
    found = set()
    total = current = 0
    spoken = False   # inside a number, which a scale word may still multiply
    for word in [*words, ""]:   # the empty word ends a number left open
        # "three hundred and fifty" is one number; "fifty and one hundred" is
        # two, so "and" joins only after a whole number of hundreds.
        if spoken and word == "and" and current % 100 == 0:
            continue
        joins = (word in _SCALES
                 or (word in _TENS and current % 100 == 0)
                 or ((word in _UNITS or word in _ORDINALS)
                     and (current % 100 == 0
                          or (current % 100 >= 20 and current % 10 == 0))))
        if spoken and not joins:
            found.add(total + current)
            total = current = 0
            spoken = False
        short = _SHORTHAND.fullmatch(word)
        if word.isdigit():   # "50", which "thousand" may follow
            current = int(word)
        elif short:
            found.add(int(short[1]) * _SHORTHAND_SCALE[short[2]])
            continue
        elif word in _SCALES and _SCALES[word] == 100:
            current = (current or 1) * 100
        elif word in _SCALES:
            total += (current or 1) * _SCALES[word]
            current = 0
        elif word in _TENS or word in _UNITS or word in _ORDINALS:
            current += _TENS.get(word) or _UNITS.get(word) or _ORDINALS.get(word) or 0
        elif word == "half":
            found.add(_HALF)
            continue
        else:   # "12th", "mp3"
            found.update(int(d) for d in re.findall(r"\d+", word))
            continue
        spoken = True
    return found


def faithful(said: str, written: str) -> list[str]:
    """Every reason the model's answer cannot be trusted. Empty means trusted."""
    problems = []

    words_in = len(said.split())
    words_out = len(re.findall(r"[A-Za-z0-9£$€][^\s]*", written))
    if words_out > words_in * GROWTH_LIMIT + 2:
        problems.append(f"grew from {words_in} to {words_out} words")

    heard, wrote = _words(said), _words(written)
    heard_text, wrote_text = " ".join(heard), " ".join(wrote)
    for hedge in HEDGES:
        pattern = rf"\b{hedge}\b"
        if len(re.findall(pattern, wrote_text)) < len(re.findall(pattern, heard_text)):
            problems.append(f"lost {hedge!r}")

    # "river front" can come back as "riverfront", so two said words joined
    # count as said.
    known = set(heard) | {a + b for a, b in zip(heard, heard[1:])}
    numbers_said = bool(re.search(r"\d", heard_text) or _NUMBER_WORDS.search(heard_text))
    added = []
    for word in wrote:
        if word in known or word in _HARMLESS or word in added:
            continue
        if numbers_said and (re.search(r"\d", word) or _NUMBER_WORDS.fullmatch(word)):
            continue
        added.append(word)
    if added:
        more = f" and {len(added) - 5} more" if len(added) > 5 else ""
        problems.append("added " + ", ".join(repr(w) for w in added[:5]) + more)

    said_negated, said_plain = _negations(heard)
    wrote_negated, _ = _negations(wrote)
    # A word said both ways ("not coming... no sorry, it is coming") may have
    # had its negation taken back, so only a word always said negated counts.
    for word in sorted(said_negated - said_plain):
        if word in wrote and word not in wrote_negated:
            problems.append(f"lost the negation of {word!r}")

    if numbers_said:
        said_numbers = _numbers(heard)
        for n in sorted(_numbers(_words(_NUMBERED_MARKER.sub("", written))) - said_numbers):
            problems.append(f"wrote {str(n)!r}, which was never said")

    content = [w for w in heard if w not in _FUNCTION_WORDS
               and not re.search(r"\d", w) and not _NUMBER_WORDS.fullmatch(w)]
    if content:
        wrote_set = set(wrote)
        kept = sum(1 for w in content if w in wrote_set) / len(content)
        if kept < KEEP_AT_LEAST:
            problems.append(f"kept only {kept:.0%} of what was said")
    return problems


_ALWAYS_FILLER_ANYWHERE = re.compile(rf"\b(?:{_ALWAYS_FILLER})\b,?\s*", re.IGNORECASE)
# Whisper writes "Uh." or "Um." as a sentence of its own, and the rule above
# left its full stop to be pasted as one (2026-10-02). So a filler that starts
# a sentence takes its full stop with it, and likewise "Hmm?" and "Uh, um.".
# Only one that starts a sentence: one that ends a sentence ("Call them uh.
# Then...") keeps the full stop, or the two sentences would run together.
# Spaces only, never line breaks, so the layout survives.
_ALWAYS_FILLER_SENTENCE = re.compile(
    rf"(?:^[ \t]*|(?<=[.!?]\s))(?:{_ALWAYS_FILLER})\b"
    rf"(?:,?[ \t]*(?:{_ALWAYS_FILLER})\b)*[.!?]+[ \t]*",
    re.IGNORECASE | re.MULTILINE)


def rule_clean(raw: str) -> str:
    """Your words with only the fixed rules applied: um, uh, er and hmm gone
    wherever they are, and fillers and remarks about dictating gone from
    sentence starts. The spelling rules have already run. This is what is
    pasted when the model cannot be trusted."""
    text = _ALWAYS_FILLER_ANYWHERE.sub("", _ALWAYS_FILLER_SENTENCE.sub("", raw))
    cleaned = _strip_leading_fillers(text, remarks=True)
    # A dictation made only of remarks is pasted with them, never as nothing,
    # and leftover punctuation counts as nothing.
    return cleaned if re.search(r"[^\W_]", cleaned) else _strip_leading_fillers(text)


def clean_or_fall_back(raw: str, on_token=None) -> tuple[str, str]:
    """The text to paste, and why it is not the model's answer ("" if it is).

    One path for every failure: the server down, a timeout, an answer cut off
    or broken off, and an answer the check does not trust all paste your words.
    """
    try:
        answer = clean_transcript(raw, on_token=on_token)
    except OllamaError as exc:
        return rule_clean(raw), str(exc)
    problems = faithful(raw, answer)
    if problems:
        return rule_clean(raw), "; ".join(problems)
    return answer, ""


# ---------------------------------------------------------------------------
# 6. INJECTION
# ---------------------------------------------------------------------------


_instance_lock = None  # module-level so it lives as long as the process


def claim_single_instance() -> bool:
    """Stop a second copy of Sibyl starting. Returns False if one is running.

    Two copies would both grab the hotkey, both try to open the microphone, and
    both paste into the same window. Raised by five of six external reviewers.

    A bound socket rather than a PID file on purpose: the operating system
    releases a socket automatically when the process dies, however it dies.
    A PID file survives a crash and then locks you out of your own app.
    """
    global _instance_lock
    import socket

    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    try:
        sock.bind(("127.0.0.1", 49731))
        sock.listen(1)
        _instance_lock = sock  # never closed; held for the process lifetime
        return True
    except OSError:
        sock.close()
        return False


def window_title(hwnd: int | None) -> str:
    """The title bar text of a window, for the log.

    Worth the twelve lines: 'pasted into Visual Studio Code' explains an
    outcome that 'pasted' cannot. The paste goes back to wherever you were
    when you STARTED talking, which is correct but surprising if you have
    since clicked elsewhere, and the only way to see it is to name it.
    """
    if IS_MAC or not hwnd:
        return "unknown window"
    try:
        user32 = ctypes.windll.user32
        length = user32.GetWindowTextLengthW(hwnd)
        if length <= 0:
            return "untitled window"
        buf = ctypes.create_unicode_buffer(length + 1)
        user32.GetWindowTextW(hwnd, buf, length + 1)
        return buf.value or "untitled window"
    except Exception:
        return "unknown window"


def capture_target_window() -> int | None:
    """Remember which window was in front when recording started.

    A capture takes several seconds. In that time you will often click into a
    browser or Slack to look something up. Without this, the paste lands in
    whatever happens to be in front when generation finishes, which means your
    words fired into a chat window. Three of six external reviewers
    rated that critical, and they were right.
    """
    if IS_MAC:
        return None  # Not portable to macOS. See _restore_target_window.
    try:
        return ctypes.windll.user32.GetForegroundWindow() or None
    except Exception:
        return None


def _restore_target_window(hwnd: int | None) -> bool:
    """Put focus back where it was. Returns whether it actually worked.

    Windows deliberately stops background processes from stealing focus, so
    SetForegroundWindow fails unless the caller qualifies. The standard way
    round it is to briefly attach our input queue to the foreground window's
    thread, which makes Windows treat us as the same input context.
    """
    if IS_MAC or not hwnd:
        return True  # Nothing to restore; caller proceeds as before.

    try:
        user32 = ctypes.windll.user32
        kernel32 = ctypes.windll.kernel32

        if not user32.IsWindow(hwnd):
            return False  # Window closed while we were working.
        if user32.GetForegroundWindow() == hwnd:
            return True  # Never left.

        if user32.IsIconic(hwnd):
            user32.ShowWindow(hwnd, 9)  # SW_RESTORE

        foreground = user32.GetForegroundWindow()
        our_thread = kernel32.GetCurrentThreadId()
        their_thread = user32.GetWindowThreadProcessId(foreground, None)

        attached = False
        if their_thread and their_thread != our_thread:
            attached = bool(user32.AttachThreadInput(their_thread, our_thread, True))
        try:
            user32.SetForegroundWindow(hwnd)
        finally:
            if attached:
                user32.AttachThreadInput(their_thread, our_thread, False)

        time.sleep(0.05)  # Let the focus change settle before we check.
        return user32.GetForegroundWindow() == hwnd
    except Exception:
        return False


# Windows can keep a history of everything copied (Win+V) and sync it to your
# other devices, which would keep every dictation. Microsoft names three markers
# that tell it to skip an entry ("Clipboard Formats", learn.microsoft.com,
# checked 2026-09-26): any data under the first, a four-byte zero under the
# other two. The first alone is documented to cover both; all three are written
# because each is documented, and an older Windows may know only some.
CLIPBOARD_OPT_OUT = ("ExcludeClipboardContentFromMonitorProcessing",
                     "CanIncludeInClipboardHistory",
                     "CanUploadToCloudClipboard")


class ClipboardBusy(OSError):
    """Another program held the clipboard for the whole half second. Its own
    kind, so the notch can say so rather than a vaguer "Paste failed": the fix
    is waiting a moment, not anything about the paste (batch E2)."""


def _copy_windows(text: str) -> None:
    """The text and the three markers, in one opening of the clipboard.

    One opening, not two: history takes its copy when the clipboard is closed,
    so markers added afterwards would be too late. Written directly against
    Windows because pyperclip opens and closes the clipboard itself, with the
    text alone. Its shape is pyperclip's, which is proven: a hidden window to
    own the clipboard (Microsoft: with no owner, SetClipboardData fails), and
    half a second of retries while another program has it open.
    """
    from ctypes import wintypes as wt

    # Private copies of the two libraries, so the type declarations below
    # cannot change how the rest of Sibyl calls the same functions.
    u32 = ctypes.WinDLL("user32", use_last_error=True)
    k32 = ctypes.WinDLL("kernel32", use_last_error=True)
    # Handles are 64 bits wide; undeclared, ctypes would cut them to 32.
    u32.CreateWindowExW.argtypes = [wt.DWORD, wt.LPCWSTR, wt.LPCWSTR, wt.DWORD,
                                    ctypes.c_int, ctypes.c_int, ctypes.c_int,
                                    ctypes.c_int, wt.HWND, wt.HMENU,
                                    wt.HINSTANCE, wt.LPVOID]
    u32.CreateWindowExW.restype = wt.HWND
    u32.DestroyWindow.argtypes = [wt.HWND]
    u32.OpenClipboard.argtypes = [wt.HWND]
    u32.SetClipboardData.argtypes = [wt.UINT, wt.HANDLE]
    u32.SetClipboardData.restype = wt.HANDLE
    u32.RegisterClipboardFormatW.argtypes = [wt.LPCWSTR]
    u32.RegisterClipboardFormatW.restype = wt.UINT
    k32.GlobalAlloc.argtypes = [wt.UINT, ctypes.c_size_t]
    k32.GlobalAlloc.restype = wt.HGLOBAL
    k32.GlobalLock.argtypes = [wt.HGLOBAL]
    k32.GlobalLock.restype = wt.LPVOID
    k32.GlobalUnlock.argtypes = [wt.HGLOBAL]
    k32.GlobalFree.argtypes = [wt.HGLOBAL]

    GMEM_MOVEABLE, CF_UNICODETEXT = 0x0002, 13
    # Markers first, text last: if anything fails part-way, the clipboard is
    # closed holding markers without text, never text without markers, which
    # is the very exposure they exist to prevent (review, C2).
    items = [(u32.RegisterClipboardFormatW(name), b"\0\0\0\0")
             for name in CLIPBOARD_OPT_OUT]
    items += [(CF_UNICODETEXT, text.encode("utf-16-le") + b"\0\0")]

    hwnd = u32.CreateWindowExW(0, "STATIC", None, 0, 0, 0, 0, 0,
                               None, None, None, None)
    if not hwnd:
        raise OSError("could not make the window that owns the clipboard")
    try:
        deadline = time.time() + 0.5
        while not u32.OpenClipboard(hwnd):
            if time.time() > deadline:
                raise ClipboardBusy("the clipboard is in use by another program")
            time.sleep(0.01)
        try:
            if not u32.EmptyClipboard():
                raise OSError("could not empty the clipboard")
            for fmt, data in items:
                if not fmt:
                    raise OSError("could not prepare the clipboard")
                handle = k32.GlobalAlloc(GMEM_MOVEABLE, len(data))
                pointer = k32.GlobalLock(handle) if handle else None
                if not pointer:
                    if handle:
                        k32.GlobalFree(handle)
                    raise OSError("could not prepare the clipboard")
                ctypes.memmove(pointer, data, len(data))
                k32.GlobalUnlock(handle)
                # On success Windows owns the memory; on failure it is ours.
                if not u32.SetClipboardData(fmt, handle):
                    k32.GlobalFree(handle)
                    raise OSError("could not put the text on the clipboard")
        finally:
            u32.CloseClipboard()
    finally:
        u32.DestroyWindow(hwnd)


def copy_only(text: str) -> None:
    """Put text on the clipboard and nothing else, kept out of Windows'
    clipboard history and cloud sync.

    Split out of inject on 2026-09-06 because the tray's "Copy last output
    again" needs the clipboard write without the paste.
    """
    if sys.platform == "win32":
        _copy_windows(text)
        return
    import pyperclip

    pyperclip.copy(text)


def inject(text: str, target_hwnd: int | None = None) -> bool:
    """Clipboard first, then simulated paste. Returns whether it pasted.

    Two safety properties, in order of importance:

    1. The clipboard is written first, so the text is always recoverable by
       hand even if everything after this fails.
    2. **If we cannot get focus back to the window you started in, we do not
       paste at all.** Leaving text on the clipboard is a minor inconvenience.
       Pasting your dictation into a colleague's chat window is not.
    """
    import pyautogui

    copy_only(text)

    if not _restore_target_window(target_hwnd):
        return False

    # THE MODIFIER TRAP. Single-key hotkeys make this far less likely than the
    # old Ctrl+Shift+Space chord did, but Right Ctrl and Right Alt are still
    # modifiers: if one is held when the paste fires, the OS sees Ctrl+Ctrl+V
    # or Alt+Ctrl+V. Force every modifier up first.
    #
    # pyautogui sleeps 0.1s after EVERY call it makes, so that applications are
    # not overwhelmed by synthetic input arriving faster than a human could
    # produce it. Eight releases therefore cost 800ms of pure waiting, measured
    # 2026-09-05, and that was the bulk of the "unexplained" fixed overhead on
    # every capture. Releasing a key takes microseconds; the pacing is the whole
    # cost. Suspended for this loop only, then restored before the paste itself,
    # which is the one keystroke that genuinely has to land.
    #
    # The safety this loop provides is unchanged: the keys are still released,
    # and the deliberate settle pause below is what actually gives the target
    # window time to notice.
    #
    # THE CORNER TRAP. pyautogui refuses every call while the pointer sits in a
    # screen corner: a "failsafe" meant to stop a script that runs away with the
    # mouse. Sibyl never moves the mouse, so it protected nothing and failed the
    # paste instead, as "Something went wrong", for anyone who parks the pointer
    # where Windows shows the desktop (review round 3, all seven reviewers). Off
    # for these few key presses only, then put back.
    previous_failsafe = pyautogui.FAILSAFE
    pyautogui.FAILSAFE = False
    try:
        previous_pause = pyautogui.PAUSE
        pyautogui.PAUSE = 0
        try:
            for key in ("ctrl", "ctrlright", "shift", "shiftright", "alt", "altright",
                        "command", "option"):
                try:
                    pyautogui.keyUp(key)
                except Exception:
                    pass  # Key name not valid on this platform. Harmless.
        finally:
            pyautogui.PAUSE = previous_pause

        time.sleep(0.25)  # Let the target window process the key-ups.

        if IS_MAC:
            pyautogui.hotkey("command", "v")
        else:
            pyautogui.hotkey("ctrl", "v")
    finally:
        pyautogui.FAILSAFE = previous_failsafe
    return True


# ---------------------------------------------------------------------------
# 7. HOTKEYS
# ---------------------------------------------------------------------------


def key_code(key) -> int | None:
    """The numeric code Windows uses for a key, or None if it has none.

    Everything matches on this number rather than on a name. The reason is a
    trap that cost a debugging session: pynput exposes Key.alt_r and
    Key.alt_gr as two DIFFERENT objects that compare unequal, yet both carry
    virtual-key code 165, because they are the same physical key. A binding
    stored under one name never matches an event delivered under the other,
    and it fails silently, which looks exactly like broken hardware.
    """
    vk = getattr(key, "vk", None)
    if vk is None:
        vk = getattr(getattr(key, "value", None), "vk", None)
    return vk


def parse_binding(name: str) -> frozenset:
    """Turn a config string into the set of key codes that must be held.

    'ctrl_r'         -> one key,  tapped alone
    'ctrl_r+alt_r'   -> two keys, held together
    """
    from pynput import keyboard

    codes = set()
    parts = [p for p in name.replace(" ", "").split("+") if p]
    if not parts:
        raise ValueError(f"Empty hotkey definition: '{name}'")

    for part in parts:
        clean = part.strip().lower().lstrip("<").rstrip(">")
        if hasattr(keyboard.Key, clean):
            code = key_code(getattr(keyboard.Key, clean))
        elif len(clean) == 1:
            code = key_code(keyboard.KeyCode.from_char(clean))
        else:
            raise ValueError(
                f"Unknown key '{part}' in '{name}'. Use a pynput name such as "
                f"alt_r, ctrl_r, f9, or a single character. Combine with '+'."
            )
        if code is None:
            raise ValueError(f"Key '{part}' has no virtual-key code on this platform.")
        codes.add(code)

    return frozenset(codes)


class TapListener:
    """Fires when an exact set of keys is tapped together and nothing else.

    This exists because of a real collision. Right Ctrl and Right Alt are
    genuine modifiers: binding them naively means every Ctrl+C typed with the
    right-hand Ctrl would trigger Sibyl.

    So the listener watches a whole *gesture*: from the moment the first bound
    key goes down until the last one comes back up. It records the largest set
    of bound keys that were held at the same time, and fires only if that set
    exactly matches a binding, nothing unbound was touched, and the whole thing
    took less than max_hold.

    Recording the largest simultaneous set is what makes finger order
    irrelevant. Ctrl-then-Alt and Alt-then-Ctrl both end up as {ctrl, alt}, so
    the chord fires either way, and a binding on a subset of those keys never
    fires by mistake.

    Hold-to-talk, added 2026-09-11, uses three optional hooks on top of the
    tap, each given the gesture's key set:

      on_chord_down    the instant a binding's exact keys are all down. This
                       is when recording starts, so no words are lost to the
                       time it takes to decide between a tap and a hold.
      on_hold_release  the chord was held longer than max_hold, then let go.
                       Without this hook a long hold is rejected, as before.
      on_abort         another key joined in after on_chord_down fired: it
                       was Ctrl+Alt+something, not a gesture for us.

    A quick release still calls the binding's own callback, exactly as it
    always has. The hooks are meant for one binding; with two bindings where
    one is a subset of the other, on_chord_down would fire for both.
    """

    def __init__(self, bindings: dict, max_hold: float = TAP_MAX_HOLD) -> None:
        self._bindings = bindings              # frozenset of key codes -> callback
        self._max_hold = max_hold
        self._watched = set().union(*bindings) if bindings else set()
        self._held: set = set()                # bound keys down right now
        self._peak: set = set()                # most that were down together
        self._started_at: float | None = None
        self._polluted = False                 # an unbound key joined in
        self._announced = False                # on_chord_down fired this gesture
        self.on_reject = None                  # diagnostics hook, used by --test-hotkeys
        self.on_chord_down = None
        self.on_hold_release = None
        self.on_abort = None

    def _reset(self) -> None:
        self._held.clear()
        self._peak = set()
        self._started_at = None
        self._polluted = False
        self._announced = False

    def _on_press(self, key) -> None:
        code = key_code(key)

        if code in self._watched:
            if self._started_at is None:
                self._started_at = time.time()
            self._held.add(code)
            # Peak, not current: the set must survive one key being released
            # a moment before the other.
            if len(self._held) > len(self._peak):
                self._peak = set(self._held)
            chord = frozenset(self._held)
            if (self.on_chord_down is not None and not self._polluted
                    and not self._announced and chord in self._bindings):
                self._announced = True
                self.on_chord_down(chord)
            return

        # Some other key went down. Whatever is in flight is a combo, not a
        # deliberate tap, so it must not fire.
        if self._started_at is not None:
            self._polluted = True

    def _on_release(self, key) -> None:
        code = key_code(key)
        if code not in self._watched or self._started_at is None:
            return

        self._held.discard(code)
        if self._held:
            return  # Part of the gesture is still down. Not finished yet.

        gesture = frozenset(self._peak)
        held_for = time.time() - self._started_at
        polluted = self._polluted
        announced = self._announced
        self._reset()

        if polluted:
            if announced and self.on_abort is not None:
                self.on_abort(gesture)
            self._reject(gesture, "another key was pressed during it")
            return
        if held_for > self._max_hold:
            if announced and self.on_hold_release is not None:
                self.on_hold_release(gesture)
                return
            self._reject(gesture, f"held {held_for:.1f}s, longer than {self._max_hold}s")
            return

        callback = self._bindings.get(gesture)
        if callback is None:
            self._reject(gesture, "not a configured combination")
            return

        callback()

    def _reject(self, gesture, why: str) -> None:
        if self.on_reject is not None:
            self.on_reject(gesture, why)

    def start(self) -> None:
        from pynput import keyboard

        self._listener = keyboard.Listener(
            on_press=self._on_press, on_release=self._on_release
        )
        self._listener.daemon = True
        self._listener.start()


def _pretty(spec: str) -> str:
    """'ctrl_r+alt_r' -> 'hold ctrl_r + alt_r'. Human-facing labels only."""
    parts = [p for p in spec.replace(" ", "").split("+") if p]
    return f"hold {' + '.join(parts)}" if len(parts) > 1 else f"tap {parts[0]}"


def describe_binding(codes) -> str:
    """Render a set of key codes back into names, for logs and the tray."""
    from pynput import keyboard

    names = []
    for code in sorted(codes):
        match = next(
            (k.name for k in keyboard.Key if key_code(k) == code), None
        )
        names.append(match or f"vk{code}")
    return " + ".join(names)


# ---------------------------------------------------------------------------
# 8. APPLICATION
# ---------------------------------------------------------------------------

STATE_COLOURS = {
    # Traffic lights, chosen for the notch on 2026-09-11, and a grey dot at
    # rest. The tray icon is drawn from the same table.
    "idle": (90, 100, 115),       # slate: at rest
    "loading": (225, 170, 40),    # yellow: busy, like working
    "recording": (200, 60, 60),   # red: listening
    "thinking": (225, 170, 40),   # yellow: working
    "done": (60, 165, 90),        # green: pasted
    "failed": (200, 60, 60),      # red, always with words saying what failed
}


def _make_icon_image(state: str):
    """A filled circle on a transparent square. Generated rather than shipped as
    a .png so there is no binary asset to keep in sync with the code."""
    from PIL import Image, ImageDraw

    size = 64
    image = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    draw = ImageDraw.Draw(image)
    draw.ellipse(
        (8, 8, size - 8, size - 8),
        fill=STATE_COLOURS.get(state, STATE_COLOURS["idle"]),
    )
    return image


def _brand_icon_image():
    """The Sibyl logo, for the tray once the notch is showing the state. The
    same file as the desktop icon. None if it cannot be read: a picture is not
    worth the app, exactly as the notch itself is not."""
    try:
        from PIL import Image

        return Image.open(Path(__file__).with_name("sibyl.ico")).convert("RGBA")
    except Exception as exc:
        print(f"[tray] no logo, keeping the coloured dot: {type(exc).__name__}")
        return None


# -- the notch ----------------------------------------------------------
# The tray icon above already carries these states, and that turned out not to
# be enough. A tray icon is something you have to go and LOOK at, and the whole
# premise of Sibyl is that you never look away from what you were writing.
# Twenty seconds of silence with nothing moving on screen is indistinguishable
# from a crash, so the state also goes somewhere it cannot be missed.
#
# Since roadmap step 5 (2026-09-13) that somewhere is the notch, mockup C: a
# small dot that always sits above the taskbar and grows into a chip, a
# coloured dot and one word. The words of a dictation never appear on it: the
# notch is always on screen, screen shares included, so it shows how long Sibyl
# has been working and never what you said.
#
# Since 2026-09-14 it is drawn by Pillow rather than by Tk, because the Tk
# version looked pixelated. Tk draws every curve without blending edge pixels, and can
# only make a pixel fully see-through or not at all. Pillow draws the notch four
# times too large and shrinks it, so an edge pixel can be part covered, and
# Windows is handed the picture with a see-through level for every pixel
# (_push_layered). Tk still owns the window, its clicks and its timing; it just
# no longer paints it. The values come from the design check of 2026-09-14.
#
# Since 2026-10-04 it can be dragged anywhere on the main screen, because at
# the bottom centre it sometimes covered something you needed, and the dot
# became a microphone on the same coloured disc (design C). Where it is
# dropped is kept in NOTCH_SPOT_FILE; a click still starts listening.

# Sizes at 96 dots per inch, multiplied for the real screen, so the notch is the
# same physical size on any display.
NOTCH_HEIGHT = 28
NOTCH_DISC = 19           # the coloured disc with the microphone on it; was a 10 px dot
NOTCH_MARGIN_BOTTOM = 8   # gap above the taskbar, which Windows measures for us
# Where the notch was last dragged to. Written only when it is dropped, as a
# share of the usable screen; never in .env, which is yours and Sibyl never
# writes. A module constant, like LOG_FILE, so a test can point it elsewhere.
NOTCH_SPOT_FILE = LOG_FILE.with_name("notch.json")
NOTCH_DONE_MS = 3000      # "Done" shows this long, then the chip shrinks to the dot
NOTCH_FAILED_MS = 8000    # a failure stays longer: it has words to read
NOTCH_GROW_MS = 160       # the chip grows and shrinks rather than jumping
NOTCH_SS = 4              # drawn this many times too large, then shrunk: the smoothing
PANEL_WIDTH = 360         # the right-click panel with the last text
PANEL_MAX_LINES = 8       # a long dictation is cut short in the panel, never in the paste

# The brand toolkit's surfaces (brand/SIBYL_BRAND_TOOLKIT.md). Its rule of
# accents "only sparingly" gives way to the traffic lights above, deliberately.
NOTCH_BG = (17, 17, 19)          # Carbon
NOTCH_BG_DOWN = (27, 27, 30)     # Carbon, lit a little while pressed
NOTCH_FG = (245, 245, 242)       # Sibyl White
NOTCH_DIM = (184, 184, 188)      # Fog
# Two hairlines instead of a shadow. A dark one outside gives the chip an edge
# on a white page; a faint light one inside gives it one on a black taskbar.
# The brand prefers thin borders to shadows, and a shadow's soft halo would also
# catch clicks meant for the window underneath: every pixel of the notch's
# picture that is not fully see-through belongs to the notch.
NOTCH_RING_OUT = (0, 0, 0, 90)
NOTCH_RING_IN = (38, 38, 41, 255)          # white at 9% over Carbon
# Behind the tick, the cross and Paste again: white at 7% at rest, 13% under
# the pointer, 20% while pressed.
NOTCH_BUTTON_FILL = ((255, 255, 255, 18), (255, 255, 255, 34), (255, 255, 255, 52))
NOTCH_FONT = "seguisb.ttf"       # Segoe UI Semibold. The brand's Inter does not come with Windows
NOTCH_TEXT_FONT = "segoeui.ttf"  # Segoe UI, for the panel's text
NOTCH_POINTS = 10

NOTCH_WORDS = {
    "loading": "Waking up",
    "recording": "Listening",
    "thinking": "Working",
    "done": "Done",
    "failed": "Something went wrong",   # callers normally say what
}


def _notch_font(name: str, points: float, scale: float):
    """A Windows font at `points`, sized for this screen and for drawing
    NOTCH_SS times too large. Pillow's own font if it is missing: a plain
    notch is better than none."""
    from PIL import ImageFont

    size = round(points * 96 / 72 * scale * NOTCH_SS)
    folder = os.path.join(os.environ.get("WINDIR", r"C:\Windows"), "Fonts")
    try:
        return ImageFont.truetype(os.path.join(folder, name), size)
    except OSError:
        return ImageFont.load_default(size)


def _mix(rgb: tuple, other: tuple, amount: float) -> tuple:
    return tuple(round(a + (b - a) * amount) for a, b in zip(rgb, other))


def _stroke(draw, points: list, fill: tuple, width: float) -> None:
    """A line with round ends, which Pillow does not draw by itself. The brand's
    icons are 1.5-pixel strokes with round caps."""
    draw.line(points, fill=fill, width=round(width), joint="curve")
    r = width / 2
    for x, y in (points[0], points[-1]):
        draw.ellipse((x - r, y - r, x + r, y + r), fill=fill)


def _plate(size: tuple, radius: float, fill: tuple, one: int):
    """The surface every notch window is made of: the dark hairline outside, the
    light hairline inside, then the plate. `one` is one screen pixel at drawing
    size. Returns the picture, and a mask of the plate's inside to cut the
    contents to."""
    from PIL import Image, ImageDraw

    w, h = size
    base = Image.new("RGBA", size, (0, 0, 0, 0))
    draw = ImageDraw.Draw(base)
    draw.rounded_rectangle((0, 0, w - 1, h - 1), radius=radius, fill=NOTCH_RING_OUT)
    draw.rounded_rectangle((one, one, w - 1 - one, h - 1 - one),
                           radius=radius - one, fill=NOTCH_RING_IN)
    inner = (2 * one, 2 * one, w - 1 - 2 * one, h - 1 - 2 * one)
    draw.rounded_rectangle(inner, radius=radius - 2 * one, fill=fill + (255,))
    mask = Image.new("L", size, 0)
    ImageDraw.Draw(mask).rounded_rectangle(inner, radius=radius - 2 * one, fill=255)
    return base, mask


def _microphone(draw, cx: float, cy: float, u: float, fill: tuple) -> None:
    """A microphone on the disc, `u` being one pixel at 96 dpi in drawing
    pixels: a rounded head, the cradle under it, a stem and a base. Drawn from
    shapes, like the tick and the cross, so it needs no icon font."""
    stroke = 1.5 * u
    draw.rounded_rectangle((cx - 2.5 * u, cy - 6.5 * u, cx + 2.5 * u, cy + 1.5 * u),
                           radius=2.5 * u, fill=fill)
    # The cradle: the lower half of a ring, its ends rounded by hand.
    draw.arc((cx - 5 * u, cy - 6 * u, cx + 5 * u, cy + 4 * u), 0, 180,
             fill=fill, width=round(stroke))
    r = stroke / 2
    for end in (-1, 1):
        x = cx + end * (5 * u - r)
        draw.ellipse((x - r, cy - u - r, x + r, cy - u + r), fill=fill)
    _stroke(draw, [(cx, cy + 4 * u - r), (cx, cy + 6 * u)], fill, stroke)
    _stroke(draw, [(cx - 2.5 * u, cy + 6 * u), (cx + 2.5 * u, cy + 6 * u)], fill, stroke)


def _luminance(rgb: tuple) -> float:
    """How bright a colour looks, by the formula the contrast rules use."""
    def channel(c):
        c /= 255
        return c / 12.92 if c <= 0.04045 else ((c + 0.055) / 1.055) ** 2.4
    r, g, b = (channel(c) for c in rgb)
    return 0.2126 * r + 0.7152 * g + 0.0722 * b


def _contrast(a: tuple, b: tuple) -> float:
    """The contrast between two colours, from 1 to 21. A graphic needs 3."""
    hi, lo = sorted((_luminance(a), _luminance(b)), reverse=True)
    return (hi + 0.05) / (lo + 0.05)


def _mic_colour(disc: tuple) -> tuple:
    """Sibyl White or Carbon, whichever stands out more on this disc. Worked
    out rather than listed: white on the yellow is 1.9 to 1 and on the green
    2.9 to 1, both under the 3 a graphic needs, so those two get a dark one."""
    return max((NOTCH_FG, NOTCH_BG), key=lambda c: _contrast(c, disc))


def _past_drag_limit(dx: int, dy: int, limit: tuple) -> bool:
    """Has a press moved far enough to be a drag rather than a click? `limit`
    is Windows' own drag threshold, the distance every program uses."""
    return abs(dx) > limit[0] or abs(dy) > limit[1]


def _keep_inside(anchor: tuple, size: tuple, area: tuple) -> tuple:
    """The top-left corner of a box of `size` centred on `anchor`, moved the
    least that keeps it inside `area` (left, top, right, bottom). So near an
    edge the chip grows toward the side with room."""
    (ax, ay), (w, h), (left, top, right, bottom) = anchor, size, area
    return (min(max(round(ax - w / 2), left), right - w),
            min(max(round(ay - h / 2), top), bottom - h))


def _panel_spot(notch: tuple, size: tuple, gap: int, area: tuple) -> tuple:
    """Where the right-click panel goes: centred on the notch (x, y, w, h),
    above it, or below when there is no room above, and inside `area`."""
    x, y, w, h = notch
    ph = size[1]
    py = y - gap - ph
    if py < area[1]:
        py = y + h + gap
    return _keep_inside((x + w / 2, py + ph / 2), size, area)


def load_spot(path: Path) -> tuple | None:
    """Where the notch was last dropped, as shares of the usable screen, or
    None for the default spot. A missing, unreadable or nonsensical file means
    the default too, never an error: a remembered position is not worth a
    failed start. Only a missing file is silent, because that is every first
    run; anything else is said in the log, or it looks like a first run too."""
    try:
        spot = json.loads(path.read_text(encoding="utf-8"))
        x, y = float(spot["x"]), float(spot["y"])
    except FileNotFoundError:
        return None
    except Exception as exc:
        print(f"[pill] could not read where the notch was left, so it starts at "
              f"the bottom: {type(exc).__name__}")
        return None
    if 0 <= x <= 1 and 0 <= y <= 1:
        return (x, y)
    print("[pill] where the notch was left is off the screen, so it starts at the bottom")
    return None


def save_spot(path: Path, spot: tuple | None) -> None:
    """Remember where the notch was dropped; None forgets it. A failure is
    reported, never raised: the notch still works where it is.

    Written to a spare file and swapped in with one rename, so a save cut short
    leaves the last good spot rather than half a file."""
    if spot is None:
        try:
            path.unlink(missing_ok=True)
        except OSError as exc:
            print(f"[pill] could not forget where the notch was, so it may go "
                  f"back there after a restart: {type(exc).__name__}")
        return
    spare = path.with_name(path.name + ".tmp")
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        spare.write_text(json.dumps({"x": spot[0], "y": spot[1]}), encoding="utf-8")
        os.replace(spare, path)
    except OSError as exc:
        print(f"[pill] could not save where the notch is: {type(exc).__name__}")


def _wrap(text: str, font, width: float) -> list:
    """Break `text` into lines no wider than `width`, at spaces."""
    lines = []
    for paragraph in text.split("\n"):
        line = ""
        for word in paragraph.split():
            trial = f"{line} {word}".strip()
            if line and font.getlength(trial) > width:
                lines.append(line)
                line = word
            else:
                line = trial
        lines.append(line)
    return lines


_LAYERED = {}


def _layered_api() -> dict:
    """The Windows calls behind the notch's smooth edges, declared once. They
    get their own copies of user32 and gdi32, so declaring argument types here
    cannot change how the rest of the file calls the same functions. Declaring
    them is not optional: without it, 64-bit handles are cut to 32 bits."""
    if not _LAYERED:
        import ctypes.wintypes as wt

        class BLENDFUNCTION(ctypes.Structure):
            _fields_ = [("BlendOp", ctypes.c_ubyte), ("BlendFlags", ctypes.c_ubyte),
                        ("SourceConstantAlpha", ctypes.c_ubyte),
                        ("AlphaFormat", ctypes.c_ubyte)]

        class BITMAPINFOHEADER(ctypes.Structure):
            _fields_ = [("biSize", wt.DWORD), ("biWidth", wt.LONG),
                        ("biHeight", wt.LONG), ("biPlanes", wt.WORD),
                        ("biBitCount", wt.WORD), ("biCompression", wt.DWORD),
                        ("biSizeImage", wt.DWORD), ("biXPelsPerMeter", wt.LONG),
                        ("biYPelsPerMeter", wt.LONG), ("biClrUsed", wt.DWORD),
                        ("biClrImportant", wt.DWORD)]

        u = ctypes.WinDLL("user32", use_last_error=True)
        g = ctypes.WinDLL("gdi32", use_last_error=True)
        u.GetDC.restype = wt.HDC
        u.GetDC.argtypes = [wt.HWND]
        u.ReleaseDC.argtypes = [wt.HWND, wt.HDC]
        u.GetWindowLongW.argtypes = [wt.HWND, ctypes.c_int]
        u.SetWindowLongW.argtypes = [wt.HWND, ctypes.c_int, wt.LONG]
        u.SetWindowPos.argtypes = [wt.HWND, wt.HWND, ctypes.c_int, ctypes.c_int,
                                   ctypes.c_int, ctypes.c_int, wt.UINT]
        u.UpdateLayeredWindow.argtypes = [
            wt.HWND, wt.HDC, ctypes.POINTER(wt.POINT), ctypes.POINTER(wt.SIZE),
            wt.HDC, ctypes.POINTER(wt.POINT), wt.DWORD,
            ctypes.POINTER(BLENDFUNCTION), wt.DWORD]
        g.CreateCompatibleDC.restype = wt.HDC
        g.CreateCompatibleDC.argtypes = [wt.HDC]
        g.CreateDIBSection.restype = wt.HBITMAP
        g.CreateDIBSection.argtypes = [wt.HDC, ctypes.c_void_p, wt.UINT,
                                       ctypes.POINTER(ctypes.c_void_p),
                                       wt.HANDLE, wt.DWORD]
        g.SelectObject.restype = wt.HGDIOBJ
        g.SelectObject.argtypes = [wt.HDC, wt.HGDIOBJ]
        g.DeleteObject.argtypes = [wt.HGDIOBJ]
        g.DeleteDC.argtypes = [wt.HDC]
        _LAYERED.update(u=u, g=g, wt=wt, blend=BLENDFUNCTION,
                        header=BITMAPINFOHEADER)
    return _LAYERED


def _on_top(hwnd) -> None:
    """Back to the front of the always-on-top windows, never activated."""
    if hwnd:
        HWND_TOPMOST, SWP_NOSIZE, SWP_NOMOVE, SWP_NOACTIVATE = -1, 0x1, 0x2, 0x10
        _layered_api()["u"].SetWindowPos(hwnd, HWND_TOPMOST, 0, 0, 0, 0,
                                         SWP_NOSIZE | SWP_NOMOVE | SWP_NOACTIVATE)


def _push_layered(hwnd, image, x: int, y: int) -> bool:
    """Hand Windows the notch's picture, a see-through level for every pixel,
    and where to put it. This is what makes the edges smooth.

    Windows refuses the call (error 87) once Tk has hidden and shown the window,
    measured 2026-09-14. Switching the layered style off and on again clears
    that, so a refusal is retried once that way."""
    from PIL import Image

    api = _layered_api()
    u, g, wt = api["u"], api["g"], api["wt"]
    w, h = image.size
    # Windows wants blue first, and each colour already multiplied by its
    # see-through level, which is Pillow's "RGBa".
    r, gr, b, a = image.convert("RGBa").split()
    data = Image.merge("RGBA", (b, gr, r, a)).tobytes()

    def attempt():
        screen = u.GetDC(None)
        dc = g.CreateCompatibleDC(screen)
        header = api["header"](ctypes.sizeof(api["header"]), w, -h, 1, 32,
                               0, 0, 0, 0, 0, 0)
        bits = ctypes.c_void_p()
        bitmap = g.CreateDIBSection(dc, ctypes.byref(header), 0,
                                    ctypes.byref(bits), None, 0)
        try:
            ctypes.memmove(bits, data, len(data))
            old = g.SelectObject(dc, bitmap)
            ok = u.UpdateLayeredWindow(
                hwnd, screen, ctypes.byref(wt.POINT(x, y)),
                ctypes.byref(wt.SIZE(w, h)), dc, ctypes.byref(wt.POINT(0, 0)),
                0, ctypes.byref(api["blend"](0, 0, 255, 1)), 2)  # AC_SRC_ALPHA, ULW_ALPHA
            error = ctypes.get_last_error()
            g.SelectObject(dc, old)
            return bool(ok), error
        finally:
            g.DeleteObject(bitmap)
            g.DeleteDC(dc)
            u.ReleaseDC(None, screen)

    ok, error = attempt()
    if not ok and error == 87:
        WS_EX_LAYERED = 0x00080000
        style = u.GetWindowLongW(hwnd, -20)
        u.SetWindowLongW(hwnd, -20, style & ~WS_EX_LAYERED)
        u.SetWindowLongW(hwnd, -20, style | WS_EX_LAYERED)
        ok, error = attempt()
    if not ok:
        print(f"[pill] Windows would not take the notch's picture (error {error})")
    return ok


class Pill:
    """The notch: a microphone on a coloured disc, above the taskbar unless
    you drag it elsewhere, that says what Sibyl is doing.

    Still called Pill, after the window it replaced, to keep the change to the
    rest of the file small. Mockup C, roadmap step 5.

    Four rules, and each one is load-bearing rather than stylistic:

    1. **It must never take focus.** Sibyl pastes back into the window that
       was in front when you started speaking. A window that stole focus would
       BECOME that window, and would break the single most important fix in the
       project. That is enforced by the operating system in _refuse_focus below,
       not hoped for. It holds for a click too, which is what makes a click on
       the dot a safe way to start: the window you were typing in is still in
       front when the click is handled.

    2. **Only the thread that built the window may touch it.** Tk is not
       thread-safe and the capture runs on a worker thread, so every other
       thread posts a message onto a queue and the Tk loop applies it.

    3. **It is optional.** If the window cannot be built, Sibyl runs exactly
       as it did before, the same way a machine with no speakers still runs the
       whole pipeline. A progress display is not worth losing the product to.

    4. **A click is handed straight on.** The on_* callbacks are Sibyl's own
       methods. Like the keyboard callbacks, they return at once and do
       anything slow on a worker thread, because this thread is also the one
       that keeps the notch drawn.

    What you see is a picture Pillow drew (_render). The Tk canvas underneath is
    never seen: it holds invisible shapes where the dot, the tick and the cross
    are, so Tk can still say which one a click landed on.
    """

    def __init__(self) -> None:
        self.root = None
        self._queue = queue.Queue()
        self._canvas = None
        self._hwnd = None
        self._scale = 1.0         # screen pixels per 1/96 inch
        self._font = None         # the chip's word, at drawing size
        self._text_font = None    # the panel's text
        self._small_font = None   # the panel's label
        self._state = "idle"
        self._word = ""
        self._since = 0.0         # when "Working" began, for its seconds counter
        self._runs = []           # what the chip says, as (words, colour)
        self._text = ""           # the same as plain text
        self._lay = None          # where everything goes, at the chip's full width
        self._drawn = None        # (state, text, hover, press) on screen now
        self._shown_w = None      # the width on screen, mid-growth included
        self._grow = None         # (from width, to width, start time) while growing
        self._hit_key = None
        self._hot = None          # the button under the pointer
        self._down = None         # the button, or the body, being pressed
        self._image = None        # the last picture handed to Windows
        self._spot = None         # where it was dragged to, as shares of the screen
        self._at = None           # its top-left corner on screen now
        self._grip = None         # (pointer x, y, where it rested, dragging?) while pressed
        self._drag_limit = (4, 4)  # Windows' own, asked for in build()
        self._panel = None
        self._panel_canvas = None
        self._panel_hwnd = None
        self._panel_hit_key = None
        self._panel_text = ""
        self._panel_image = None
        self._panel_hot = None    # "paste" or "quit" under the pointer
        self._panel_down = None   # the one being pressed
        self._rest_job = None
        self._drain_job = None
        self._display_fault = False   # said already, quiet until a drawing works
        self._grow_job = None
        self._jobs = set()        # other pending after() calls, for close()
        # Set by Sibyl. Called on the Tk thread; each must return at once.
        self.on_click = None        # left-click on the dot at rest: listen
        self.on_send = None         # the tick
        self.on_cancel = None       # the cross
        self.on_paste_again = None  # the panel's button
        self.on_quit = None         # the panel's "Quit Sibyl"
        self.last_text = lambda: ""

    # -- safe to call from ANY thread ---------------------------------------
    # Nothing here touches a widget. It only puts a message on the queue, which
    # is exactly why it is safe from the capture worker.

    def set_state(self, state: str, word: str = "", stay: bool = False) -> None:
        """`word` replaces the state's usual word: a failure says what failed.
        `stay` keeps a failure on show until the next state, for a problem
        found at start, when nobody may be looking yet."""
        self._queue.put((state, word, stay))

    # -- MAIN thread only ---------------------------------------------------

    def build(self) -> bool:
        """Create the notch and show the resting dot. Returns whether it worked.

        Every failure in here is survivable, so they are reported and swallowed
        rather than raised.
        """
        try:
            import tkinter as tk
            from PIL import Image  # noqa: F401  (the drawing needs it; fail here)
        except Exception as exc:
            print(f"[pill] no tkinter or Pillow on this machine, running without "
                  f"the notch: {type(exc).__name__}: " + private(str(exc), "(details not logged)"))
            return False

        # Real-pixel mode, switched on BEFORE the window exists. Fixed
        # 2026-09-11 after the pill turned up two thirds of the way up the
        # screen. On a scaled display (this one is 125%) Windows describes the
        # screen to a program either in real pixels or in enlarged units, and
        # pyautogui flips the process into real pixels the moment it is
        # imported, which happens at the end of boot, after this window has
        # been placed. The pill's coordinates stayed the same numbers while
        # their meaning changed underneath them. Switching first means one
        # unit of measurement for the whole life of the process; pyautogui's
        # own call later is then a no-op. Same call pyautogui makes, so the
        # result is identical to what it would have produced anyway.
        if not IS_MAC:
            try:
                ctypes.windll.user32.SetProcessDPIAware()
            except Exception:
                pass  # Older Windows. It will place slightly wrong, not fail.

        try:
            root = tk.Tk()
            if _logging:
                root.report_callback_exception = report_crash
            root.overrideredirect(True)   # no title bar, no close button
            root.attributes("-topmost", True)
            # In real-pixel mode Tk measures the true screen: 1.25 here.
            self._scale = root.winfo_fpixels("1i") / 96
            self._font = _notch_font(NOTCH_FONT, NOTCH_POINTS, self._scale)
            self._text_font = _notch_font(NOTCH_TEXT_FONT, NOTCH_POINTS, self._scale)
            self._small_font = _notch_font(NOTCH_FONT, 7.5, self._scale)
            self._spot = load_spot(NOTCH_SPOT_FILE)
            if not IS_MAC:
                SM_CXDRAG, SM_CYDRAG = 68, 69
                metric = ctypes.windll.user32.GetSystemMetrics
                self._drag_limit = (metric(SM_CXDRAG) or 4, metric(SM_CYDRAG) or 4)

            canvas = tk.Canvas(root, bg="black", highlightthickness=0,
                               borderwidth=0)
            canvas.pack()
            # A click on the body counts when the button comes up, not when it
            # goes down: only then can it be told from a drag. The tick and the
            # cross still answer on press, and are not handles. The press must
            # land on the body; the moving and the letting go are watched on
            # the whole canvas, because a chip that grows or shrinks under the
            # button rebuilds the body, and a release bound to the old one went
            # nowhere (2026-10-04). _grip says whether the body was pressed.
            canvas.tag_bind("body", "<ButtonPress-1>", self._grab)
            canvas.bind("<B1-Motion>", self._drag)
            canvas.bind("<ButtonRelease-1>", self._let_go)
            canvas.tag_bind("send", "<ButtonPress-1>",
                            lambda _e: self._key("send", self.on_send))
            canvas.tag_bind("cancel", "<ButtonPress-1>",
                            lambda _e: self._key("cancel", self.on_cancel))
            for key in ("send", "cancel"):
                canvas.tag_bind(key, "<Enter>", lambda _e, k=key: self._hover(k))
                canvas.tag_bind(key, "<Leave>", lambda _e: self._hover(None))
            canvas.bind("<ButtonPress-3>", self._toggle_panel)
            self.root, self._canvas = root, canvas

            # The old pill's order, kept on purpose: shown once so Windows
            # makes the real window, given the no-activate style, hidden, and
            # shown for good by the first _drain. A test window that was never
            # hidden took the foreground as it appeared (2026-09-13).
            self._draw()
            root.update_idletasks()
            self._hwnd = self._refuse_focus()
            root.withdraw()
            self._drawn = None   # so the first _drain draws it and shows it
            self._drain_job = root.after(60, self._drain)
            return True
        except Exception as exc:
            print(f"[pill] could not build the window, carrying on without "
                  f"it: {type(exc).__name__}: " + private(str(exc), "(details not logged)"))
            self.root = None
            return False

    @staticmethod
    def _usable_area(root) -> tuple:
        """Left, top, right and bottom edges of the screen the taskbar leaves free.

        Asked of Windows rather than assumed, so the notch sits directly on top
        of the taskbar whatever its height, its scaling, or whether it is set to
        auto-hide. The previous fixed 90-pixel margin was a guess at the
        taskbar's height in one particular setup.
        """
        if not IS_MAC:
            try:
                class RECT(ctypes.Structure):
                    _fields_ = [("left", ctypes.c_long), ("top", ctypes.c_long),
                                ("right", ctypes.c_long), ("bottom", ctypes.c_long)]

                work = RECT()
                SPI_GETWORKAREA = 0x0030
                if ctypes.windll.user32.SystemParametersInfoW(
                        SPI_GETWORKAREA, 0, ctypes.byref(work), 0):
                    return work.left, work.top, work.right, work.bottom
            except Exception:
                pass
        # The whole screen. Slightly low on Windows if this is ever reached,
        # never lost off the edge.
        return 0, 0, root.winfo_screenwidth(), root.winfo_screenheight()

    def _refuse_focus(self, window=None):
        """Ask Windows never to activate this window: the notch, unless told.
        Returns the window's real handle.

        This is the one part of the notch that is not cosmetic. WS_EX_NOACTIVATE
        tells Windows that showing or clicking this window must not make it the
        foreground window, which is what protects the paste target.
        WS_EX_TOOLWINDOW keeps it out of Alt-Tab, where a status dot has no
        business being either. WS_EX_LAYERED is what lets Windows show the
        picture _push_layered hands it.

        Re-applied after every show, because withdrawing and re-showing a
        borderless Tk window can rebuild it with default styles.
        """
        window = window or self.root
        if IS_MAC or window is None:
            return None
        try:
            GWL_EXSTYLE = -20
            WS_EX_NOACTIVATE = 0x08000000
            WS_EX_TOOLWINDOW = 0x00000080
            WS_EX_LAYERED = 0x00080000
            user32 = ctypes.windll.user32
            hwnd = window.winfo_id()
            hwnd = user32.GetParent(hwnd) or hwnd
            style = user32.GetWindowLongW(hwnd, GWL_EXSTYLE)
            user32.SetWindowLongW(
                hwnd, GWL_EXSTYLE,
                style | WS_EX_NOACTIVATE | WS_EX_TOOLWINDOW | WS_EX_LAYERED,
            )
            return hwnd
        except Exception as exc:
            print(f"[pill] could not set the no-activate style: "
                  f"{type(exc).__name__}: " + private(str(exc), "(details not logged)"))
            return None

    def _later(self, ms: int, fn) -> None:
        """root.after, remembered, so close() can cancel it."""
        job = None

        def run():
            self._jobs.discard(job)
            if self.root is not None:
                fn()

        job = self.root.after(ms, run)
        self._jobs.add(job)

    def _drain(self) -> None:
        """Apply everything the other threads have posted, and keep the seconds
        counter moving. Runs on the Tk thread every 60ms."""
        if self.root is None:
            return   # closed between this callback being scheduled and running
        try:
            while not self._queue.empty():
                self._apply_state(*self._queue.get_nowait())
            before = self._drawn
            drew = self._draw()
            if drew and (before is None or before[0] != self._state):
                self._show()
            if drew:   # a tick with nothing new to draw proves nothing works
                self._display_fault = False
        except Exception as exc:
            # A fault in the display must never take the pipeline down with it.
            # This runs every 60ms, so one that stays is said once, not a
            # thousand times a minute.
            if not self._display_fault:
                print(f"[pill] display error, ignored until the drawing works "
                      f"again: {type(exc).__name__}: " + private(str(exc), "(details not logged)"))
            self._display_fault = True
        if self.root is not None:
            self._drain_job = self.root.after(60, self._drain)

    def _apply_state(self, state: str, word: str, stay: bool = False) -> None:
        if state == "thinking" and self._state != "thinking":
            self._since = time.time()
        self._state = state
        self._word = word or NOTCH_WORDS.get(state, "")
        if state != "recording":
            self._hot = None
        if self._rest_job is not None:
            self.root.after_cancel(self._rest_job)
            self._rest_job = None
        if state in ("done", "failed") and not stay:
            # An outcome shows for a while, then the chip goes back to the dot.
            # The dot itself never goes: it is how you know Sibyl is running.
            ms = NOTCH_DONE_MS if state == "done" else NOTCH_FAILED_MS
            self._rest_job = self.root.after(ms, self._rest)
        if state == "recording":
            self._close_panel()

    def _rest(self) -> None:
        self._rest_job = None
        self._apply_state("idle", "")

    def _label(self) -> list:
        """What the chip says, as (words, colour). Working shows its seconds in
        the dimmer colour: the word is the news, the count is reassurance."""
        if self._state == "thinking":
            seconds = int(time.time() - self._since)
            return [(f"{self._word} ", NOTCH_FG), (f"{seconds}s", NOTCH_DIM)]
        return [(self._word, NOTCH_FG)] if self._word else []

    def _layout(self, runs: list) -> dict:
        """Where everything goes, in screen pixels, at the chip's full width.
        The disc sits in the middle of the round left end; the cross in the
        middle of the round right end, with the tick just before it."""
        s = self._scale
        h = round(NOTCH_HEIGHT * s)
        lay = {"h": h, "text_x": round(29 * s)}
        if not runs:
            lay["w"] = h   # at rest: a circle
            return lay
        end = lay["text_x"] + sum(self._font.getlength(w) for w, _ in runs) / NOTCH_SS
        if self._state == "recording":
            r = round(11 * s)
            lay["r"] = r
            lay["send"] = round(end + 8 * s + r)
            # 12 px between the two, not 4: a click a little off went to the
            # wrong one (batch E3, all seven reviewers).
            lay["cancel"] = round(lay["send"] + 34 * s)
            lay["w"] = round(lay["cancel"] + h / 2)
        else:
            lay["w"] = round(end + 14 * s)
        return lay

    def _draw(self) -> bool:
        """Redraw the notch if what it should show has changed. Returns whether
        it did. A change of width grows or shrinks the chip, never jumps."""
        runs = self._label()
        text = "".join(w for w, _ in runs)
        key = (self._state, text, self._hot, self._down)
        if key == self._drawn:
            return False
        self._drawn = key
        self._runs, self._text = runs, text
        self._lay = self._layout(runs)
        target = self._lay["w"]
        if self._shown_w is None or self._shown_w == target:
            self._grow = None
            self._frame(target)
        else:
            self._grow = (self._shown_w, target, time.time())
            if self._grow_job is None:
                self._growing()
        return True

    def _growing(self) -> None:
        """One step of the chip growing or shrinking to its new width: quick at
        first, then settling, over NOTCH_GROW_MS."""
        self._grow_job = None
        if self.root is None or self._grow is None:
            return
        start_w, end_w, started = self._grow
        t = min(1.0, (time.time() - started) * 1000 / NOTCH_GROW_MS)
        eased = 1 - (1 - t) ** 3
        self._frame(round(start_w + (end_w - start_w) * eased))
        if t < 1:
            self._grow_job = self.root.after(15, self._growing)
        else:
            self._grow = None

    def _anchor(self, area: tuple, width: int) -> tuple:
        """The point a chip of `width` is centred on, in screen pixels: in the
        middle just above the taskbar, unless it has been dragged somewhere.
        Dragged, the stored point is where the microphone was dropped, and the
        chip grows to its right, so the microphone stays put (decided
        2026-10-05; it grew both ways before). Read on every frame, so a chip
        growing in a corner grows from there."""
        left, top, right, bottom = area
        h = self._lay["h"]
        if self._spot is None:
            return ((left + right) / 2,
                    bottom - round(NOTCH_MARGIN_BOTTOM * self._scale) - h / 2)
        fx, fy = self._spot
        return left + fx * (right - left) + (width - h) / 2, top + fy * (bottom - top)

    def _frame(self, width: int) -> None:
        """Draw the chip at `width` and put it on screen where it rests, kept
        inside the screen."""
        h = self._lay["h"]
        area = self._usable_area(self.root)
        x, y = _keep_inside(self._anchor(area, width), (width, h), area)
        self._at = (x, y)
        self._image = self._render(width)
        self._shown_w = width
        clickable = self._state in ("idle", "done", "failed")
        self._canvas.config(width=width, height=h,
                            cursor="hand2" if clickable else "")
        self.root.geometry(f"{width}x{h}+{x}+{y}")
        self._hit_areas(width)
        if self._hwnd:
            _push_layered(self._hwnd, self._image, x, y)

    def _hit_areas(self, width: int) -> None:
        """The invisible canvas shapes Tk uses to tell which part was clicked.
        Rebuilt only when the layout changes: rebuilding one under the pointer
        makes Tk report the pointer leaving and entering it, which would redraw
        the hover, which would rebuild it again."""
        lay, h = self._lay, self._lay["h"]
        key = (width, self._state, tuple(sorted(lay.items())))
        if key == self._hit_key:
            return
        self._hit_key = key
        c = self._canvas
        c.delete("all")
        c.create_rectangle(0, 0, width, h, fill="black", outline="", tags="body")
        r = NOTCH_DISC * self._scale / 2
        c.create_oval(h / 2 - r, h / 2 - r, h / 2 + r, h / 2 + r, fill="black",
                      outline="", tags=("body", "dot"))
        if self._state == "recording":
            # What answers a click is wider than what is drawn: the chip's full
            # height, and out to halfway between the tick and the cross, so the
            # two meet and never overlap (batch E3).
            half = (lay["cancel"] - lay["send"]) / 2
            for key_ in ("send", "cancel"):
                cx = lay[key_]
                c.create_rectangle(cx - half, 0, cx + half, h,
                                   fill="black", outline="", tags=key_)

    def _render(self, width: int):
        """The chip at `width`, drawn NOTCH_SS times too large and shrunk.
        What is inside is laid out for the full width and cut to the chip's
        shape, so while it grows the word is uncovered rather than squeezed."""
        from PIL import Image, ImageChops, ImageDraw

        s, k, lay = self._scale, NOTCH_SS, self._lay
        h = lay["h"]
        big_w, big_h = width * k, h * k
        one = max(1, round(s)) * k
        base, mask = _plate((big_w, big_h),
                            big_h / 2,
                            NOTCH_BG_DOWN if self._down == "body" else NOTCH_BG,
                            one)
        top = Image.new("RGBA", (big_w, big_h), (0, 0, 0, 0))
        draw = ImageDraw.Draw(top)
        mid = big_h / 2
        r = NOTCH_DISC * s * k / 2
        colour = STATE_COLOURS.get(self._state, STATE_COLOURS["idle"])
        # A hairline of light round the disc, for definition. Not a glow.
        draw.ellipse((mid - r, mid - r, mid + r, mid + r), fill=colour + (255,),
                     outline=_mix(colour, (255, 255, 255), 0.18) + (255,),
                     width=one)
        _microphone(draw, mid, mid, s * k, _mic_colour(colour) + (255,))
        x = lay["text_x"] * k
        for words, fill in self._runs:
            draw.text((x, mid), words, font=self._font, fill=fill + (255,),
                      anchor="lm")
            x += self._font.getlength(words)
        if self._state == "recording":
            kr, stroke = lay["r"] * k, max(1.5 * s, 1) * k
            for key in ("send", "cancel"):
                cx = lay[key] * k
                level = 2 if self._down == key else 1 if self._hot == key else 0
                draw.ellipse((cx - kr, mid - kr, cx + kr, mid + kr),
                             fill=NOTCH_BUTTON_FILL[level])
                if key == "send":
                    _stroke(draw, [(cx - 0.42 * kr, mid + 0.02 * kr),
                                   (cx - 0.12 * kr, mid + 0.32 * kr),
                                   (cx + 0.45 * kr, mid - 0.3 * kr)],
                            NOTCH_FG + (255,), stroke)
                else:
                    for dy in (-1, 1):
                        _stroke(draw, [(cx - 0.3 * kr, mid - dy * 0.3 * kr),
                                       (cx + 0.3 * kr, mid + dy * 0.3 * kr)],
                                NOTCH_DIM + (255,), stroke)
        top.putalpha(ImageChops.multiply(top.getchannel("A"), mask))
        return Image.alpha_composite(base, top).resize((width, h), Image.LANCZOS)

    def _show(self) -> None:
        """On screen, on top of the other always-on-top windows, focus refused.
        Re-done on every change of state, as the old pill did."""
        if self.root.state() == "withdrawn":
            self.root.deiconify()
            self.root.update_idletasks()
        self._refuse_focus()
        _on_top(self._hwnd)
        if self._shown_w is not None:
            self._frame(self._shown_w)   # a hide and show can cost it its picture

    def _grab(self, event) -> None:
        """A press on the disc or the chip. Nothing happens yet: only the
        release can tell a click from a drag. Measured from where the disc is
        drawn, which near an edge is not where it rests: a wide chip squeezed
        against the right edge otherwise sat still for the first 74 px of a
        drag (review, 2026-10-05)."""
        h = self._lay["h"]
        self._grip = (event.x_root, event.y_root,
                      (self._at[0] + h / 2, self._at[1] + h / 2), False)

    def _drag(self, event) -> None:
        """The pointer moved with the button down. Past Windows' drag threshold
        the notch follows it, in any state, with the disc kept on the screen.
        Moved with the same two calls every frame uses, which never take
        focus; Windows' own window-dragging can, and stalls Tk's timers."""
        if self._grip is None:
            return
        x0, y0, (ax, ay), dragging = self._grip
        dx, dy = event.x_root - x0, event.y_root - y0
        if not dragging:
            if not _past_drag_limit(dx, dy, self._drag_limit):
                return
            self._grip = (x0, y0, (ax, ay), True)
            self._close_panel()
        area = self._usable_area(self.root)
        left, top, right, bottom = area
        h, w = self._lay["h"], self._shown_w
        # The whole chip is kept on screen, as it is drawn, and the spot is
        # where its disc then is. Kept as a disc, the spot ran on past the
        # edge with the pointer, and the microphone later reappeared there.
        x, y = _keep_inside((ax + dx + (w - h) / 2, ay + dy), (w, h), area)
        self._spot = ((x + h / 2 - left) / (right - left),
                      (y + h / 2 - top) / (bottom - top))
        self._frame(w)

    def _let_go(self, _event=None) -> None:
        """The release decides. A drag is remembered where it was dropped; a
        press that never moved is a click, exactly as a click always was."""
        grip, self._grip = self._grip, None
        if grip is None:
            return
        if grip[3]:
            save_spot(NOTCH_SPOT_FILE, self._spot)
        else:
            self._clicked()

    def _clicked(self, _event=None) -> None:
        """A left-click on the disc or the chip. It starts listening only when
        Sibyl is at rest; while listening only the tick and the cross answer,
        and while working a click does nothing."""
        if self._state in ("idle", "done", "failed"):
            self._press("body")
            self._hand_on(self.on_click)

    def _key(self, key: str, callback) -> None:
        """The tick or the cross, clicked."""
        self._press(key)
        self._hand_on(callback)

    def _hover(self, key) -> None:
        if key != self._hot:
            self._hot = key
            self._draw()

    def _press(self, what: str) -> None:
        """Show a press for a moment: a click that changes nothing on screen
        feels like a click that missed."""
        self._down = what
        self._draw()
        self._later(140, self._release)

    def _release(self) -> None:
        self._down = None
        self._draw()

    def _hand_on(self, callback) -> None:
        """Call one of Sibyl's methods. An error is reported, never raised
        into Tk."""
        if callback is None:
            return
        try:
            callback()
        except Exception as exc:
            print(f"[pill] click failed: {type(exc).__name__}: " + private(str(exc), "(details not logged)"))

    # -- the right-click panel ------------------------------------------------

    def _toggle_panel(self, _event=None) -> None:
        """Right-click: the last cleaned-up text, Paste again, and Quit Sibyl.
        Right-click again to close it. Not while listening: that dictation is not text yet.
        """
        if self._panel is not None:
            self._close_panel()
            return
        if self._state == "recording":
            return
        import tkinter as tk

        self._panel_text = self.last_text() or "Nothing yet. Dictate something first."
        self._panel_hot = self._panel_down = None
        self._panel_hit_key = None
        panel = tk.Toplevel(self.root)
        panel.overrideredirect(True)
        panel.attributes("-topmost", True)
        canvas = tk.Canvas(panel, bg="black", highlightthickness=0, borderwidth=0)
        canvas.pack()
        canvas.tag_bind("paste", "<ButtonPress-1>", self._paste_again)
        canvas.tag_bind("quit", "<ButtonPress-1>", self._quit)
        canvas.tag_bind("home", "<ButtonPress-1>", self._home)
        for name in ("paste", "quit", "home"):
            canvas.tag_bind(name, "<Enter>", lambda _e, n=name: self._panel_hover(n))
            canvas.tag_bind(name, "<Leave>", lambda _e: self._panel_hover(None))
        canvas.bind("<ButtonPress-3>", self._toggle_panel)
        self._panel, self._panel_canvas = panel, canvas
        self._panel_hwnd = None
        self._panel_frame()
        # The same order as the notch itself: shown once, the style set,
        # hidden, shown again. It must never take focus either: Paste again
        # pastes into whatever window is in front, which is only right if that
        # is still yours.
        panel.update_idletasks()
        self._panel_hwnd = self._refuse_focus(panel)
        panel.withdraw()
        panel.deiconify()
        panel.update_idletasks()
        self._panel_frame()

    def _panel_frame(self) -> None:
        """Draw the panel and put it on screen, centred on the notch: above
        it, or below when the notch has been dragged too near the top."""
        image, boxes = self._render_panel()
        w, h = image.size
        x, y = _panel_spot((*self._at, self._shown_w, self._lay["h"]), (w, h),
                           round(8 * self._scale), self._usable_area(self.root))
        self._panel_canvas.config(width=w, height=h)
        self._panel.geometry(f"{w}x{h}+{x}+{y}")
        key = (w, h, tuple(sorted(boxes.items())))
        if key != self._panel_hit_key:
            # Rebuilt only when it moves, for the reason in _hit_areas.
            self._panel_hit_key = key
            c = self._panel_canvas
            c.delete("all")
            c.create_rectangle(0, 0, w, h, fill="black", outline="", tags="sheet")
            for name, (bx, by, bw, bh) in boxes.items():
                c.create_rectangle(bx, by, bx + bw, by + bh, fill="black",
                                   outline="", tags=name)
        self._panel_image = image
        if self._panel_hwnd:
            _push_layered(self._panel_hwnd, image, x, y)

    def _render_panel(self):
        """A small label, the last text, Paste again, and a quieter Quit Sibyl
        on the left, on the notch's surface with 12-pixel corners; once the
        notch has been dragged, Move back to the bottom in a row below them.
        Returns the picture and each button's box (x, y, width, height) in
        screen pixels, by name."""
        from PIL import Image, ImageChops, ImageDraw

        s, k = self._scale, NOTCH_SS

        def px(v):
            return round(v * s)

        w, pad = px(PANEL_WIDTH), px(14)
        one = max(1, round(s)) * k
        lines = _wrap(self._panel_text, self._text_font, (w - 2 * pad) * k)
        if len(lines) > PANEL_MAX_LINES:
            lines = lines[:PANEL_MAX_LINES]
            lines[-1] = lines[-1].rstrip(".,;: ") + "..."
        line_h = round(self._text_font.size / k * 1.45)
        label_h = round(self._small_font.size / k * 1.2)
        text_y = pad + label_h + px(8)
        bh = px(28)
        by = text_y + len(lines) * line_h + px(12)

        def width_of(label):
            return round(self._font.getlength(label) / k) + 2 * px(14)

        # Two pill buttons of the same design, decided 2026-09-19: Paste
        # again on the right, Quit Sibyl on the left.
        labels = {"paste": "Paste again", "quit": "Quit Sibyl",
                  "home": "Move back to the bottom"}
        bw = width_of(labels["paste"])
        boxes = {"paste": (w - pad - bw, by, bw, bh),
                 "quit": (pad, by, width_of(labels["quit"]), bh)}
        if self._spot is not None:
            boxes["home"] = (pad, by + bh + px(8), width_of(labels["home"]), bh)
        h = max(y + bh for _, y, _, _ in boxes.values()) + pad

        base, mask = _plate((w * k, h * k), px(12) * k, NOTCH_BG, one)
        top = Image.new("RGBA", (w * k, h * k), (0, 0, 0, 0))
        draw = ImageDraw.Draw(top)
        x = pad * k
        # Fog, not Slate: Slate on Carbon is 3.8 to 1, under the 4.5 small
        # text needs; Fog is 9.5 to 1 (batch E4).
        for letter in "LAST DICTATION":   # letter-spaced by hand; Pillow cannot
            draw.text((x, pad * k), letter, font=self._small_font,
                      fill=NOTCH_DIM + (255,), anchor="la")
            x += self._small_font.getlength(letter) + self._small_font.size * 0.12
        for i, line in enumerate(lines):
            draw.text((pad * k, (text_y + i * line_h) * k), line,
                      font=self._text_font, fill=NOTCH_FG + (255,), anchor="la")
        for name, (x0, y0, bw_, _) in boxes.items():
            level = (2 if self._panel_down == name
                     else 1 if self._panel_hot == name else 0)
            draw.rounded_rectangle((x0 * k, y0 * k, (x0 + bw_) * k - 1, (y0 + bh) * k - 1),
                                   radius=bh * k / 2, fill=NOTCH_BUTTON_FILL[level],
                                   outline=(255, 255, 255, 26), width=one)
            draw.text(((x0 + bw_ / 2) * k, (y0 + bh / 2) * k), labels[name],
                      font=self._font, fill=NOTCH_FG + (255,), anchor="mm")
        top.putalpha(ImageChops.multiply(top.getchannel("A"), mask))
        image = Image.alpha_composite(base, top).resize((w, h), Image.LANCZOS)
        return image, boxes

    def _panel_hover(self, name) -> None:
        if self._panel is not None and name != self._panel_hot:
            self._panel_hot = name
            self._panel_frame()

    def _paste_again(self, _event=None) -> None:
        """Show the press, then close the panel and paste."""
        self._panel_down = "paste"
        self._panel_frame()
        self._later(90, self._paste_now)

    def _quit(self, _event=None) -> None:
        """Show the press, then close the panel and quit Sibyl."""
        self._panel_down = "quit"
        self._panel_frame()
        self._later(90, self._quit_now)

    def _home(self, _event=None) -> None:
        """Show the press, then close the panel and move the notch back."""
        self._panel_down = "home"
        self._panel_frame()
        self._later(90, self._home_now)

    def _home_now(self) -> None:
        self._close_panel()
        self._spot = None
        save_spot(NOTCH_SPOT_FILE, None)
        self._frame(self._shown_w)

    def _quit_now(self) -> None:
        self._close_panel()
        self._hand_on(self.on_quit)

    def _paste_now(self) -> None:
        self._close_panel()
        self._hand_on(self.on_paste_again)

    def _close_panel(self) -> None:
        panel, self._panel = self._panel, None
        self._panel_canvas = self._panel_hwnd = None
        if panel is not None:
            try:
                panel.destroy()
            except Exception:
                pass

    def loop(self) -> None:
        """Hand the main thread to Tk. Blocks until the process ends."""
        if self.root is not None:
            self.root.mainloop()

    def close(self) -> None:
        """Cancel the pending callbacks, then destroy the window.

        The running app never calls this: the notch lives exactly as long as the
        process does. The tests do, and without it Tk complains about an
        `invalid command name` because _drain reschedules itself every 60ms and
        one of those was still queued when the window went away. A repeating
        callback with no way to stop it is a loose end whether or not anything
        currently trips over it.
        """
        root, self.root = self.root, None   # stops _drain rescheduling itself
        if root is None:
            return
        try:
            for job in (self._drain_job, self._rest_job, self._grow_job, *self._jobs):
                if job is not None:
                    root.after_cancel(job)
            self._drain_job = self._rest_job = self._grow_job = None
            self._jobs.clear()
            self._panel = self._panel_canvas = None
            root.destroy()   # the panel, if open, goes with it
        except Exception:
            pass


class Sibyl:
    def __init__(self) -> None:
        self.recorder = Recorder()
        self.tray = None
        # The tray shows the coloured dot only when nothing else shows the
        # state: until the notch is built, and for good if it cannot be (and on
        # macOS). Otherwise it is the logo, decided 2026-09-19.
        self._tray_shows_state = True
        self.pill = Pill()
        self._busy = False
        self._hands_free = False   # a tap started this recording; a tap ends it
        self._target_hwnd = None   # window that was in front when recording began
        self._wav = None           # the recording being worked on, for quit()
        # The last thing Sibyl produced, kept so it can be pasted again
        # somewhere else. In memory only: it is derived from a recording of a
        # real voice, and it dies with the process for the same reason the WAV
        # dies with the capture.
        self._last_output = ""
        # While the microphone opens on its worker, key releases are noted
        # here ("tap", "hold", "abort") and applied in order once it listens.
        self._starting = False
        self._pending = []
        # Re-entrant because the worker replays those releases through
        # on_tap and friends while still holding it, so no new key can slip
        # in between them.
        self._lock = threading.RLock()
        # The notch's clicks go to the same methods the keys use.
        self.pill.on_click = self.on_notch_click
        self.pill.on_send = self.on_hold_release    # the tick sends, as letting go does
        self.pill.on_cancel = self.on_notch_cancel
        self.pill.on_paste_again = self.recall
        self.pill.on_quit = self.quit
        self.pill.last_text = lambda: self._last_output

    # -- state display ------------------------------------------------------

    def _set_state(self, state: str, note: str = "", short: str = "",
                   stay: bool = False) -> None:
        """`note` is the whole story, for the console and the tray. `short` is
        the few words the notch has room for: a failure says what failed."""
        base = {
            "idle": "Sibyl - ready",
            "recording": "Sibyl - listening",
            "thinking": "Sibyl - thinking...",
            "loading": "Sibyl - waking model...",
            "done": "Sibyl - done",
            "failed": "Sibyl - problem",
        }.get(state, "Sibyl")
        # While the recogniser is dead every capture fails the same way, so no
        # failure may fade back to the dot that means ready: that fade is what
        # made a broken Sibyl look healthy. Decided here, from the situation,
        # because a caller on a failing path cannot be relied on to remember —
        # the `finally` in _finish_capture's own except block already forgot.
        if state == "failed" and stt_is_dead():
            stay = True
        label = f"{base}  |  {note}" if note else base
        print(f"[{state}] {note or base}")
        # Three places, one call: the console for the record, the tray for
        # anyone who goes looking, and the notch for the case this exists to
        # solve, which is not looking at all.
        self.pill.set_state(state, short, stay)
        if self.tray is not None:
            if self._tray_shows_state:
                self.tray.icon = _make_icon_image(state)
            self.tray.title = label[:127]  # Windows tooltip limit

    # -- the hotkey path ----------------------------------------------------

    # Hold to talk, tap for hands-free, since 2026-09-11. TapListener reports
    # four moments and these four methods decide what each one means. Every
    # one must return immediately: blocking inside a pynput callback freezes
    # the listener and silently drops every later keypress, so anything slow
    # goes to a worker thread.

    def on_chord_down(self, *_) -> None:
        """Both keys are down. Start recording now, before we know whether
        this is a hold or a tap, so no words are lost to the deciding."""
        with self._lock:
            if self._busy:
                beep("error")
                print("[busy] still processing the last capture; press ignored")
                # A beep alone is indistinguishable from dead hardware, and a
                # model that has hung holds this for up to OLLAMA_TIMEOUT_WARM
                # (U30). Re-stating "thinking" keeps its seconds counter, which
                # _apply_state anchors only on entering the state.
                #
                # Deliberately says nothing about WHICH operation: _busy is one
                # flag for six, and three of them are not captures at all
                # (on_abort, on_notch_cancel, recall). Naming a capture here
                # would be a plausible, specific and sometimes false claim. No
                # stage word is lost by relabelling: the "thinking" calls pass
                # no short, so the chip reads "Working" while "transcribing" and
                # "cleaning up" go to the console and tray. The one exception is
                # "Time limit, sending" (E5), which this replaces.
                self._set_state("thinking", "still busy with the last request; "
                                            "the press was ignored",
                                "Still busy")
                return
            if stt_is_dead():
                # Recording would only waste your breath: there is nothing to
                # transcribe with, it will fail the same way every time until
                # Sibyl restarts, and there are no words to fall back to (U14).
                beep("error")
                self._set_state("failed", "the speech model did not load, so "
                                          "there is nothing to transcribe with. "
                                          "Check with: python sibyl.py "
                                          "--test-stt <a .wav file>",
                                "Speech model failed")
                return
            if self.recorder.is_recording or self._starting:
                return  # hands-free, or still opening: the release decides

            # Captured now, not at paste time: by then you may have clicked
            # into another window while waiting.
            self._target_hwnd = capture_target_window()
            self._hands_free = False
            self._starting = True
        # Opening the microphone took 187 to 429 ms, measured, and this is the
        # thread Windows times: a keyboard hook that answers too slowly is
        # silently removed. So the opening goes to a worker (batch B). If the
        # worker cannot even start, the error must not escape this callback,
        # and "starting" must not stay set to swallow every later press.
        try:
            threading.Thread(target=self._open_microphone, daemon=True).start()
        except Exception as exc:
            with self._lock:
                self._starting = False
                self._pending.clear()
                beep("error")
                self._set_state("failed", f"could not start recording: "
                                          f"{type(exc).__name__}: "
                                          + private(str(exc), "(details not logged)"),
                                "Microphone failed")

    def _open_microphone(self) -> None:
        """The slow half of a press. Once listening, applies whatever the keys
        did meanwhile, in the order they did it."""
        try:
            # Here, not in the callback: reading a title asks the window
            # itself, and a window that has hung answers slowly.
            print(f"[target] will paste back into: "
                  + private(window_title(self._target_hwnd), "the window in front"))
            self.recorder.start(on_timeout=self._on_timeout,
                                on_warning=lambda: beep("warning"))
        except Exception as exc:
            with self._lock:
                self._starting = False
                self._pending.clear()   # releases of a press that never recorded
                beep("error")
                self._set_state("failed", f"could not start recording: "
                                          f"{type(exc).__name__}: "
                                          + private(str(exc), "(details not logged)"),
                                "Microphone failed")
            return
        beep("start")
        with self._lock:
            self._starting = False
            self._set_state("recording")
            pending, self._pending = self._pending, []
            for what in pending:
                {"tap": self.on_tap, "hold": self.on_hold_release,
                 "abort": self.on_abort}[what]()

    def on_tap(self, *_) -> None:
        """A quick press and release. The first one after a start means
        hands-free: keep listening. The next one sends."""
        with self._lock:
            if self._starting:
                self._pending.append("tap")
                return
            if not self.recorder.is_recording or self._busy:
                return
            if not self._hands_free:
                self._hands_free = True
                self._set_state("recording", "hands-free, tap again to send")
                return
            self._busy = True
        threading.Thread(target=self._finish_capture, daemon=True).start()

    def on_hold_release(self, *_) -> None:
        """Let go after a hold: send. Also ends a hands-free recording, since
        a long press is what someone who forgot they were hands-free will do.
        The notch's tick sends through here too."""
        with self._lock:
            if self._starting:
                self._pending.append("hold")
                return
            if not self.recorder.is_recording or self._busy:
                return
            self._busy = True
        threading.Thread(target=self._finish_capture, daemon=True).start()

    def on_abort(self, *_) -> None:
        """Another key joined the chord: Ctrl+Alt+something, never meant for
        Sibyl. Throw the recording away. Not during hands-free, though: by
        then you are dictating, and touching the keyboard is normal."""
        with self._lock:
            if self._starting:
                self._pending.append("abort")
                return
            if not self.recorder.is_recording or self._busy or self._hands_free:
                return
            self._busy = True
        threading.Thread(target=self._cancel_capture, daemon=True).start()

    # -- the notch's clicks -------------------------------------------------
    # Tk calls these on its own thread, and like the keyboard callbacks they
    # return at once. A click never moves focus (the notch refuses it), so the
    # window in front is still the one you were typing in. The tick is
    # on_hold_release, above.

    def on_notch_click(self, *_) -> None:
        """A click on the dot starts listening, hands-free: a click is over the
        moment it happens, so there is no hold to wait for. The tick, the cross
        or a tap of the keys ends it. Hands-free is set directly, not by a
        tap: a double-click while the microphone opens would be two taps,
        and the second would send."""
        self.on_chord_down()
        with self._lock:
            if (self.recorder.is_recording or self._starting) and not self._busy:
                self._hands_free = True

    def on_notch_cancel(self, *_) -> None:
        """The cross: throw the recording away. Unlike on_abort, hands-free
        included, because a click on the cross is always meant."""
        with self._lock:
            if not self.recorder.is_recording or self._busy:
                return
            self._busy = True
        threading.Thread(target=self._cancel_capture, daemon=True).start()

    def _cancel_capture(self) -> None:
        try:
            wav = self._wav = self.recorder.stop()
            # A recording of a real voice, never sent: it goes at once.
            if wav is not None:
                wav.unlink(missing_ok=True)
                self._wav = None
            print("[cancelled] nothing was sent")
            self._set_state("idle", "cancelled")
        except Exception as exc:
            # A delete that failed leaves your voice on disk: say where, as
            # _finish_capture does, since the next capture forgets the path.
            left = (f", recording not deleted, remove it by hand: "
                    f"{private_path(self._wav)}" if self._wav is not None else "")
            self._set_state("failed", f"cancel failed: {type(exc).__name__}: "
                            + private(str(exc), "(details not logged)") + left,
                            "Cancel failed")
        finally:
            with self._lock:
                self._busy = False

    def _on_timeout(self) -> None:
        print(f"[timeout] hit the {MAX_RECORD_SECONDS}s cap, sending anyway")
        with self._lock:
            if not self.recorder.is_recording or self._busy:
                return
            self._busy = True
        threading.Thread(target=self._finish_capture,
                         args=("Time limit, sending",), daemon=True).start()

    # -- recall -------------------------------------------------------------
    # Phase 3 job 3, done as the cheap reframe rather than the roadmap's focus
    # detector. Sibyl cannot tell whether a paste landed: Windows will not
    # reliably say whether the focused thing accepts text, and is blindest
    # inside browsers and Electron apps, which is where the work happens. So
    # this does not try to find out. It makes the answer stop mattering.

    def recall(self, *_) -> None:
        """Paste the last output again, into whatever is in front NOW.

        Returns immediately, like on_tap and for the same reason: blocking
        inside a pynput callback freezes the listener and silently drops every
        later keypress.
        """
        with self._lock:
            if self._busy or self._starting or self.recorder.is_recording:
                # A capture owns the clipboard and the paste until it finishes.
                # Two pastes racing is exactly the class of bug this feature
                # exists to clean up after.
                beep("error")
                print("[busy] capture in progress; recall ignored")
                return
            if not self._last_output:
                beep("error")
                print("[recall] nothing captured yet this session")
                self._set_state("failed", "nothing to recall yet",
                                "Nothing to paste yet")
                return
            self._busy = True

        threading.Thread(target=self._do_recall, daemon=True).start()

    def _do_recall(self) -> None:
        text = self._last_output
        try:
            # Named before the paste rather than after, so the message reports
            # where the text was aimed even if focus moves as it lands.
            here = private(window_title(capture_target_window()), "the window in front")
            # No target window, deliberately. Everywhere else in Sibyl the
            # paste is restored to the window you started speaking in, which is
            # the most important fix in the project. Here that would defeat the
            # point: you have MOVED, on purpose, to somewhere the text can
            # actually go. inject treats a target of None as "already in the
            # right place", so it releases the modifiers and pastes.
            inject(text, None)
            self._set_state("done", f"re-sent {len(text)} chars to {here}")
        except Exception as exc:
            beep("error")
            self._set_state("failed", f"recall failed: {type(exc).__name__}: "
                            + private(str(exc), "(details not logged)"),
                            "Clipboard busy" if isinstance(exc, ClipboardBusy)
                            else "Paste failed")
        finally:
            # The output is NOT consumed. Recall into the wrong window is a
            # thing you fix by recalling again somewhere else.
            with self._lock:
                self._busy = False

    def recopy(self, *_) -> None:
        """The tray's version of recall: clipboard only, no paste.

        It cannot paste for you, and that is a fact about Windows rather than a
        shortcut taken here. Clicking a tray menu moves the foreground to the
        shell, so by the time this runs, the window you wanted the text in is
        no longer the one that would receive it. Pasting anyway would fire your
        text into whatever the click left in front, which is the bug the focus
        work fixed. So it hands the text to the clipboard and lets you press
        Ctrl+V where you can see the cursor. The hotkey is the one-step path.
        """
        with self._lock:
            if self._busy or self._starting or self.recorder.is_recording:
                print("[busy] capture in progress; recall ignored")
                return
            text = self._last_output
        if not text:
            self._set_state("failed", "nothing to recall yet", "Nothing to copy yet")
            return
        try:
            copy_only(text)
            self._set_state("done", f"{len(text)} chars back on the clipboard, "
                                    f"press Ctrl+V where you want them", "Copied")
        except Exception as exc:
            beep("error")
            self._set_state("failed", f"recall failed: {type(exc).__name__}: "
                            + private(str(exc), "(details not logged)"),
                            "Clipboard busy" if isinstance(exc, ClipboardBusy)
                            else "Copy failed")

    def _finish_capture(self, word: str = "") -> None:
        """`word` is what the notch says while working, if not "Working"."""
        wav = None
        fallback = None   # your words, rule-cleaned, once there is a transcript
        started = time.time()
        # What the notch says if the step under way breaks, moved on as each
        # one starts. An unexpected error used to show "Something went
        # wrong", the one failure that never said what failed (batch E2).
        failure = "Recording failed"
        try:
            wav = self._wav = self.recorder.stop()   # quit() deletes it too
            beep("stop")
            print(f"[recorded] {self.recorder.last_duration:.1f}s of audio "
                  f"(cap is {MAX_RECORD_SECONDS}s)")

            if wav is None:
                self._set_state("failed", "nothing recorded", "Nothing recorded")
                beep("error")
                return

            # -- STT ---------------------------------------------------------
            self._set_state("thinking", "transcribing", word)
            failure = "Transcribing failed"
            t0 = time.time()
            raw = transcribe(wav)
            stt_time = time.time() - t0

            if not raw:
                self._set_state("failed", "no speech detected", "No speech heard")
                beep("error")
                return
            failure = "Clean-up failed"
            # Deterministic spelling fixes before the model sees the text.
            raw = fix_technical_terms(raw)
            # Kept before the model is asked, so that whatever fails after this
            # point, recall holds THIS dictation's words, never the last one's.
            fallback = rule_clean(raw)
            self._last_output = fallback
            # The FULL transcript, not a preview. Truncating it made it
            # impossible to tell what the clean-up invented from what was
            # actually said, which is the one comparison that matters when
            # judging whether the output is faithful.
            print(f"  heard ({stt_time:.1f}s), {len(raw)} chars:")
            print("    " + private(raw, "(words not logged)"))

            self._set_state("thinking", f"cleaning up ({CLEANUP_MODEL})", word)
            t0 = time.time()
            # The model's answer if the check trusts it; your own words if not,
            # or if the model failed. `why` is empty when the answer is used.
            # Its words no longer stream to the screen: the notch counts
            # seconds instead, because it is always on show, screen shares
            # included (roadmap step 5).
            output, why = clean_or_fall_back(raw)
            # The check names the words it objected to, in quotes; the log
            # keeps the reason and loses the words.
            why = private(why, re.sub(r"'[^']*'|\"[^\"]*\"", "'...'", why))
            print(f"  cleaned ({time.time() - t0:.1f}s)"
                  + (f", pasting your words. Why: {why}" if why else ""))

            # Stored BEFORE the paste is attempted, and kept whether or not it
            # succeeds. The case this exists for is the one where the paste
            # reports success and lands nowhere.
            self._last_output = output

            failure = "Paste failed"
            pasted = inject(output, self._target_hwnd)
            total = time.time() - started
            if pasted:
                # "sent", not "pasted", and the difference is not pedantry.
                # All we know is that focus was restored and Ctrl+V was
                # delivered. If the focused element cannot accept text (a
                # sidebar, a file list, a page with no cursor in a field) the
                # keystroke does nothing and we have no way to detect it.
                # Saying "pasted" once sent a user hunting for 517 characters
                # that were never anywhere. Naming the clipboard gives a way
                # back, which the old message did not.
                self._set_state(
                    "done",
                    f"sent {len(output)} chars{' of your words' if why else ''} to "
                    + private(window_title(self._target_hwnd), "the window in front")
                    + f" in {total:.1f}s"
                    + (f". Why: {why}" if why else
                       " (nothing there? right-click the dot to paste it again)"),
                    "Done, your words" if why else "",
                )
            else:
                # Refused to paste rather than risk the wrong window.
                beep("error")
                self._set_state(
                    "failed",
                    f"on clipboard ({len(output)} chars{' of your words' if why else ''})"
                    f" - original window gone, press Ctrl+V yourself",
                    "On clipboard, press Ctrl+V",
                )

        except Exception as exc:
            # A model failure never lands here: clean_or_fall_back pastes your
            # words instead. This is everything else, and the words still exist.
            beep("error")
            note = (f"failed: {type(exc).__name__}: "
                    + private(str(exc), "(details not logged)"))
            if fallback is not None:
                try:
                    copy_only(self._last_output)
                    note += " - your words are on the clipboard"
                except Exception:
                    pass
            # A dead recogniser is not a passing failure: it fails the same way
            # every time, and there are no words to fall back to, so the
            # dictation is lost. The notch must not fade back to the grey dot a
            # healthy Sibyl shows, which is what an ordinary failure does after
            # NOTCH_FAILED_MS.
            if stt_is_dead():
                failure = "Speech model failed"
            elif isinstance(exc, ClipboardBusy):
                failure = "Clipboard busy"
            self._set_state("failed", note, failure)
        finally:
            # The WAV is a recording of your voice. It has no business
            # outliving the request that needed it.
            if wav is not None:
                try:
                    wav.unlink(missing_ok=True)
                    self._wav = None
                except Exception as exc:
                    # Said out loud, as _cancel_capture does: your voice is
                    # still on disk, and only you can remove it now.
                    beep("error")
                    self._set_state("failed", f"recording not deleted, remove it "
                                              f"by hand: {private_path(wav)} "
                                              f"({type(exc).__name__})",
                                    "Recording not deleted")
            with self._lock:
                self._busy = False

    # -- lifecycle ----------------------------------------------------------

    def quit(self, *_) -> None:
        # os._exit skips every `finally`, including the one that deletes the
        # recording, so the recording is deleted here first. Quitting during a
        # capture used to leave your voice on disk (U33), and the notch's Quit
        # button made that two clicks away.
        wav = self._wav
        if wav is not None:
            try:
                wav.unlink(missing_ok=True)
            except Exception as exc:
                print(f"[quit] recording not deleted, remove it by hand: "
                      f"{private_path(wav)} ({type(exc).__name__})")
        if self.tray is not None:
            self.tray.stop()
        os._exit(0)

    def _boot(self) -> None:
        """Load everything into RAM before the first capture. Cold-loading per
        capture is the single biggest avoidable source of latency."""
        self._set_state("loading", f"loading STT ({STT_MODEL})")
        # Unlike a failed clean-up, there is no falling back to your own words:
        # without the recogniser there are no words to fall back to. So a dead
        # one is shown now rather than at the first capture.
        #
        # get_stt() itself raises when STT_BACKEND names a backend that does not
        # exist, before load() is ever reached, and load()'s own except cannot
        # catch that. _boot runs on a daemon thread, so an escape here would
        # kill boot in silence and leave the notch on "loading" for ever.
        download_failed = False
        try:
            stt = get_stt()
            stt.load(on_download=lambda: self._set_state(
                "loading", f"downloading the speech model ({STT_MODEL}), "
                           f"first start only", "Downloading speech model"))
            stt_error, download_failed = stt.load_error, stt.download_failed
        except Exception as exc:
            stt_error = f"{type(exc).__name__}: {exc}"
        # A failed download names itself: the fix is the connection, and the
        # next start fetches only what is still missing (batch E6).
        stt_problem = (("Speech model download failed" if download_failed
                        else "Speech model failed") if stt_error else None)
        # Ollama and Sibyl both start with Windows, and Sibyl can win the race.
        # So "not running" gets a minute to come true before it is shown.
        # With no recogniser nothing can reach the clean-up, so neither waiting
        # for Ollama nor warming it buys anything: both only delay news Sibyl
        # already has. The warm-up and its practice clean-up cost about 17s,
        # measured 2026-09-19, and the wait up to a minute (U13).
        deadline = time.time() + (0 if stt_problem else OLLAMA_WAIT_AT_START)
        problem = ollama_problem(CLEANUP_MODEL)
        while problem == "Ollama not running" and time.time() < deadline:
            self._set_state("loading", "waiting for Ollama to start",
                            "Waiting for Ollama")
            time.sleep(2)
            problem = ollama_problem(CLEANUP_MODEL)
        if not problem and not stt_problem:
            self._set_state("loading", f"waking {CLEANUP_MODEL}")
            warm_up(CLEANUP_MODEL)
            # Loading the model is not enough. Its first clean-up also reads
            # the whole prompt and its examples, about 14 s here, and later
            # ones reuse that reading: measured 2026-09-19, 17.7 s then 3.5 s.
            # One practice clean-up now moves those 14 s off your first
            # dictation. Its answer is thrown away.
            try:
                clean_transcript("okay")
            except OllamaError as exc:
                # Answering /api/tags is not the same as being able to write.
                # Without this, a model that cannot load showed a grey "ready".
                print(f"[loading] the practice clean-up failed: "
                      f"{type(exc).__name__}: " + private(str(exc), "(details not logged)"))
                problem = "Clean-up failed"
        # pyautogui is only needed at paste time and takes ~0.8s to import,
        # measured 2026-09-05, so without this the FIRST capture of a session
        # silently pays for it. Exactly the same reasoning as the models above.
        self._set_state("loading", "preparing the paste step")
        import pyautogui  # noqa: F401  (imported for its side effect: caching)
        if stt_problem and problem:
            # Only one can be shown, and the recogniser is the one that stops
            # captures entirely. The other is still recorded, so fixing the
            # first does not reveal a second surprise on the next launch.
            print(f"[loading] also: {problem}")
        if stt_problem:
            fix = ("check the internet connection and start Sibyl again; "
                   "only the missing files are fetched" if download_failed else
                   "Check with: python sibyl.py --test-stt <a .wav file>")
            self._set_state("failed", f"{stt_problem}: captures cannot work "
                                      f"until it loads. {fix}",
                            stt_problem, stay=True)
        elif problem:
            # Red until the next capture, which still works: with no model,
            # your words are pasted, tidied by the fixed rules.
            self._set_state("failed", f"{problem}: captures will paste your own "
                                      f"words. Check with: python sibyl.py "
                                      f"--test-ollama", problem, stay=True)
        else:
            self._set_state("idle", "ready")

    def run(self) -> None:
        import pystray

        try:
            codes = parse_binding(HOTKEY)
        except ValueError as exc:
            print(f"[FATAL] bad hotkey setting: {exc}")
            return

        listener = TapListener({codes: self.on_tap})
        listener.on_chord_down = self.on_chord_down
        listener.on_hold_release = self.on_hold_release
        listener.on_abort = self.on_abort
        listener.start()

        self.tray = pystray.Icon(
            "Sibyl",
            _make_icon_image("loading"),
            "Sibyl - starting",
            menu=pystray.Menu(
                pystray.MenuItem(f"Talk: {_pretty(HOTKEY)}", None, enabled=False),
                pystray.MenuItem(f"Model: {CLEANUP_MODEL}", None, enabled=False),
                # Clipboard only. See recopy: a tray click has already taken
                # the foreground, so this cannot paste for you.
                pystray.MenuItem("Copy last output again", self.recopy),
                pystray.MenuItem("Quit", self.quit),
            ),
        )

        # Built before anything is printed or booted, so the "waking up" state
        # has somewhere to appear. Building it is a main-thread job: a Tk window
        # may only be touched by the thread that created it, and one created on
        # a worker thread dies with that thread.
        pill_ready = False if IS_MAC else self.pill.build()
        logo = _brand_icon_image() if pill_ready else None
        if logo is not None:
            self._tray_shows_state = False
            self.tray.icon = logo

        print(f"Sibyl starting.")
        print(f"  Talk: {_pretty(HOTKEY)}")
        print(f"  Tap, speak, tap again. Quit from the tray or Ctrl+C here.\n")

        threading.Thread(target=self._boot, daemon=True).start()

        # Two event loops both want the main thread and only one can have it.
        #
        # pystray had it until 2026-08-27. tkinter has it now, because tkinter
        # genuinely cannot run anywhere else, while pystray on Windows can:
        # run_detached() exists for exactly this situation.
        #
        # macOS keeps the old arrangement. There, UI event loops really must be
        # on the main thread, so the tray keeps it and the pill is skipped
        # rather than half-working. Same fallback if the window could not be
        # built at all.
        if not pill_ready:
            self.tray.run()
            return

        self.tray.run_detached()
        self.pill.loop()


# ---------------------------------------------------------------------------
# 9. STAGED SELF-TESTS
# ---------------------------------------------------------------------------
# Each stage runs alone, so a failure names its own layer rather than just
# saying "it didn't work".


def test_audio(seconds: int = 5) -> None:
    import numpy as np
    import sounddevice as sd
    import soundfile as sf

    print(f"Recording {seconds}s from the default input. Say something.")
    beep("start")
    audio = sd.rec(
        int(seconds * SAMPLE_RATE), samplerate=SAMPLE_RATE,
        channels=CHANNELS, dtype="int16",
    )
    sd.wait()
    beep("stop")

    out = Path("test_recording.wav")
    sf.write(out, audio, SAMPLE_RATE, subtype="PCM_16")
    peak = int(np.abs(audio).max())
    print(f"  wrote {out.resolve()}  ({len(audio) / SAMPLE_RATE:.1f}s, peak {peak})")
    if peak < 500:
        print("  WARNING: peak is very low. Check the microphone is not muted.")


def test_hotkeys() -> None:
    """Report what pynput can actually capture on this keyboard.

    whisper-local uses global-hotkeys and pywin32 on Windows rather than
    pynput, which hints pynput has real limits here. Better to find out now
    than to ship a key that silently never fires.
    """
    from pynput import keyboard

    print("Checking the configured hotkey parses:")
    try:
        codes = parse_binding(HOTKEY)
        print(f"  ok    '{HOTKEY}' -> {describe_binding(codes)} {sorted(codes)}")
    except ValueError as exc:
        print(f"  FAIL  {exc}")
        return

    print("\nNow press the keys a few times. Ctrl+C to stop.")
    print("  TAP     the gesture matched and Sibyl WOULD fire")
    print("  ignored the gesture was seen but is not bound, and why")
    print("  key     every single key, with the code Windows actually sent")
    print("\nThe raw key lines matter: a key can arrive under a name you did")
    print("not expect, and matching on the code is what makes that harmless.\n")

    seen: dict = {}

    def on_tap(label):
        def fire():
            seen[label] = seen.get(label, 0) + 1
            print(f"  TAP     {label}  (x{seen[label]})")
        return fire

    bindings = {codes: on_tap(f"{HOTKEY} -> tap: hands-free (or send, if already listening)")}
    listener = TapListener(bindings)
    listener.on_chord_down = lambda g: print("  DOWN    both keys down: Sibyl WOULD start listening")
    listener.on_hold_release = lambda g: print("  HOLD    held and let go: Sibyl WOULD send")
    listener.on_abort = lambda g: print("  ABORT   another key joined in: Sibyl WOULD discard")
    listener.on_reject = lambda gesture, why: print(
        f"  ignored {describe_binding(gesture) or '(none)'}: {why}"
    )

    # Report EVERY key, bound or not. The old version only printed keys it
    # already recognised, so a key arriving under an unexpected name produced
    # no output at all, which is indistinguishable from the key being dead.
    original_press = listener._on_press

    def traced_press(key):
        code = key_code(key)
        mark = "*" if code in listener._watched else " "
        print(f"  key    {mark} {str(key):20} code={code}")
        original_press(key)

    listener._on_press = traced_press
    listener.start()

    try:
        while True:
            time.sleep(0.5)
    except KeyboardInterrupt:
        print(f"\n  captured: {seen or 'nothing - these keys may not be reachable'}")


def test_ollama() -> None:
    """Probe the daemon without generating anything, so 'not running' is
    distinguishable from 'slow' in one second rather than one minute."""
    import requests

    if OLLAMA_ADDRESS_PROBLEM:
        print(f"  FAIL  refused: {OLLAMA_ADDRESS_PROBLEM}")
        return
    print(f"Probing {OLLAMA_HOST} ...")
    started = time.time()
    try:
        response = requests.get(f"{OLLAMA_HOST}/api/tags", timeout=5,
                                proxies=NO_PROXIES)
    except requests.exceptions.ConnectionError:
        print(f"  FAIL  nothing listening at {OLLAMA_HOST}")
        print(f"        Start it with:  ollama serve")
        return
    except requests.exceptions.Timeout:
        print(f"  FAIL  {OLLAMA_HOST} accepted the connection but did not respond")
        return

    if response.status_code != 200:
        print(f"  FAIL  HTTP {response.status_code}: {response.text[:200]}")
        return

    installed = [m["name"] for m in response.json().get("models", [])]
    print(f"  ok    server is up, {len(installed)} model(s) installed, "
          f"answered in {time.time() - started:.2f}s")
    for name in installed:
        print(f"          {name}")

    print("\nRequired model:")
    model = CLEANUP_MODEL
    # Ollama reports "qwen2.5:1.5b"; a config of "qwen2.5" should still
    # match, so compare on the base name too.
    found = any(m == model or m.split(":")[0] == model.split(":")[0]
                for m in installed)
    if found:
        print(f"  ok    clean-up: {model}")
    else:
        print(f"  MISSING clean-up: {model}   ->  ollama pull {model}")


def test_stt(path: str) -> None:
    wav = Path(path)
    if not wav.exists():
        print(f"  FAIL  no such file: {wav}")
        return

    import soundfile as sf

    duration = sf.info(wav).duration
    print(f"Loading {STT_BACKEND} / {STT_MODEL} ({STT_DEVICE}, {STT_COMPUTE}) ...")
    t0 = time.time()
    backend = get_stt()
    backend.load()
    print(f"  model loaded in {time.time() - t0:.1f}s")

    print(f"Transcribing {wav.name} ({duration:.1f}s of audio) ...")
    t0 = time.time()
    text = fix_technical_terms(backend.transcribe(wav))
    elapsed = time.time() - t0

    print(f"\n{text}\n")
    # The number Phase 2 needs. Realtime factor above 1 means faster than
    # playback, which is the bar for transcribe-while-recording to work.
    print(f"  {elapsed:.2f}s for {duration:.1f}s of audio "
          f"= {duration / elapsed:.1f}x realtime")


def test_llm(text: str) -> None:
    print(f"Ollama at {OLLAMA_HOST}\n")

    try:
        print(f"--- clean-up ({CLEANUP_MODEL}) ---")
        t0 = time.time()
        cleaned = clean_transcript(text)
        t_clean = time.time() - t0
        print(f"{cleaned}\n  [{t_clean:.1f}s]\n")
    except OllamaError as exc:
        print(f"  FAIL  {exc}")
        print(f"\n--- would paste your words instead ---\n{rule_clean(text)}")
        return

    problems = faithful(text, cleaned)
    if not problems:
        print("--- the check: trusted, would paste the answer above ---")
        return
    print("--- the check: NOT trusted ---")
    for problem in problems:
        print(f"  - {problem}")
    print(f"\n--- would paste your words instead ---\n{rule_clean(text)}")


def test_pill() -> None:
    """Show the notch going through a whole capture, without speaking a word.

    The same states in the same order a real capture has, a failure at the
    end, then back to the resting dot. `test_pill.py` asserts all of this;
    this is the look of it. Needs no microphone, no Ollama and no models.
    """
    pill = Pill()
    if not pill.build():
        print("  FAIL  the notch could not be created. Sibyl will still run "
              "without it, but you will not see what it is doing.")
        return

    def drive() -> None:
        # Each state long enough to drag it by hand.
        time.sleep(0.8)
        pill.set_state("loading")
        time.sleep(5.0)
        pill.set_state("recording")
        time.sleep(5.0)
        pill.set_state("thinking")
        time.sleep(5.0)
        pill.set_state("done")
        time.sleep(NOTCH_DONE_MS / 1000 + 1.0)
        pill.set_state("failed", "Microphone failed")
        time.sleep(NOTCH_FAILED_MS / 1000 + 1.0)
        print("\n  ok    went through every state and back to the resting dot.")
        print("  Check by eye: did it ever steal focus from this terminal?")
        # os._exit skips Python's buffer flush, so without this the whole
        # report vanishes whenever output is piped rather than shown on a
        # terminal. It is used anyway because Tk's mainloop is on the main
        # thread and cannot be stopped cleanly from this one.
        sys.stdout.flush()
        os._exit(0)

    print("Watch the bottom of the screen. This takes about 30 seconds.")
    print("Drag the notch in each state, and keep typing in another window: "
          "it must never take your cursor.")
    print("Where you leave it is where Sibyl will start. Right-click it to move "
          "it back to the bottom.\n")
    threading.Thread(target=drive, daemon=True).start()
    pill.loop()   # Tk owns the main thread, exactly as it does in real use


def main() -> None:
    # No console: started from the desktop icon or at login, by pythonw.exe.
    if sys.stdout is None:
        try:
            open_log()
        except Exception as exc:
            # There is no console and now no log either, so the only way left
            # to say anything is a box. Silence would look like "still loading".
            if not IS_MAC:
                ctypes.windll.user32.MessageBoxW(
                    None, f"Sibyl could not open its log file:\n\n{LOG_FILE}\n\n"
                          f"{type(exc).__name__}: {exc}", "Sibyl", 0x10)
            sys.exit(1)

    parser = argparse.ArgumentParser(
        description="Sibyl - speak, and get back what you meant to write. "
                    "Runs entirely on this machine, start to finish.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="With no flags, runs in the background with a system tray icon.",
    )
    parser.add_argument("--test-audio", action="store_true",
                        help="record 5s to test_recording.wav. No models needed.")
    parser.add_argument("--test-hotkeys", action="store_true",
                        help="check which keys pynput can actually capture.")
    parser.add_argument("--test-ollama", action="store_true",
                        help="probe the Ollama daemon and list models.")
    parser.add_argument("--test-stt", metavar="WAV",
                        help="transcribe a WAV locally, with timings.")
    parser.add_argument("--test-llm", metavar="TEXT",
                        help="run the clean-up and the check on a string.")
    parser.add_argument("--test-pill", action="store_true",
                        help="show the notch through a fake capture. "
                             "No microphone, no models.")
    args = parser.parse_args()

    if args.test_audio:
        return test_audio()
    if args.test_hotkeys:
        return test_hotkeys()
    if args.test_ollama:
        return test_ollama()
    if args.test_stt:
        return test_stt(args.test_stt)
    if args.test_llm:
        return test_llm(args.test_llm)
    if args.test_pill:
        return test_pill()

    if not claim_single_instance():
        if _logging and not IS_MAC:
            # Nobody would see the message below, so it goes in a box.
            ctypes.windll.user32.MessageBoxW(
                None, "Sibyl is already running.\n\nLook for the dot above the "
                      "taskbar, or the circle in the system tray near the clock.",
                "Sibyl", 0x40)
            sys.exit(0)
        sys.exit(
            "\n  Sibyl is already running.\n"
            "  Look for the circle icon in your system tray, near the clock.\n"
            "  Quit it from there before starting another copy.\n"
        )

    try:
        Sibyl().run()
    except KeyboardInterrupt:
        print("\nStopped.")


if __name__ == "__main__":
    main()
