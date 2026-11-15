# Taste profile

Covers → Taste profile shows the taste profile a vision model wrote from the
covers you chose and passed over, read-only. It changes no cover, suggestion
or decision yet (#1372). The tab has no count, and the other tabs keep theirs
while it is open. It belongs to the [review experience](review-experience.md);
what the model receives is in the
[AI trust boundary](../architecture/ai-boundary.md#taste-profile-builder).

Before the first profile, it reads "No taste profile yet" and says what is
missing: "Choose a taste profile model in Settings.", linking to Settings →
Reviewer, or "Mechanical Librarian writes it once you have chosen 25 covers in
To decide. You have chosen {n}.", linking to To decide. A first profile needs
25 recorded choices and a later one 10 more; at most 4 are scheduled a day.

While a first profile is written, "Writing your taste profile" is the
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
switch. A heading with no rules is hidden. Versions, beside the rules above
1024 pixels, lists every version, newest first, the one in use marked "(in use)", each "Built {date} from {n}
decisions".

## Settings

Settings → Reviewer › Taste profile, inside the reviewer panel below the
description adviser, picks what writes the taste profile: Codex or an
OpenRouter vision model. It says what the model receives, which model is
selected, and the daily limit, "Mechanical Librarian asks for at most 8 taste
profiles a day.", which the configuration sets
([configuration](../reference/configuration.md#taste-profile)). Continue
without a taste profile stops taste profiles and keeps cover review and
description advice.

The Codex row appears once Codex is signed in or selected for any task. It
names the Codex model, says taste profiles count against the plan's usage
limits, and Use Codex for taste profiles selects it; it is disabled until the
Codex sign-in is usable. The OpenRouter row offers recommended models measured
for taste profiles, and Test taste profile model and prices shows the model's
prices per input token, output token, image and request before Use for taste
profile saves it. A failure shows under the button that met it.
