# Security and Privacy Policy

Sibyl's entire claim is that what you dictate stays on your machine. A hole in
that is the most serious kind of bug this project can have, so it gets its own
reporting route.

## What counts as a security or privacy problem here

Please report privately, not in a public issue, if you find any of the
following:

| Report privately | Why it matters |
|---|---|
| **Anything leaving the machine** — a network call to anywhere other than the configured local model runtime, or the one known first-run download below | This is the product's one promise |
| **Dictated text written to disk** anywhere other than the temporary WAV, which is deleted in a `finally` block | Dictation holds names, addresses, medical details, whatever you said |
| **Dictated text, window titles, or the model's reply appearing in the log file** | The log is the file people attach to bug reports |
| **A recording left behind** after a capture, a crash, or a quit mid-dictation | Same reason |
| **Clipboard contents being read**, kept, or sent anywhere | Sibyl writes the clipboard; it should never harvest it |
| **A path, command or filename taken from your speech and executed** | Speech is untrusted input |
| **A dependency shipping a phone-home, a telemetry pass, or a post-install script** | A supply-chain route around the one rule |
| **Code execution** from a `.env` value, a model name, or a model's reply | |

## One known, documented network call — please do not report this as a finding

On its **first run only**, `faster-whisper` downloads the `base.en` speech model
(about 150 MB) from Hugging Face, over its own HTTP transport rather than through
Sibyl's `requests` calls. It is then cached on your disk and loaded from there
with no contact at all. Only a file of it that has gone missing is fetched again.

This is expected, is documented in the
[README's privacy audit](README.md#the-one-exception-stated-plainly), and carries
nothing about you — it is a one-way fetch of a public file. Installing needs the
internet; using Sibyl does not.

**What would be a real finding** is that call carrying anything outward: an
identifier, a usage count, a version ping, or any of your text. If you see that,
report it privately.

## One known local socket — also not a finding

While Sibyl runs, it holds a socket open on `127.0.0.1` port **49731**. It is a
lock: a second copy of Sibyl finds the port taken and stops, so two copies never
fight over the hotkey and the microphone. Sibyl never accepts or reads a
connection on it and sends nothing through it, and `127.0.0.1` cannot be
reached from another machine. It is why `netstat` shows a Sibyl port besides
the connection to Ollama.

## What does not need a private report

Open a normal public issue for these. They are bugs, not security problems:

- The clean-up produced bad text, dropped a word, or obeyed an instruction.
- The hotkey does not fire on your keyboard.
- The dot is in the wrong place, or the wrong colour.
- Anything in the install process.

## How to report privately

**Use GitHub's private vulnerability reporting.** Go to this repository's
**Security** tab and choose **Report a vulnerability**. That opens a private
thread visible only to the maintainers. It needs no email address from you and
exposes none from anyone else.

If that is unavailable to you for any reason, open a public issue containing
**only** the sentence "I would like to report a security issue privately" and
nothing else, and wait to be contacted. Do not put the details in it.

### Please include

1. **What you found**, in one sentence.
2. **How to reproduce it**, step by step.
3. **What you observed**, and how you observed it — for example the exact
   `netstat` line, the file path, or the log excerpt.
4. **Which copy of Sibyl.** There are no version numbers, so name the commit:
   the short code beside the latest change on the repository's page when you
   downloaded it, or the output of `git log -1` in your copy. And your Python
   and Ollama versions.

### Please do not include

- **A real dictation.** Reproduce it with invented text of the same shape.
- **Your `.env` file.** It may hold real names of real people.
- **A recording of anyone's voice.**

## What happens next

This is a small project maintained by one person in their own time, so please
expect days rather than hours.

| When | What |
|---|---|
| Within about a week | An acknowledgement that the report arrived |
| After that | An assessment, and either a fix or an explanation of why it is not one |
| At the fix | A note in the release describing the problem and crediting you, unless you would rather not be named |

**Please give it 90 days before disclosing publicly**, or less if we agree on
less. If you get no response at all within 30 days, treat that as a dead
project and disclose as you see fit — that is fairer to users than silence.

## Supported versions

Only the current `main` branch is supported. There are no maintained release
branches, and no backports.

## What Sibyl deliberately does not protect against

Stated plainly, so nobody relies on something that was never promised.

| Not protected | Why |
|---|---|
| **Anyone with access to your unlocked machine** | The last cleaned-up text is held in memory and is on your clipboard. Anyone at your keyboard can press Ctrl+V |
| **Other programs on your machine reading your clipboard** | Windows clipboard is shared by design. Sibyl must use it in order to paste. Each entry carries Microsoft's documented opt-out from clipboard history and cloud sync, but any program running at the time can still read it |
| **Other programs on your machine reading the recording** | During each dictation your voice exists as a temporary WAV file until the dictation ends, after the paste, and it is deleted. That is usually seconds, longer if the clean-up model has to load first. A program already running as you could read it in that time |
| **Other programs on your machine talking to Ollama** | Ollama answers any program on this computer, not only Sibyl. Sibyl sends it your words for the clean-up; what Ollama itself keeps or logs is Ollama's behaviour, not Sibyl's |
| **Another program watching your keyboard** | Sibyl is not an anti-keylogger |
| **A malicious model you install yourself** | Sibyl asks Ollama for whatever `CLEANUP_MODEL` names. Only install models you trust |
| **A malicious dependency you install yourself** | `requirements-lock.txt` names every library at the exact version tested, which stops a newer, unreviewed release arriving unasked; verifying them is still the normal responsibility of installing anything from PyPI |
| **Disk forensics on deleted files** | The temporary WAV is deleted, not shredded. A deleted file is recoverable until overwritten |
| **Your microphone being on** | Sibyl records only while you hold the keys, but it cannot stop anything else from recording |
| **A model licence you breach** | The default clean-up model is non-commercial. That is a legal matter, not a technical one. See the [README](README.md#licence) |
