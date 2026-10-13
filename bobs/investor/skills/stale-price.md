+++
summary = "Prices come from prices.py with their session date; a quote from a web page has no session and is not evidence"
tells = [
  { tool = "WebFetch", pattern = 'finance\.yahoo|google\.com/finance|marketwatch|investing\.com' },
]
+++

# A price without its session

What happened: seeded from the design, before any failure. Trading agents in published tests quoted
prices with no timestamp, then compared them with stale data.
What generalises: every price this Bob uses is a bar from prices.py, cited with the evidence folder
and the session date; anything behind the last completed session is called stale.
The tell: fetching a finance web page is the moment a price without a session would enter a thesis.
