---
name: jira-refine
description: Refine a Jira ticket into small, independently-shippable pieces of work the whole team can pick up in parallel. Reads the ticket's description and already-linked tickets via the Jira API, interviews the user as a principal architect to close every gap, then outputs an epic/story/task/spike breakdown with dependencies. Use when given a Jira ticket key (e.g. TICKET-123) and asked to refine, break down, split, decompose, or plan it into sub-tickets.
---

# Jira ticket refinement

Runs anywhere a Python 3.7+ interpreter is available — Claude Code, opencode,
or a bare CLI wired to a local model (qwen, llama, etc). No third-party
dependency: `scripts/jira_client.py` uses only the Python standard library.

## Setup

Requires these environment variables, already exported before this skill runs:

- `JIRA_URL` — base URL, no trailing slash (e.g. `https://yourcompany.atlassian.net`)
- `JIRA_TOKEN` — API token (Cloud) or Personal Access Token (Server/Data Center)
- `JIRA_EMAIL` — **required for Jira Cloud** (any `*.atlassian.net` instance):
  the account email the token belongs to. Cloud only accepts Basic auth
  (`email:token`), not Bearer. Leave unset only for a Server/Data Center
  instance, where the token is a PAT sent as `Bearer`.

If a required var is missing, `scripts/jira_client.py` prints
`{"error": "..."}` and exits 1 instead of a raw traceback — surface that
error to the user and stop rather than guessing at credentials. A `403` with
body `"Failed to parse Connect Session Auth Token"` means `JIRA_EMAIL` is
missing against a Cloud instance — set it and retry.

Optionally, `JIRA_REFINE_CONTEXT_DIRS` — a `:`-separated list of absolute
paths to domain-context repos (see Step 0). If unset, there is no domain
context — Step 0 simply does less.

Optionally, `JIRA_REFINE_SOURCE_REPOS` — an absolute path to a directory
whose immediate subdirectories are real source-code checkouts (e.g.
`~/repos/github.com/your-org` containing `service-a/`, `service-b/`, etc).
Unlike the context repos above, this is **read
reactively, never upfront** — see Step 2.5. If unset, that step does
nothing.

## Step 0 — load domain context, if any is configured

Before touching the ticket, check for domain-context repos: read
`JIRA_REFINE_CONTEXT_DIRS` and, for each path that exists, read its
`CONTEXT.md` in full — these are short entry-point
docs, written to be read whole. Do not open every file under a context
repo's `docs/` — `CONTEXT.md`'s own "File map" section names which `docs/*.md`
files go deeper on which topics; open only the ones that turn out relevant
to *this* ticket, once you know what the ticket is about from Step 1.

Each `CONTEXT.md` carries a "Gaps / uncertainties" section — treat those
gaps as topics you still must ask the user about in Step 2, never as
settled by the doc's silence. This context makes your questions and
descriptions domain-accurate; it does not replace confirming scope,
ownership, or sizing with the user.

If no context repo exists at all, skip this step and proceed exactly as
before — nothing downstream depends on it being present.

## Step 1 — load the ticket and its existing context

Run:

```
python3 scripts/jira_client.py get <TICKET-KEY>
```

This returns the ticket's summary, description, type, status, labels,
components, parent, subtasks, and issue links as one JSON object.

If the result has a non-empty `subtasks`, `issuelinks`, or `parent`, or its
`issuetype` is `Epic`, also run:

```
python3 scripts/jira_client.py children <TICKET-KEY>
```

to list everything already filed underneath it (classic Epic Link or
next-gen parent). Read every linked/child ticket's summary before continuing
— the point is to never propose work that already exists as a separate
ticket.

Completion criterion for this step: you can state, in your own words, what
the ticket is asking for and what (if anything) is already broken out under
it. If the description is too thin to state that, treat "the description is
too vague to summarize" itself as the first finding to raise in Step 2.

## Step 2 — interview the user as a principal architect

Do not draft the breakdown yet. First close every gap a principal engineer
would refuse to plan around. Ask in small batches (2-4 questions), wait for
the answers, and push back with a sharper follow-up on any vague answer
("it depends", "not sure", "whatever's easiest") before moving on — a vague
answer is not an answered question.

