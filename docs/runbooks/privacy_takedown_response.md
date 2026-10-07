# Runbook: personal-information removal requests (Judicial Privacy Act and similar)

**Applies to:** any request to remove a home address, phone number, or other personal information of a judge, public official, or family member from followthemoneyil.com.
**Legal background and the July 2026 case analysis:** `docs/audits/judicial_privacy_takedown_2026-07-16.md`.
**Implementation plan for the tooling referenced below:** `.claude/plans/privacy-redaction.md`.

> Tooling status (2026-09-30): the `privacy_redactions` table, the `add-privacy-redaction` / `apply-privacy-redactions` commands, the ETL re-apply hook and the public `/privacy` page are built and tested locally (plan steps 1–5). Not yet deployed to prod (plan step 6).

---

## 1. Governing rules in one paragraph

Under the Illinois Judicial Privacy Act (705 ILCS 90) a person or business that receives a valid written request from a judicial officer must remove the judge's personal information (including a spouse's home address, since it is the judge's home address) from the Internet within **72 hours**, must not transfer it onward, and faces an injunction plus fee-shifting if it does not. "Publicly available content" in that Act *means* government records, so "it came from ISBE" is not a defense. The Public Official Safety and Privacy Act (5 ILCS 347) is the same rule for legislators, constitutional officers, State's Attorneys, public defenders, and election clerks; it does not cover judges. The federal Daniel Anderl Act adds a parallel 72-hour duty for federal judges. ISBE's own practice is the compliance template: replace the street address with the literal string `Redaction Requested`, blank address2/city/state/zip, keep name, amount, date, committee, occupation, and employer.

## 2. The July 2026 case (Howard Chrisman / Judge Amy J. St. Eve) — action checklist

| # | Action | Owner | Status |
|---|---|---|---|
| 1 | Take the exposure offline within 72 hours | Devin | Done 2026-07-16 (whole site put in maintenance mode 8 minutes after the request) |
| 2 | Deploy the tooling, then record + apply the request with `add-privacy-redaction` (exact command in `.claude/plans/privacy-redaction.md` step 6) | Devin | Open |
| 3 | Rebuild derived data: the command refreshes `isbe_condensed_receipts`; run `refresh-analytics --with-snapshot` in tmux so `analytics_donor_summary` is rebuilt with the redacted row | Devin | Open |
| 4 | The command flushes Redis; then `systemctl restart ilcf-web.service` (in-process search cache) | Devin | Open |
| 5 | Bring the site out of maintenance mode | Devin | Open |
| 6 | Verify: search for the name shows no street address; the old donor-key URL returns 404; CSV export for the committee shows `Redaction Requested` | Devin | Open |
| 7 | Submit the old donor-key URL and search URL to Google Search Console "Remove outdated content" and Bing Webmaster "Content removal" | Devin | Open |
| 8 | Reply to Ironwall (template in section 6). Ask for the judge's signed request and written consent per 705 ILCS 90/2-10(c), as documentation | Devin | Open |
| 9 | Suggest the ISBE Redaction Request form (elections.il.gov/RedactionRequest.aspx, position "Judge") so the redaction happens at source | Devin | Include in reply |
| 10 | Confirm `PUBLIC_CONTACT_EMAIL` is set in `/srv/illinois_campaign_finance/shared/.env` so the footer shows a working contact | Devin | Open |
| 11 | The request is recorded in `privacy_redactions` by step 2; also update the log in section 7 | Devin | Open |

## 3. Standard procedure for any future request

1. **Acknowledge the same day.** A one-line reply ("received, investigating") starts the record. Do not argue the law in the first reply.
2. **Check coverage.** Is the subject a judicial officer (705 ILCS 90/1-10: US Supreme Court, Courts of Appeals, District, Magistrate, Bankruptcy, Illinois Supreme, Appellate, Circuit, and administrative law judges) or a covered public official (5 ILCS 347 §10)? Is the named person the officer or an immediate family member (spouse, child, parent, or co-resident blood relative)? Is the data "personal information" (home address, phone, personal email, marital status, minor children)? If all three: comply.
3. **Check the request's form, but do not stall on it.** A valid request is signed by the officer, or by a representative of their employer who furnishes the officer's written consent (§2-10(c)). Vendors such as Ironwall, Atlas, or DeleteMe are neither. Ask for the consent copy for the file, and comply anyway. The defect is curable and the clock restarts when they cure it.
4. **Redact within 72 hours of the request.** Use `add-privacy-redaction` (section 5). Redact the address only, ISBE-style. Never delete the contribution record; that would falsify the disclosure history.
5. **Rebuild derived data and flush caches.** The command handles the matview and Redis; restart `ilcf-web.service`; run `refresh-analytics --with-snapshot` in tmux.
6. **Verify every exit path**: `/search`, `/donors/key/<key>`, `/donors/<id>`, `/api/donors`, `/api/donors/<id>`, itemized page and its `?format=csv`, expenditures CSV, Viz Lab data endpoints. See the memo §2 for file:line references.
7. **Search engines.** Submit the old URLs for outdated-content removal. The donor key embeds the street address in the URL path, so the URL itself is the leak until the key changes.
8. **Reply** with what was removed and when. Point at the ISBE redaction form so the fix propagates to every downstream republisher.
9. **Log it** (section 7 or the `privacy_redactions` table).

