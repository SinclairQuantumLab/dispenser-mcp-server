# Proposed recording-session continuation

User-requested future work; **not implemented**. Motivation: MCP restart changed
hardware run `8322b905` to `481a3bc3` while hardware continued at a commanded
1.2 A, splitting records and leaving an acquisition gap.

- Separate transport/process lifetime, conditioning/heating completion, and
  recording-session closure. Conditioning completion may be followed by cooldown
  pressure observations in the same recording session.
- Only an explicit agent **final recording-session close** followed by a new open,
  or a confirmed human dashboard **New Session**, starts a new session. A
  provisional assessment or conditioning-completion declaration does not close it.
- On restart, find the unfinished previous recording session and ask the operator
  **Continue Previous** or **New**. While awaiting the answer, append new data
  provisionally to that OPEN session with a restart-segment tag. Never append to
  an explicitly closed session.
- If New is selected, reassign only that provisional restart segment, preserving
  its original timestamps, IDs and raw data; never move prior history. Define the
  storage mechanics during implementation, without duplicating observations.
- Record continuation never energizes hardware or resumes control. Fresh hardware
  state and control authority remain separate. Record the acquisition gap rather
  than inventing missing samples.
- Keep existing per-session folder grouping and a lightweight pilot design; no
  event-sourcing or compatibility framework. No current server changes are implied.
