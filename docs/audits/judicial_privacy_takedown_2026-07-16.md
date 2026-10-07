# Judicial Privacy Act takedown request — Howard Chrisman / Judge Amy J. St. Eve

**Assessment date:** 2026-09-30
**Request received:** 2026-07-16, from Joshua Uribe, "Privacy Specialist", Ironwall by Incogni, on behalf of Judge Amy J. St. Eve (U.S. Court of Appeals, Seventh Circuit)
**Status of site at assessment:** `https://www.followthemoneyil.com/search?period=2026cycle&q=Chrisman` returns HTTP 503 "Temporarily offline / down for maintenance"

> Not legal advice. This is a research memo by a non-lawyer assistant. Statute text was pulled directly from ilga.gov on 2026-09-30; everything else is flagged with its source and confidence.

---

## 1. What was asked, and what we actually did

| Time (UTC, 2026-07-16) | Event | Source |
|---|---|---|
| 18:59 | Cloudflare WHOIS-relay message from joshua.uribe@ironwall.com, reason "content violating local laws". No body. | Gmail thread 19f6c4c5965d9a7b |
| 19:05 | Devin emails Uribe asking what the inquiry is about | Gmail thread 19f6c510df106613 |
| 19:46 | Second Cloudflare relay, from hunter.chapman@ironwall.com, reason "Research or other purpose". No body. | Gmail thread 19f6c4c5965d9a7b |
| 20:06 | Ironwall's substantive request (the screenshot). Cites 705 ILCS 90 (Judicial Privacy Act) and 5 ILCS 347 (Public Official Safety and Privacy Act). Names Howard Chrisman, 251 Beach Rd, Glencoe 60022-1317, and links the donor-key page and the search page. | Gmail thread 19f6c89bc11289b1 |
| 20:14 | Devin: "I have temporarily taken the website offline while investigating this." | same |
| 21:04 | Uribe: "Thank you for investigating this issue." | same |

**The "we have attempted to contact ... multiple times without receiving a response" line is not supported.** The two prior "attempts" were empty Cloudflare relay forms sent 47 minutes apart on the same afternoon, and Devin replied to the first one within six minutes. The site was taken offline eight minutes after the substantive request arrived, far inside the statute's 72-hour window.

## 2. What the site exposed

Local ISBE copy (`isbe_receipts`, Receipts.txt dated 2026-06-25):

- Howard Chrisman, 251 Beach Rd / Beach Road, Glencoe, 60022 or 60022-1317. Contributions 2014–2022 to Illinois Hospital Assn PAC and Lightfoot for Chicago; occupation/employer fields name Northwestern Medicine / Northwestern Memorial HealthCare.
- `redaction_requested = FALSE` on every Chrisman row. ISBE had not redacted this donor at source as of the June 2026 feed.
- The relationship is public: Wikipedia and FJC biographies record that Judge St. Eve married Howard B. Chrisman in 1993 and that he is a Northwestern physician. So the spouse's address is, in substance, the judge's home address.

Where the address surfaces in the app (all confirmed by grep on 2026-09-30):

1. `webapp/templates/search.html:122` — Address column on global search.
2. `webapp/templates/donors/detail.html:9` and `donors/detail_by_key.html:13` — "Address:" line.
3. `webapp/templates/candidate_finance/itemized.html:113` — donor address under each itemized receipt.
4. **The donor key itself embeds the street address in the URL path**: `/donors/key/howard|chrisman|251 beach rd||glencoe|il|60022-1317`. Hiding the address in the template does not hide it from the URL, link text, browser history, or search-engine indexes. `database/analytics.py` derives donor keys from name + address (`_stable_entity_key`, `_bulk_donor_key_sql`).
5. Any JSON/CSV/API endpoint or Viz Lab data endpoint that returns receipt rows with address fields. Not individually audited here.

