+++
summary = "News can halve or veto a tip, never raise a probability or a stake: the model does not see injuries, the market does"
tells = [
  { tool = "Bash", pattern = 'harness/(review|recheck)\.py' },
]
+++

# News is not a number

What happened: seeded at build time (plan Task 16), from the design (spec 5 and 6). When a key
player is ruled out, the market moves against his team and the model's edge on the other side
appears to grow. That is the market seeing what the team model cannot, not a better bet.
What generalises: your review decides keep, halve or veto, with a reason and saved evidence. A
grown edge after injury news is reported as "the market moved; the model does not see injuries"
and never increases a stake. If you think news should change a probability, propose a model
change as a lesson citing the ledger; do not act on it.
The tell: a `review.py` or `recheck.py` call is where a reading of the news becomes a decision.
