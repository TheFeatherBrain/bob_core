+++
summary = "An odds team name not in the alias table is 'could not tell': the game is dropped and the name reported, never matched by guess"
tells = [
  { tool = "Bash", pattern = 'harness/(teams|odds)\.py' },
]
+++

# Never guess a team

What happened: seeded at build time (plan Task 16), from the design. "Real" could be Real Madrid or
Real Betis; "Olympiacos B" is a different team. Odds attached to the wrong game make an edge that
does not exist, and the ledger would record a tip on a game nobody priced.
What generalises: `odds.py` drops a game whose names do not map and lists them as `UNMAPPED`. Write
the name in the tracker for the person; the alias table (`harness/teams.py`) grows only by a
reviewed edit, never by a match you infer.
The tell: running `teams.py` or `odds.py` is the moment an unmapped name appears and the temptation
to "fix" it by guessing arises.
