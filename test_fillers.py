"""The fixed rules around the model: do they keep the layout and the words?

Two rules, no model. `fix_technical_terms` corrects how the speech recogniser
spells technology names, before the model sees the text. Since 2026-09-12 it no
longer touches react, flask or docker, which are ordinary English as often as
they are software. `_strip_leading_fillers` tidies up after the model, and since
the same day it capitalises only a sentence it actually changed ("iPhone" had
become "IPhone") and leaves openers that carry meaning ("I mean to...").

`_strip_leading_fillers` removes a stray "So," or "Um" that the model left at the
start of a sentence. Until 2026-09-11 it did that by splitting the text into
sentences and gluing them back with spaces, which turned every line break into a
space. Harmless while the output was always one paragraph; fatal once the
clean-up started writing bullet points and paragraphs, because a perfectly
formatted answer from the model would reach the window as one flat line.

No model and no microphone needed. Instant.

Run:  python test_fillers.py
"""
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[0]))
import sibyl

results = []


def check(name, got, expected):
    ok = got == expected
    print(f"  {'PASS' if ok else 'FAIL'}  {name}")
    if not ok:
        print(f"        expected: {expected!r}")
        print(f"        got:      {got!r}")
    results.append(ok)


strip = sibyl._strip_leading_fillers

print("The tidy-up after the model")

check("bullets stay on their own lines",
      strip("Three things:\n- Email the landlord.\n- Book the MOT.\n- Collect the suit."),
      "Three things:\n- Email the landlord.\n- Book the MOT.\n- Collect the suit.")

check("a paragraph break survives",
      strip("The report is nearly done.\n\nSeparately, the offsite moved."),
      "The report is nearly done.\n\nSeparately, the offsite moved.")

check("a filler at the start of a bullet goes, the bullet stays",
      strip("Plan:\n- So book the MOT.\n- Um collect the suit."),
      "Plan:\n- Book the MOT.\n- Collect the suit.")

check("a filler at the start of a later paragraph goes",
      strip("First topic.\n\nAnyway, second topic."),
      "First topic.\n\nSecond topic.")

check("fillers still go within one line",
      strip("So we should go. Um, then we eat."),
      "We should go. Then we eat.")

check("a real word that looks like a filler survives",
      strip("Well done to the team. Right alignment is broken."),
      "Well done to the team. Right alignment is broken.")

check("numbered items keep their numbers",
      strip("Steps:\n1. So feed it.\n2. Mix it."),
      "Steps:\n1. Feed it.\n2. Mix it.")

# -- words that start in lower case keep it (review round, 2026-09-12) ----------

check("iPhone and eBay are not capitalised",
      strip("iPhone sales are up. eBay is slow."),
      "iPhone sales are up. eBay is slow.")

check("a sentence after e.g. is not capitalised",
      strip("Bring the red one, e.g. the big box."),
      "Bring the red one, e.g. the big box.")

check("nor are bullets that start in lower case",
      strip("- iPad first.\n- x86 build second."),
      "- iPad first.\n- x86 build second.")

# -- openers that carry meaning survive ---------------------------------------------

check("'I mean to' is meant, not a filler",
      strip("I mean to finish it by Friday."),
      "I mean to finish it by Friday.")

check("'You know the answer' is meant, not a filler",
      strip("You know the answer."),
      "You know the answer.")

check("'so' before far, much, many and long is meant",
      strip("So far so good. So much to do. So many came. So long as it works."),
      "So far so good. So much to do. So many came. So long as it works.")

check("'I mean' and 'you know' still go before a comma",
      strip("I mean, the meeting moved. You know, it rained."),
      "The meeting moved. It rained.")

check("'Okay' that means yes survives",
      strip("Okay, that works."),
      "Okay, that works.")

check("'Okay' still goes when another filler follows it",
      strip("Okay, so the meeting moved. Okay so it rained."),
      "The meeting moved. It rained.")

# -- the spelling rules leave ordinary words alone ---------------------------------

terms = sibyl.fix_technical_terms

check("react, flask and docker are left as said",
      terms("I need to react quickly, so I packed a flask of tea and the docker "
            "at the port was late."),
      "I need to react quickly, so I packed a flask of tea and the docker "
      "at the port was late.")