No route or template references `redaction_requested`. That turns out not to matter for rows ISBE has already redacted, because ISBE blanks the address in the feed itself (address1 becomes the literal string "Redaction Requested", city and zip are emptied; 41,293 such rows in `isbe_receipts`). The site inherits ISBE redactions automatically on re-import. It has no mechanism of its own for a request that arrives directly, which is this case.

## 3. Does the Judicial Privacy Act (705 ILCS 90) apply?

Text pulled from ilga.gov section documents on 2026-09-30. Definitions section was amended by P.A. 104-278, effective 2026-01-01.

**Coverage — yes on every element.**

- §1-10 "Judicial officer" includes actively employed and former "(2) Judges of the United States Court of Appeals". Judge St. Eve sits on the Seventh Circuit.
- §1-10 "Immediate family" includes "a judicial officer's spouse".
- §1-10 "Personal information" means "a home address, home telephone number, ... marital status, and identity of children under the age of 18". The street address is a home address. Note that "marital status" is itself personal information, which bears on any page that would label Chrisman as the judge's spouse.
- §1-10 "Publicly available content" means "any written, printed, or electronic document or record ... maintained, controlled, or in the possession of a government agency that may be obtained by any person or entity, from the Internet, from the government agency upon request ..., or in response to a request under the Freedom of Information Act."

**The "it was already in public documents" argument does not work under this statute.** The Act's operative prohibition is written specifically about *republishing government records*. Public-record origin is the trigger, not a defense.

**Operative provision — §2-5, persons, businesses, and associations.**

- (a)(1): "All persons, businesses, and associations shall refrain from publicly posting or displaying on the Internet publicly available content that includes a judicial officer's personal information, provided that the judicial officer has made a written request ..."
- (a)(3): "This subsection includes, but is not limited to, Internet phone directories, Internet search engines, Internet data aggregators, and Internet service providers." A campaign-finance aggregator is an "Internet data aggregator" on any plain reading.
- (b)(1): 72 hours to remove after receiving a written request.
- (b)(2): must ensure the information is not available on any website or subsidiary website the person controls.
- (b)(3): must not transfer the information to any other person "through any medium" after the request. This reaches bulk exports and APIs.
- (c): Redress is injunctive or declaratory relief; if granted, the violator pays the judge's costs and reasonable attorney's fees. There are no statutory per-violation damages (contrast New Jersey's Daniel's Law, $1,000 per violation).
- §3-1 criminal provision (Class 3 felony) requires knowing publication with knowledge of an imminent and serious threat *and* the publication being a proximate cause of bodily injury or death. Not in play.

**Exceptions — there is no campaign-finance, news-media, or "required by law" exception.** The Act's only exception (§3-5) covers government employees publishing on the agency's own site. The Act's table of contents is §§1-1, 1-5, 1-10, 2-1, 2-5, 2-10, 3-1, 3-5, 4-1 through 4-99 (amendatory and effective-date sections). Nothing carves out election disclosures.

**The State Board of Elections itself treats contributor addresses as within scope.** ISBE runs a Redaction Request form (elections.il.gov/RedactionRequest.aspx) listing "Judge" and "Political Committee Officer" among eligible positions, and its bulk data dictionary documents `RedactionRequested` on Receipts as "Donor has requested address redaction under the Judicial Privacy Act". ISBE redacts the address and keeps name, amount, date, committee, occupation and employer. That is the template for what compliance looks like: the disclosure value of the record survives; only the street address goes.

**Procedural weakness in Ironwall's request (real, but curable).**

- §1-10 "Written request" means "written notice signed by a judicial officer or a representative of the judicial officer's employer".
- §2-10(c): a representative "may submit a written request on the judicial officer's behalf, provided that the judicial officer gives written consent to the representative and provided that the representative agrees to furnish a copy of that consent when a written request is made."
- §2-10(d): the request "shall specify what personal information shall be maintained private" and "shall disclose the identity of the officer's immediate family".
- §2-10(a): "No ... person ... shall be found to have violated any provision of this Act if the judicial officer fails to submit a written request".

