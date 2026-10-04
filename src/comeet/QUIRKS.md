# Comeet API quirks

Every item here cost somebody real time. Read before changing `client.py`.

**Numeric id ≠ API uid.** The recruiter UI and profile URLs carry a numeric id
(`/can/60235428`); the Recruiting API keys on an alphanumeric uid (`AE.17934`).
A numeric id returns `404 {"error":"not_found"}`, which reads like "no such
candidate" and is not. Use `resolve.resolve_uid()`.

**Résumé URLs expire in 15 minutes.** `candidate.resume.url` is a presigned S3
link. Storing it is pointless — by the time anything reads it back it is dead.
Fetch on demand, or serve a redirect that re-fetches.

**`/candidates/{uid}/files` is not the CV.** It holds recruiter-uploaded extras —
in practice reference-call PDFs — and never the résumé, which is only on
`candidate.resume`. It also never holds the signed offer. Most candidates have
zero files.

**The signed offer is not in the public API.** `/offers`, `/documents`,
`/attachments` all 404. The offer step's `/download` on the internal API gives a
302 to a presigned S3 copy. `completed_steps` containing "Offer" is the public
signal that someone reached that stage.

**`/notes` is POST-only.** GET returns 405.

**`source` casing differs between API and CSV export.** The API says
`"Active search"`, the CSV says `"Active Search"`. A case-sensitive comparison
silently reports every sourced candidate as inbound.

**The referrer is `source.name`, not `source_contact`.**

**Hire date is `opening.hired_candidate_hire_date`** — never
`time_last_status_changed`, which moves on any status change.

**The list endpoint already has step data.** `/positions/{uid}/candidates`
returns the same shape as the per-candidate detail call, so walking a position
needs one request, not N+1.

**Positions: `status=open` is a filter, not the default.** Plain `/positions`
returns open *and* closed. Closed reqs are where completed hiring outcomes live,
so anything mining history wants them; only live-feed paths want open-only.

**The live per-position feed hides rejects.** Candidates rejected at screen do
not appear. Build decision history from mined/closed data instead.

**Internal Render DB hostnames do not resolve cross-region.** Unrelated to
Comeet, but it broke this stack for days: use the external host.