check("a real mis-spelling is still fixed",
      terms("we moved it to sq lite"),
      "we moved it to SQLite")

# Found in G2, 2026-10-02: the rule made "ql" optional, so a correctly said
# "PostgreSQL" came back as "Postgres", a different word. The rules fix how a
# name was heard, never which name was said.
check("a correctly said PostgreSQL is left as said",
      terms("her talk on PostgreSQL logical replication"),
      "her talk on PostgreSQL logical replication")
check("a mis-spelled PostgreSQL is fixed to PostgreSQL, not Postgres",
      terms("we run postgresql and post gres ql"),
      "we run PostgreSQL and PostgreSQL")
check("and a mis-spelled Postgres is still fixed to Postgres",
      terms("the post gres server and postgres"),
      "the Postgres server and Postgres")

# -- a filler that survives in the middle of a sentence ----------------------------
#
# Measured 2026-09-20, every run of three: the model returned "I'm not completely
# certain, but you know, I think the Leeds office might close in March." The rule
# already knew "you know", but only looked at the start of a sentence, so a filler
# sitting after a joining word survived. Worse, `faithful` has no filler rule, so
# the answer was trusted and pasted all three runs.
#
# The comma is the whole guard, and it is the same guard the sentence-start rule
# already used: a meant "you know" is not followed by one.

check("the measured failure: 'but you know,' in the middle of a sentence",
      strip("I'm not completely certain, but you know, I think the Leeds office "
            "might close in March."),
      "I'm not completely certain, but I think the Leeds office might close in March.")

check("a meant 'you know' has no comma, and is left alone",
      strip("I need to check because you know the answer already."),
      "I need to check because you know the answer already.")

check("'I mean' goes the same way",
      strip("I think so, but I mean, it might slip."),
      "I think so, but it might slip.")

check("'well' after 'because' is left: it was never measured, and 'and well, truly' exists",
      strip("Ask Dana, because well, she has the file."),
      "Ask Dana, because well, she has the file.")

# The review of 2026-09-23 blocked a wider version of this rule. "so you know"
# is a fixed phrase meaning "for your information", not two fillers, and
# "and I mean" is emphasis. Both lost real words, on the path that pastes the
# speaker's own text. Only "but" survived, and these three hold it there.

check("'So you know,' keeps its whole preface: it means for your information",
      strip("So you know, I will not be in tomorrow."),
      "So you know, I will not be in tomorrow.")

check("'And I mean,' keeps its emphasis",
      strip("And I mean, literally every single one."),
      "And I mean, literally every single one.")

check("'because you know,' is left too: only 'but' was measured",
      strip("I checked twice, because you know, the last one was wrong."),
      "I checked twice, because you know, the last one was wrong.")

check("a capital 'But' stays capital when its filler goes",
      strip("But you know, I think it slipped."),
      "But I think it slipped.")

check("a hedge that follows a joining word is never touched",
      strip("It is late, but maybe we can still finish."),
      "It is late, but maybe we can still finish.")

check("a joining word with no filler after it is left alone",
      strip("I called the office, but nobody answered."),
      "I called the office, but nobody answered.")

# This test asserted the opposite when it was written, and the assertion was
# wrong. After a joining word "now" means at this moment, so deleting it edits
# what was said. Same for "look" and "right". Only the two words measured
# failing, and which carry no second meaning before a comma, are removed here.
check("'and now,' keeps its 'now': after a joining word it means at this moment",
      strip("I sent the draft, and now, we wait."),
      "I sent the draft, and now, we wait.")

check("'and look,' keeps its 'look': it can be an instruction",
      strip("Stand by the window, and look, over there."),
      "Stand by the window, and look, over there.")

check("bullets and paragraphs still survive the new rule",
      strip("- Email the landlord, but you know, not today.\n\n- Book the MOT."),
      "- Email the landlord, but not today.\n\n- Book the MOT.")

# Batch D4 of round 3, 2026-10-02. When the check does not trust the model, your
# own words are pasted with only the fixed rules applied, and until now those
# kept every remark about dictating ("before I forget"), which the model is told
# to remove. The rule takes only measured phrases, only at a sentence start, and
# only before a comma or full stop: a meant "before I forget the details" has
# neither, and that is the whole guard.
print("\nThe fallback's remarks about dictating")

