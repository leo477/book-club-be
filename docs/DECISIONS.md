# Decisions

## Randomizer winner is chosen client-side (SCRUM-181)

Status: accepted.

`POST /api/v1/clubs/{club_id}/randomizer/sessions` records a draw; it does not perform one. The client picks the winner and sends it as `result`. The server only checks that every candidate is a member of the club, that candidates are unique, and that `result` is one of the candidates. It does not verify that the pick was random or unbiased.

Why this is acceptable: the randomizer is a convenience for a book club to choose a book or host among its own members, run by an organizer. Nothing of value depends on it, and the organizer who submits the result could equally announce any winner without using the feature. Server-side selection would not remove that trust (the organizer decides whether to use the outcome) and would cost the in-browser reveal animation, which needs the result before it plays.

Revisit if a draw ever gates something with real stakes (prizes, moderation outcomes, or anything other members cannot overrule). The change would be to omit `result` from the request and have the server pick with `secrets.choice` from the validated candidates.
