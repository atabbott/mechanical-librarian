# Taste profile

Covers → Taste profile shows the taste profile a vision model wrote from the
covers you chose and passed over, and lets you edit it. A second vision model
compares each work's covers with it and [suggests switches](#suggestions), or
[switches covers itself](#automatic-switching) once you turn that on and its
check passes; until then it only suggests (#1372). The tab has no count, and
the other tabs keep theirs while it is open. It belongs to the [review
experience](review-experience.md); what the model receives is in the [AI trust
boundary](../architecture/ai-boundary.md#taste-profile-builder).

Before the first profile, it reads "No taste profile yet" and says what is
missing: "Set up the taste profile model in Settings.", linking to Settings →
Reviewer (no model is chosen, or its Codex sign-in or OpenRouter key is not
ready), or "{n} of 25 picks saved." once any count, then Start 25 picks, which
opens Covers' [picks](#picks). A
first profile needs 25 recorded choices and a later one 10 more; at most 4
are scheduled a day.

While a first profile is written, and from the 25th choice until its build
starts (within a minute), "Writing your taste profile" is the
heading, and the tab checks again every 5 seconds. A build that starts while
the tab is open appears without a reload. After 10 seconds without input, it
appears on your next input. A failed build says what
failed in one line under "No taste profile yet", or above the profile in use,
such as "The taste profile could not be written." ("could not be updated"
beside a profile), and offers Try again. A daily limit or an unconfirmed
charge names the local time to try again after, and Try again waits until
then. Try again counts against the daily limit, and focus moves to the
build's state. With no model chosen, the tab names Settings instead. Once one
is chosen, a build that failed for lack of a model reads "could not be written"
or "could not be updated" and offers Try again. The 10 more choices count from
the last build that did not fail that way.

A profile shows its version, the day it was built, how many decisions it read
and its rules as "{n} of 40". Its numbered rules sit under four headings: What
you’re drawn to, What you avoid, How you trade them off, and When to keep or
switch. Every heading is shown with Add rule, even one with no rules yet.
Versions, beside the rules above 1024 pixels under Switching, lists every
version, newest first, the one in use marked "(in use)": a build reads "Built
{date} from {n} decisions" and an edit "Edited {date}".

## Picks

While the taste profile model is set up, no profile exists and fewer than 25
choices count toward one, To decide's list starts with "Pick 25 covers to get
suggestions", Start and Hide. Hide removes it from To decide in this browser;
Taste profile keeps Start 25 picks. On a first visit a four-step walkthrough explains
Covers, with Next and Skip, then Start 25 picks and Later on the last step.
Skip, Later, Escape and Start 25 picks each close it for good in this browser.

Start opens "Pick the cover you like" in the tabs' place, with "{i} of 25",
where {i} follows every choice that counts, and Stop. It
offers up to 25 works whose cover you can choose now from at least two
different covers: no held, fetching or already answered work, none whose
pick would not count, and at most two works of one first author or one
series. Works with three or more different covers come first. Each work's
covers are shuffled the same way every time, its current cover is marked
Current cover rather than placed first, and nothing is recommended. A pick is
the work's ordinary cover choice; Skip for now records nothing. A work decided
elsewhere meanwhile is passed over, and a work found later joins the end, so
the open work never changes under you. A link to the picks while Start is not
offered opens To decide instead.

Stop returns to To decide with "{n} of 25 picks saved.". When no work has two
different covers, Start says "No works have two different covers to pick
from yet."; when the works run out first, it says how many picks are saved
and that no more works have two different covers. After the 25th pick Covers
opens Taste profile, which reads "Writing your taste profile".

## Suggestions

Once a profile exists and a suggestion model is set up, each work whose cover
you can still choose is compared with the profile in use, once per version. A
work whose comparison names another of its covers is listed under To decide's
Switch suggested: Strong first, then Likely, then the measured rules'
suggestions. A suggestion is Strong when the model is at least 90% confident.
A work you already answered is never suggested.

The open work marks the suggested card "Suggested" and, below the covers,
"Suggested because" names Strong or Likely beside its heading and lists the
rules it rests on, by number and text, under this tab's section headings, so
a rule from "What you avoid" reads as one, with Edit taste profile, which
opens this tab. Nothing is added above the covers. Where the covers scroll
sideways, the row opens scrolled to the suggested card. Choosing it is the
ordinary choice; keeping the current cover or choosing another is a
correction, which the next rebuild reads first. After an edit, a suggestion
keeps the rules of the version it was made under, marked "From version {n}",
until the work is compared again. A work you have open stays open when a
suggestion moves it between tabs.

Under the build's state, the tab says when suggestions wait: "Set up the
suggestion model in Settings." with no model, the daily limit or an unconfirmed
charge with the local time they resume, and "The suggestion model could not
check {n} works against your taste profile." with Try again, which compares
them again and says "The suggestion model will check {n} works again.". During
a stop, the line says when Try again returns instead. A work counts only while
it could still be compared. At most 5,000 suggestions are asked for a day by
default ([configuration](../reference/configuration.md#taste-profile)). Inside
[Switching](#automatic-switching), "Confirmed {n} of {m} suggestions" counts
the suggested works you answered and how many took the suggestion.

## Automatic switching

Switching sits beside the rules above 1024 pixels and above them on narrower
screens. It is Suggest only until you choose Automatic, which lets the profile
switch a cover itself. Under Automatic, Only covers with a fault is Current
covers' setting in Policy: ticked, a cover is switched only to cure a fault the
measured rules found; cleared, any cover the profile prefers another for may be
switched. Changing it prepares every cover again, so each is compared with the
profile again, up to the daily limit, and Switching says so beside the box.
While Current covers is Never, Suggest only is in force, Automatic is off and
Switching links to it. Setting Never in Policy also sets Switching back to
Suggest only; lifting Never leaves it there.
Until your check passes, which a later release records, Automatic only
suggests switches, and Switching says so.

Choosing Automatic first opens a preview above the rules and saves nothing. It
shows up to 5 switches the profile would make among at most 100 covers already
compared under the version in use, each with the Now and New covers, the fault
if any and the rules behind it. Only covers with a fault and its cost sit under
the preview's line, before the switches. Looks right chooses that cover now as
your own choice ("New cover chosen"); Keep current keeps the current one, a
correction ("Current cover kept"). The preview's line
says how many switches it found in how many covers, "so far" while covers are
still being compared, "the limit" at 100, and "starting with last time’s" when
a switch was suggested under an earlier version too. The preview reads again
every 5 seconds while covers are being compared. Turn on saves Automatic at
once, without waiting for the preview; Not now closes it.

A switch binds the version it was made under. After an edit or a rebuild, a
switch not yet written is withdrawn, shows as a suggestion again and is
compared again. Switched names each taste switch's version, "Taste profile
version {n}", with its rules, and its Show menu narrows the list to one
version. Put back, Keep and Others work as for any switch; while Others is open
a taste row hides its rules and reason line. Activity names only the version.

## Editing rules

Each rule has a pencil ("Edit rule {n}") that opens it in place: one line of
at most 400 characters with a counter, Save rule, Cancel and Remove. Add rule
opens an empty rule at the end of its section, with no Remove. One rule is
open at a time. Each saved change, an edit, an added rule or a removal, is a
new version at once, and "Rule {n} saved." or "Rule {n} removed." confirms it.
A rule you wrote is marked "Yours", a model rule you changed "Edited". An edit
keeps a rule's number; an added rule takes the next number above the highest,
and a rebuild numbers rules from 1 again. Escape closes a rule as Cancel does.
At 40 rules, Add rule says "40 rules is the limit." beside it.

A rule must have text, at most 400 characters, and no web address or file
path; the browser refuses these first, and the server also refuses passwords
and hidden characters. A rule the profile has no room for, past 40 rules,
1,200 words or 8,192 characters, is refused. If the profile changed since the
page loaded, such as a rebuild while the rule was open, nothing is saved and
the tab says so. When no answer comes back, the rule stays open with what you
typed and the tab reads the profile again.

A rebuild keeps your rules and edited rules word for word, ahead of the
model's in each section, and the model is asked not to repeat, reword or
contradict them. It does not write again a rule you removed or the wording you
replaced: such a repeat is left out. While your rules leave no room for
another rule or word, no rebuild is scheduled; Try again still asks for one.

## Settings

Settings → Reviewer › Taste profile, inside the reviewer panel below the
description adviser, picks what writes the taste profile: Codex or an
OpenRouter vision model. It says what the model receives, which model is
selected, and the daily limit, "Mechanical Librarian asks for at most 8 taste
profiles a day.", which the configuration sets
([configuration](../reference/configuration.md#taste-profile)). Continue
without a taste profile stops taste profiles and keeps cover review and
description advice.

Settings → Reviewer › Suggestions, below it, is built the same way for the
model that suggests switches: it says what that model receives, the daily
limit, "Mechanical Librarian asks for at most 5,000 suggestions a day.", Use
Codex for suggestions, Test suggestion model and prices, Use for suggestions,
and Continue without suggestions, which stops suggestions only.

The Codex row appears once Codex is signed in or selected for any task. It
names the Codex model, says taste profiles count against the plan's usage
limits, and Use Codex for taste profiles selects it; it is disabled until the
Codex sign-in is usable. While the Codex sign-in is not usable, no taste profile
build is scheduled. The OpenRouter row offers recommended models measured
for taste profiles, and Test taste profile model and prices shows the model's
prices per input token, output token, image and request before Use for taste
profile saves it. A failure shows under the button that met it.