What we do **not** do: contest the request, demand proof of a threat, or wait for the signed consent before acting. What we do **not** redact: name, amounts, dates, committee, occupation, employer. Those are the disclosure.

## 4. What redaction looks like (mirror ISBE exactly)

| Column (`isbe_receipts`) | After redaction |
|---|---|
| `address1` | `Redaction Requested` |
| `address2`, `city`, `state`, `zipcode` | NULL |
| `redaction_requested` | TRUE |
| everything else | unchanged |

Because donor keys are `first|last|address1|address2|city|state|zip`, the redacted donor gets a new key (`howard|chrisman|redaction requested||||`) and the old address-bearing URL 404s. That is the intended outcome.

## 5. Applying a redaction

On the server, from the app root with the shared venv:

```bash
python3 run.py add-privacy-redaction --first <First> --last <Last> --zip <zip5> \
  --requested-by "<vendor or person> on behalf of <officer>" --request-date YYYY-MM-DD \
  --statute "705 ILCS 90" --notes "<how the request arrived; consent received?>"
systemctl restart ilcf-web.service
```

What the command does, in order: inserts the request row; scrubs matching rows in `isbe_receipts`, any real `bulk_receipts_clean*` table and `donors` (section 4 format); deletes the donor's `analytics_donor_summary` rows so `/search` stops showing the address immediately; runs `REFRESH MATERIALIZED VIEW CONCURRENTLY isbe_condensed_receipts` (about 29 s on 3.6M rows locally; no lock on readers); flushes the `ilcf:cache:*` Redis keys; prints per-table counts. Matching is exact last name + first five digits of the zip, first name optional (`--first` omitted matches any first name). It never deletes a contribution row.

Then, in tmux: `python3 run.py refresh-analytics --with-snapshot` so `analytics_donor_summary` is rebuilt with the redacted address (the donor reappears on `/search` with "Redaction Requested").

Re-applying: `python3 run.py apply-privacy-redactions` re-runs every recorded request (use after `sync-prod-db` or a manual DB change). `sunshine-import` re-applies them automatically before it builds matviews, so the weekly ISBE reload cannot resurface a redacted address.

Options: `--no-apply` records without applying; `apply-privacy-redactions --skip-matview-refresh --skip-cache-flush` for dry checks.

## 6. Reply template

> Subject: Re: Urgent Request | followthemoneyil.com
>
> Thank you for your message of [date]. The street address associated with [name] has been removed from followthemoneyil.com, including the search page, the donor page, and all export endpoints, and the previous donor URL no longer resolves. This was completed on [date/time], [within N hours of your request]. The contribution records themselves remain, with the address shown as "Redaction Requested", which matches the Illinois State Board of Elections' own redaction format.
>
> For our records under 705 ILCS 90/2-10(c), please send a copy of the judge's signed written request and her written consent to your firm acting on her behalf.
>
> Because our data is imported from the State Board of Elections' bulk files, the most durable fix is for the judge or her spouse to file the Board's Redaction Request form (elections.il.gov/RedactionRequest.aspx, position "Judge"). Once the Board redacts at source, every downstream site that uses its data, including ours, inherits the redaction automatically. We have also submitted the previous URLs to Google and Bing for removal from their indexes.
>
> Please let me know if you find any remaining instance.

## 7. Request log

| Received | Requester | On behalf of | Statute cited | Data | Redacted on | Notes |
|---|---|---|---|---|---|---|
| 2026-07-16 | Joshua Uribe, Ironwall by Incogni | Judge Amy J. St. Eve (7th Cir.), spouse Howard Chrisman | 705 ILCS 90; 5 ILCS 347 (does not apply) | Street address, Glencoe 60022 | pending | Site offline since 2026-07-16. Signed request/consent not yet received. |