Ironwall is a private vendor, not the judge's employer, and the email is not signed by the judge and does not attach or offer her written consent. On a strict reading the email may not yet be a valid statutory written request. The statute expressly entitles the recipient to a copy of the consent. Asking for it is reasonable for the file. Treating it as a reason not to act would be a poor bet: the defect is trivially cured, and the substantive obligation would then attach with a fresh 72-hour clock.

**Constitutional angle (unsettled; not a defense to rely on).** Republication of truthful, lawfully obtained public-record information has strong First Amendment protection (Florida Star v. B.J.F., 1989; Smith v. Daily Mail, 1979; Cox Broadcasting v. Cohn, 1975 — cited from general knowledge, not re-verified this session). No reported Illinois decision testing §2-5 against a private republisher was found. The closest analogue is New Jersey's Daniel's Law: the District of New Jersey rejected a facial First Amendment challenge in November 2024; the Third Circuit in September 2025 sent the statutory-interpretation question to the New Jersey Supreme Court (docket A-8-25); the litigation was still active in September 2026. Outcome of the NJ Supreme Court opinion was not confirmed in this session. A solo operator should not plan on being the Illinois test case over a street address.

## 4. Does the Public Official Safety and Privacy Act (5 ILCS 347) apply?

**No.** Public Act 104-0443 §10 defines "Public official" as: (1) members or former members of the General Assembly; (2) constitutional officers; (3) State's Attorneys; (4) appointed Public Defenders; (5) county clerks and Boards of Election Commissioners. Judges are not listed. The Act is otherwise a near-verbatim clone of the Judicial Privacy Act (same definitions of personal information and publicly available content, same 72-hour rule in §20, same fee-shifting). Ironwall cited it in error; it adds nothing here. (Effective date not confirmed this session; the Act passed both houses in late 2025.)

## 5. Federal law Ironwall did not cite: Daniel Anderl Judicial Security and Privacy Act

Not cited in the request, but it also covers federal judges and their immediate family, defines "covered information" to include the home address, and imposes a 72-hour removal duty on persons, businesses and associations after written notice. Reported exceptions (from secondary sources only; the statutory text could not be retrieved this session because congress.gov, govtrack and uscode.house.gov all blocked automated fetches): information relevant to and displayed as part of a news story or other speech on a matter of public concern; information the individual voluntarily published; information received from a *federal* government source. ISBE is a state source, so the government-source exception would not apply. Whether a campaign-finance site's display of a donor's street address is "speech on a matter of public concern" is arguable but untested. Verify the text before relying on any of this.

## 6. Bottom line

The request has substantial merit under the Judicial Privacy Act. Covered judge, covered family member, covered information, and a statute written specifically to reach republication of government records, with no election-disclosure exception. The two intuitive defenses ("it was public" and "the law wasn't meant for campaign-finance sites") both fail on the statute's text, and ISBE's own redaction practice confirms the legislature contemplated exactly this data.

The request's weaknesses are all procedural: wrong second statute, an overstated "no response" narrative, and a request not signed by the judge or accompanied by her consent. None of them changes what a court would order once the paperwork is fixed.

## 7. Recommendation