fallback = sibyl.rule_clean

check("'Before I forget,' goes, the sentence after it stays",
      fallback("Before I forget, the car needs an oil change."),
      "The car needs an oil change.")

check("a whole sentence that is a remark goes, as the travel ramble was measured",
      fallback("So I want to get this down before I forget it. Next spring I'm going "
               "to Porto."),
      "Next spring I'm going to Porto.")

check("a remark sentence in the middle goes without leaving a gap",
      fallback("Next spring I'm going to Porto. I want to get this down before I forget "
               "it. Then Lisbon."),
      "Next spring I'm going to Porto. Then Lisbon.")

check("'this is just me thinking out loud for a minute.' goes, as the work ramble was "
      "measured",
      fallback("Right, so this is just me thinking out loud for a minute. I think the "
               "proposal is ready."),
      "I think the proposal is ready.")

check("'Just thinking out loud,' goes, the hedge after it stays",
      fallback("Just thinking out loud, we could possibly hire a temp."),
      "We could possibly hire a temp.")

check("a meant 'before I forget' mid-sentence keeps its words",
      fallback("Finish this before I forget the details."),
      "Finish this before I forget the details.")

check("a meant 'Before I forget' at the start, with no comma after it, keeps its words",
      fallback("Before I forget the password, write it down."),
      "Before I forget the password, write it down.")

check("'I want to get this down' meant literally keeps its words",
      fallback("I want to get this down to one page."),
      "I want to get this down to one page.")

check("a remark in the middle of a sentence is left alone",
      fallback("We could, just thinking out loud, hire a temp."),
      "We could, just thinking out loud, hire a temp.")

check("a dictation that is only a remark is kept, never pasted empty",
      fallback("Just thinking out loud."),
      "Just thinking out loud.")

# Found by the review of D4: the guard asked only whether anything was left, and
# "Uh." leaves its full stop behind, so a lone "." was pasted. Until 2026-10-02
# this expected ". Before I forget.", the defect fixed below.
check("a remark after a lone filler is kept, never pasted as a full stop",
      fallback("Uh. Before I forget."),
      "Before I forget.")

check("an ellipsis after a remark leaves no stray dots",
      fallback("Before I forget... call the bank."),
      "call the bank.")

check("a dictation that is only a remark and an ellipsis is kept, never pasted as dots",
      fallback("Just thinking out loud..."),
      "Just thinking out loud...")

# Also found by that review: the comma form deleted a meant first half, so "I
# want to get this down" goes only before a full stop or with its "before I
# forget" ending.
check("'I want to get this down,' meant literally keeps its words",
      fallback("I want to get this down, to under ten pages."),
      "I want to get this down, to under ten pages.")

check("the layout survives a removed remark",
      fallback("Before I forget, call the bank.\n\nThe MOT is due."),
      "Call the bank.\n\nThe MOT is due.")

# 2026-10-02. Whisper writes "Uh." or "Um." as a sentence of its own, and the
# fallback removed the word but left its full stop, pasted as a sentence of its
# own. Present since the fallback was written.
print("\nThe fallback's fillers said as a sentence of their own")

check("'Uh.' at the start goes with its full stop",
      fallback("Uh. Call the bank."),
      "Call the bank.")

check("'Um.' between two sentences goes with its full stop",
      fallback("Call the bank. Um. Then the MOT."),
      "Call the bank. Then the MOT.")

# Found by the review of this fix: the same defect with a question mark, and
# with a run of fillers.
check("'Hmm?' at the start goes with its question mark",
      fallback("Hmm? Call the bank."),
      "Call the bank.")

check("'Uh, um.' at the start goes whole",
      fallback("Uh, um. Call the bank."),
      "Call the bank.")

check("a filler ending a sentence leaves that sentence's full stop, so two never merge",
      ". Then the MOT." in fallback("Call them uh. Then the MOT."),
      True)

check("a paragraph break after a lone filler survives",
      fallback("Call the bank. Um.\n\nThen the MOT."),
      "Call the bank.\n\nThen the MOT.")

passed = sum(results)
print(f"\n{passed}/{len(results)} passed")
sys.exit(0 if passed == len(results) else 1)
