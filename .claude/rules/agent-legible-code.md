---
paths:
  - "src/**/*.py"
---

# Write code that agents (including you) can verify

This codebase is maintained primarily through agentic sessions. Code
that an agent can read, run, and check its own work against beats code
that is merely clever. When choosing between equivalent designs, prefer
the one a fresh session can verify without tribal knowledge.

- **Descriptive names over clever abstractions.** A function named for
  what it does is documentation that cannot drift. Avoid metaprogramming,
  dynamic dispatch tricks, and indirection layers that make "who calls
  this?" unanswerable by reading.
- **Simple control flow.** Early returns over nested conditionals;
  explicit branches over flag arguments threaded through call stacks.
- **Rich, observable output.** Log meaningful state transitions to
  stdout/structlog so a session can run the code and *see* what happened
  rather than inferring it. Example: a dev-mode email sender that prints
  the message to stdout lets an agent verify a sign-up flow end-to-end
  with no mailbox access.
- **Make failure loud and specific.** Error messages should name the
  input and the expectation that failed — they are the feedback channel
  the next session debugs through.
- **Harden tools against misuse.** Internal scripts and CLI helpers get
  argument validation and a `--help` that states intent; assume a future
  agent will call them with plausible-but-wrong arguments.
- **Plain interfaces over frameworks** where the choice is free: a
  function that takes data and returns data is verifiable in one pytest;
  a framework hook is verifiable only inside the framework.
