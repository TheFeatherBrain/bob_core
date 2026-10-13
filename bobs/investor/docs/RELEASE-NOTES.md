# Investor Scout release notes

Newest first. Each change to this Bob or its tooling gets an entry saying what changed and what it
means for someone who relies on this Bob's output. "Nothing changes for you" is information too.

## Scorecard v2: sectors, agreement, market conditions

- Growth and quality now rank within each company's sector (Fama-French 12 from its SEC SIC code),
  or against all stocks when the sector is small; each signal on the card says which.
- A top-ranked name needs at least 3 known factor groups above median to be eligible; otherwise
  it is "watch", with the count as the reason.
- Banks, insurers and real estate (SIC 6000 to 6799) no longer get an Altman Z distress veto.
- Every new call stores its full scorecard, the market conditions and the screen file's hash.
- New: `macro.py` (FRED, context only), `ledger_score.py --by-signal`, `data_audit.py`.
- What it means for you: scores from v2 are not comparable with v1; calls made under v1 stay in the
  ledger and are scored as before. Expect fewer eligible names than under v1.

## Scaffold

Folder created by new_bob.py. Nothing runs yet; see PLAYBOOK.md section 3 for first launch.