1. **Comply narrowly and permanently, mirroring ISBE.** Suppress the street address (and zip+4; consider city/zip as ISBE does) for Howard Chrisman's records. Keep name, amounts, dates, committees, occupation, employer. The transparency value of the record is untouched.
2. **Fix the URL leak first.** Because donor keys embed the street address, redaction has to reach key generation (or the key-to-URL mapping), not just templates. Otherwise the address remains in every link, in browser history and in search-engine indexes. After deploying, submit the affected URLs to Google's and Bing's outdated-content removal tools.
3. **Audit every exit path for §2-5(b)(3).** JSON/CSV endpoints, Viz Lab data routes, any bulk export. "Transfer through any medium" is broader than "display".
4. **Build a small standing mechanism.** A `privacy_redactions` table keyed on normalized name + address, applied at ETL or at render time, so a re-import does not resurface the address. Log the request, date received, and date actioned. This will not be the last such request; Ironwall and its competitors send these at scale.
5. **Point the requester at the durable fix.** The judge (or her spouse as the donor) can file ISBE's Redaction Request form. Once ISBE redacts at source, every downstream republisher, this site included, inherits it on the next import. Say this in the reply, but do not make your own compliance conditional on it.
6. **Reply in writing.** State what was removed and when (the site was offline within eight minutes, well inside 72 hours). For the file, ask for a copy of the judge's signed request and written consent as §2-10(c) contemplates. Phrase it as documentation, not a precondition.
7. **Publish a removal policy and a working contact.** The footer only renders a mailto if `PUBLIC_CONTACT_EMAIL` is set in prod (could not be verified this session; reading the prod env file was blocked). Ironwall reached you through Cloudflare's WHOIS relay, which suggests no contact was findable on the site.
8. **Do not contest.** Downside is an injunction plus fee-shifting; upside is keeping one street address online. If you want to keep addresses generally, get an Illinois attorney's view on the First Amendment posture before the next request, not after.
9. **Decide about the outage.** The site is returning 503 today. If it has been down since July 16 that is a large cost for a two-line redaction.

## 8. Things to verify independently

- Read §§1-10, 2-5, 2-10 yourself: `https://www.ilga.gov/Documents/legislation/ilcs/documents/070500900K2-5.htm` (swap the section suffix).
- Confirm judges are absent from 5 ILCS 347 §10: `https://www.ilga.gov/documents/legislation/PublicActs/104/104-0443.htm`.
- Retrieve the Anderl Act text and its exceptions (P.L. 117-263, §§5931–5936, codified as a note in 28 U.S.C. Part III). Automated fetches were blocked.
- Check how the New Jersey Supreme Court decided A-8-25 (Atlas Data Privacy v. We Inform) and the current Third Circuit posture, if you care about the constitutional question.
- Ask Ironwall for the judge's signed request and consent (§2-10(c)).
- Confirm `PUBLIC_CONTACT_EMAIL` is set in `/srv/illinois_campaign_finance/shared/.env`.
- Search `site:followthemoneyil.com Chrisman` and check Google's cache for the donor-key URL.
- Download a fresh Receipts.txt and check whether the Chrisman rows (IDs 5882824, 5227693, 5227694, 4854502, 4245904) now carry `RedactionRequested = True`, which would mean ISBE has since acted at source.
- Check `Bulk_download/.archive_*` snapshots and `bulk_receipts_clean` for un-redacted copies if you keep them.

## Sources

- Judicial Privacy Act sections (ilga.gov, fetched 2026-09-30): 1-5, 1-10, 2-1, 2-5, 2-10, 3-1, 3-5, 4-5.
- Public Act 104-0443 (Public Official Safety and Privacy Act), ilga.gov, fetched 2026-09-30.
- ISBE Redaction Request page: https://www.elections.il.gov/RedactionRequest.aspx
- ISBE bulk data dictionary, `Bulk_download/campaigndisclosuredatadictionary_*.txt`, lines 31, 139, 170, 204, 218, 249.
- Daniel's Law litigation: https://newjerseymonitor.com/2025/09/04/federal-appeals-court-boots-challenge-of-privacy-law-to-njs-top-court/ ; https://www.njcourts.gov/system/files/court-opinions/2025/a_8_25.pdf ; https://www.law360.com/appellate/articles/2525383
- Anderl Act summaries: https://en.wikipedia.org/wiki/Daniel_Anderl_Judicial_Security_and_Privacy_Act ; https://www.uscourts.gov/data-news/judiciary-news/2022/12/16/congress-passes-daniel-anderl-judicial-security-and-privacy-act
- Judge St. Eve biography: https://en.wikipedia.org/wiki/Amy_St._Eve ; https://www.fjc.gov/history/judges/st-eve-amy-joan
- Ironwall by Incogni: https://ironwall360.com/fedprotect ; https://ironwall.com/resources/case-studies/danger-appears-judges-front-door
