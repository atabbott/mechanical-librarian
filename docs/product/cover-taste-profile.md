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

While a profile is written, the tab reads "Writing your taste profile" and
checks again every 5 seconds. A failed build says what failed in one line,
such as "OpenRouter did not answer, so the taste profile was not written.", and
offers Try again. A daily limit or an unconfirmed charge names the local time
to try again after. Try again counts against the daily limit, and focus moves
to the build's state.

A profile shows its version, the day it was built, how many decisions it read
and its rules as "{n} of 40". Its numbered rules sit under four headings: What
you’re drawn to, What you avoid, How you trade them off, and When to keep or
switch. A heading with no rules is hidden. Versions lists every version, newest
first, the one in use marked "(in use)", each "Built {date} from {n}
decisions".

## Settings

Settings → Reviewer › Taste profile picks the OpenRouter vision model that
writes the taste profile, from recommended models measured for it. It says
what OpenRouter receives, and Test taste profile model and prices shows the
model's prices per input token, output token, image and request before Use
for taste profile saves it. It also states the daily limit, "Mechanical
Librarian asks for at most 8 taste profiles a day.", which the configuration
sets ([configuration](../reference/configuration.md#taste-profile)).