If Step 0 loaded any domain context, use it to make your questions sharper,
not fewer: skip re-asking something the context docs state as settled fact
(e.g. which team owns a given repo, or a term's real meaning), but turn
every one of that context's own "Gaps / uncertainties" that bears on this
ticket into a direct question below.

Cover all of the following before moving to Step 3. Track them yourself;
none may be skipped, though "no constraint" / "out of scope" is a valid
answer to any of them:

1. **Scope boundary** — what is explicitly in scope and what is explicitly
   out of scope for this ticket?
2. **Definition of done** — what acceptance criteria or observable outcome
   proves the whole thing is finished?
3. **Technical dependencies** — which existing systems, services, schemas,
   or APIs does this touch, and are any of them owned by another team?
4. **Sequencing constraints** — is there anything that genuinely *must*
   happen before something else (a real technical dependency), as opposed
   to something that only feels natural to do first?
5. **Parallel capacity** — how many people/sub-teams could plausibly work on
   pieces of this at once, and do they differ in skill area (frontend,
   backend, infra, data, QA)?
6. **Risk / unknowns** — is there anything nobody on the team has done
   before, that deserves a time-boxed spike instead of a committed estimate?
7. **Team structure** — is this single-team work, or does it span multiple
   teams? If multiple, name them — this decides whether items get grouped
   by team in the breakdown.
8. **Parent structure** — should the broken-down items sit under a *new*
   Epic created for this work, or link directly to the ticket being refined
   (no new Epic)? If the source ticket is already an Epic (or the user
   wants to reuse an existing Epic), confirm which parent to use instead of
   defaulting to either choice.

Completion criterion: every topic above has an explicit answer. Only then
proceed to Step 3.

## Step 2.5 — targeted repo lookup, only when a specific question demands it

`JIRA_REFINE_SOURCE_REPOS`, if set, is a directory of real source-code
checkouts (e.g. `ci-framework/`, `architecture/` as subdirectories). Never
scan it upfront and never open a whole repo "just in case" — this is the
opposite of Step 0's context docs. Reach for it only at the moment a
specific question — one of yours, or one the user just asked back — names
a concrete system/behavior that the curated context docs don't already
settle, e.g. "how does ci-framework handle retries in test-operator" or
"does openstack-operator already support X."

When that happens:

1. Match the named system to a subdirectory of `JIRA_REFINE_SOURCE_REPOS`
   by name.
2. Search inside only that repo for the specific term/behavior in question
   (grep for the keyword, then read only the files that match) — never a
   full read of the checkout.
3. If no matching subdirectory exists, or the var is unset, say so plainly
   and ask the user directly rather than guessing at the answer.



Fold whatever you learn back into the relevant Step 2 answer or Step 3
description; this step has no output of its own.

## Step 3 — draft the breakdown

Definitions to apply consistently:

- **Epic** — a body of work spanning multiple stories/tasks. Whether one
  gets created here is decided entirely by Step 2 question 8's answer, not
  by your own judgment of size — follow what the user chose: a new Epic as
  the parent for every item, or every item linking directly to the ticket
  being refined.
- **Story** — a unit of user-facing or system behavior, independently
  testable and shippable.
- **Task** — technical work with no independent user value on its own
  (migration, config, plumbing, CI change).
- **Spike** — a time-boxed investigation with no committed deliverable
  beyond an answer or a decision; used for the risk/unknowns surfaced in
  Step 2, question 6.

Rules:

- Default to maximizing parallel tracks. Only sequence two items when Step
  2's answers named a real technical dependency between them — never
  because one item "feels like" it should come first.
- Every item must be independently completable by one person or one
  sub-team without blocking on a teammate's in-flight work, except where an
  explicit dependency says otherwise.
- Size each item using Fibonacci story points (1, 2, 3, 5, 8, 13, ...), the
  same scale the team estimates with elsewhere. Estimate your best guess
  from the item's description and the answers gathered in Step 2 — never
  leave a size blank — but treat every size as a draft: Step 4 always
  surfaces it back to the user for review, since you're guessing at a team's
  velocity and unknowns, not measuring it.
- An item sized 13 or higher is a signal it isn't actually one
  independently-shippable piece — split it further before presenting it.
- Every dependency must point at another item *in this plan* (or explicitly
  at an already-existing linked ticket found in Step 1) — never a vague
  "depends on X area."
- If Step 2 question 7 named multiple teams, tag every item with which team
  owns it, and group the breakdown by team when you present it.
- Use the terminology, component names, and ownership boundaries from any
  domain context loaded in Step 0 (e.g. which team actually owns a repo,
  what a term like "unijob" or "promoted-component" means) instead of
  generic phrasing — this is what makes a description read as written by
  someone who knows the codebase, not a paraphrase of the ticket.

For each item produce: type, title, one-paragraph description, acceptance
criteria, `depends_on` (list of item ids or "none"), a Fibonacci size
estimate, parent (the Epic or the source ticket, per Step 2 question 8),
and a suggested owning team/skill area.

## Step 4 — review in the conversation before writing anything

Present the full plan as a reply in the conversation — never as a file
write. Structure the reply with these sections, in order:

1. **Summary** — one paragraph restating what the source ticket asks for.
2. **Existing linked/child work** — table of tickets found in Step 1, so
   nothing gets re-proposed.
3. **Plan overview** — counts: N epics, N stories, N tasks, N spikes, and
   total item count.
4. **Dependency table** — columns: id, title, depends_on.
5. **Full breakdown** — every item from Step 3 with its full fields.
6. **Open questions / risks** — anything Step 2 flagged as out of scope,
   uncertain, or worth revisiting before work starts.

End the reply by explicitly calling out the size estimates as your own
guesses and asking the user to confirm or correct each one, separately from
the general "anything else to change?" question — sizing is the one field
in this plan you're least equipped to get right unassisted, and it should
never sail through unreviewed.

Treat this as a normal iteration loop, not a single pass: apply whatever
the user asks to change, show the updated plan again in the conversation
the same way, and keep iterating for as many rounds as the user wants.

Completion criterion for this step: the user has said something equivalent
to "looks good" / "write it" / "save it" for the plan *as currently shown*,
**and** has responded to the sizing callout specifically (confirming the
estimates or giving corrected ones) — a general "looks good" that never
touched sizing is not enough on the first round; ask about sizing directly
if it goes unanswered. An answer to one clarifying question inside this
loop is not that signal — only proceed to Step 5 on an explicit go-ahead to
write the file.

## Step 5 — write the file

Only after Step 4's completion criterion is met: save the exact plan just
approved to `<TICKET-KEY>-refinement-plan.md` in the current working
directory, unless the user named a different location. Do not write the
file at any earlier point, and do not silently fold a write into a Step 4
iteration.
