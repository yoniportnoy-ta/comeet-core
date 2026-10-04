# comeet-core

One Comeet Recruiting API client for every Riverside service.

Before this existed there were two complete implementations — `comeet_recruiting`
in pipey-bot and `app/comeet_client.py` in auto-screener — with the same auth,
the same pagination and different bugs. Every quirk had to be learned twice.

```python
from comeet import ComeetClient, fields

with ComeetClient() as cc:                 # credentials: env, then dotenv
    pos = cc.get_position("41.264")
    print(fields.position_lead_recruiter(pos))
```

## What's here

| module | holds |
|---|---|
| `client.py` | the HTTP client: HS256 JWT, retry, 429/bandwidth detection, 401 refresh, `next_page` pagination |
| `auth.py` | token minting and credential discovery (env → `~/.config/comeet/.env`) |
| `errors.py` | `ComeetError` / `ComeetTransientError` / `ComeetBandwidthError` |
| `fields.py` | pure readers over Comeet payloads — names, locations, JD text, recruiter |
| `resolve.py` | numeric id → alphanumeric uid, with an injected cache and a scan budget |
| `cv.py` | résumé fetch (size + deadline bounded) and docx/rtf/doc text extraction |
| **`QUIRKS.md`** | **every trap that cost someone a day. Read it before changing `client.py`.** |

## What deliberately isn't here

Policy. `candidate_active_for_screening` and `candidate_in_allowed_step` read
per-service configuration to decide whether to *act* on a candidate, and the
answer differs per service — so they stay with their service. The line is:
decoding Comeet's data shape belongs here, deciding what to do about it does not.

Report rendering, scoring, briefs and corpus mining likewise stay put.

## Install

```
pip install -e .                      # local checkout
pip install git+ssh://git@github.com/yoniportnoy-ta/comeet-core.git
```

## Tests

`pytest tests/` — no network. The live smoke test needs credentials and is run
by hand; every case in the unit tests corresponds to a real incident.
