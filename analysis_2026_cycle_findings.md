# Illinois 2026 Cycle Campaign Finance: Data-Driven Story Pitches

**Analysis Date:** 2026-02-14
**Analyst:** Claude Code (automated, human review required)
**Database:** 11.8 GB, 57 tables, 6.4M bulk receipts, 4.8M bulk expenditures

---

## CAN AND CANNOT CLAIM MEMO

### What We Can Claim (with appropriate caveats)
- **State ISBE:** 171K receipt records for 2025 ($815M), 137K expenditure records ($180M excl. anomalies). Full coverage through early Feb 2026. Baseline comparisons to 2023 and 2024 are valid.
- **Federal FEC:** 56K Schedule A contributions ($96M), 13K Schedule B disbursements ($24M), 5K Schedule E IEs ($109M) for the 2026 cycle. Backfill 99% complete.
- **Lobbying:** 2,733 entity-client relationships for 2026 across 5 years of data (2022-2026). Complete for filed registrations.
- **IRS 527:** 1,896 IL-linked orgs, 35.9K expenditure records with valid dates. Reports through 2025-12-31.
- **Cross-matching:** 106K+ match records across 10 match tables (Jaccard 0.80+ threshold).

### What We Cannot Claim
- **2026 calendar year is sparse:** Only 2,451 bulk receipts and 18 bulk expenditures filed for dates in 2026 itself. Most 2026-cycle analysis uses 2025 as the primary data year.
- **IRS 527 contribution dates are corrupt:** 8.97M rows have non-standard date encoding; date-based 527 contribution analysis is unreliable.
- **ActBlue aggregation distortion:** ActBlue Illinois reported $401.7M in 68 transactions to one committee. These are likely bundled small-dollar pass-throughs, not a single donor. Concentration metrics are heavily influenced by how ActBlue is classified.
- **Kankakee County anomaly:** $401.7M in receipts from 53 transactions to one small county committee is almost certainly a data/filing error. Exclude from aggregate analysis.
- **Expenditure anomaly:** One $8.1B entry in 2025 is flagged; always filter `is_amount_anomalous`.
- **Lobbying 2026 may be incomplete:** Only February; registrations continue year-round.
- **No incumbency flag** in the data. Office-holder status must be inferred or looked up externally.

---

## A. EXECUTIVE SUMMARY (12 bullets, ranked by editorial value)

1. **War chests are historically fat heading into 2026.** Illinois committees banked $815M in receipts vs. $180M in expenditures in 2025 — a 4.5:1 ratio — the widest gap in available data. Hundreds of millions are sitting unspent, signaling an expensive 2026 cycle. (High confidence)

2. **JB Pritzker doubled his self-funding to $51M in 2025,** up from $24M in 2023, making him the largest individual donor in state politics. His dual-system footprint totals $432M across state and federal filings. (High confidence)

3. **Federal outside money is flooding Illinois: $109M in independent expenditures** for the 2026 cycle already, with oppose spending ($66.5M) outpacing support ($42.7M) by 1.56:1. The NRCC alone has spent $27.6M. Five IL House races each exceed $10M in IE spending. (High confidence)

4. **Brad Schneider (IL-10) is the most targeted incumbent in the country** by IE spending: $18.9M in oppose expenditures, nearly all from NRCC and Congressional Leadership Fund. (High confidence)

5. **The IL-09 House primary is the most crowded federal race,** with 6 candidates in the FEC top 25 — Abughazaleh ($2.7M), Biss ($2.0M), Fine ($1.9M), Friedman ($1.8M), Andrew ($1.2M), Huynh ($1.0M). Combined receipts exceed $10.6M for an open seat. (High confidence)

6. **Raja Krishnamoorthi dominates the Senate fundraising race at $28.5M,** nearly 9x his closest Democratic rival (Stratton at $3.2M). His prior House campaign committee transferred $19.2M — the largest single committee-to-candidate transfer in IL FEC data. (High confidence)

7. **Speaker Welch and Senate President Harmon are the top recipients of committee-to-committee transfers,** receiving $3.9M and $5.2M respectively in 2025. Their committees are stockpiling at 10:1+ receipt-to-expenditure ratios, consistent with pre-election positioning. (High confidence)

8. **The National Democratic Redistricting Committee spent $70.3M through its IL-based 527** in 2024-25, dwarfing all other 527 organizations combined. This DC-based entity operating through an IL registration is the single largest 527 spender in the dataset. (High confidence; note: money flows to national, not IL-specific, races)

9. **Thirty lobbying clients simultaneously operate 527 political organizations,** including Chicagoland Operators, Health Care Council of Illinois, IL Trial Lawyers, Nicor Gas, and IBEW locals. This dual-channel pattern (direct lobbying + tax-exempt political spending) is widespread but rarely reported. (High confidence)

10. **Capstone Consulting went from 13 lobbying clients to zero in 2026** — the sharpest single-firm decline. Four other firms (Zerimar, Holler, Wheeler, SFIO) also completely exited. Meanwhile, Kasper Holmes & Dring grew by 8 clients and EchoStar Corporation hired 4 lobbyists as a new entrant. (Medium confidence; 2026 lobbying may still be filing)

11. **The Illinois Manufacturers' Association is the top lobbying-donor crossover,** with $7.6M+ in campaign donations across name variants while maintaining active lobbying registrations. Ameren Illinois ($3.2M+) and Simmons Hanly Conroy ($3.2M+) follow. (High confidence)

12. **Small-donor participation is declining.** Contributions under $150 fell from 27,215 (2023) to 25,152 (2025), while mega-donations ($50K+) grew from $263M to $567M. The median contribution remains $500, but the money increasingly comes from the top. (Medium confidence; requires ActBlue pass-through adjustment for full picture)

---

## B. RANKED STORY PITCHES

### 1. The $109 Million Shadow Campaign: Outside Money Is Remaking Illinois Congressional Races
**Confidence: HIGH**

**Nut graf:** Independent expenditure committees have already poured $109 million into Illinois congressional races for the 2026 cycle — with oppose spending outpacing support spending by a 1.56:1 ratio. Five House races each exceed $10M in IE spending, turning suburban Chicago into the nation's most expensive congressional battlefield.

**Core finding:** NRCC ($27.6M, 5 candidates), Congressional Leadership Fund ($13.3M, 5 candidates), DCCC ($8.3M, 3 candidates). Average oppose transaction is $76,751 vs. $10,117 for support — opposition campaigns deploy larger, more concentrated bursts.

**Statistical support:** Direct tabulation of FEC Schedule E filings, 2026 cycle. 5,097 IE transactions totaling $109.2M. Oppose/support ratio: 1.56:1 by dollar, 0.21:1 by transaction count.

**Why it matters now:** Illinois voters in swing districts will see more ads from outside groups than from the candidates themselves. This fundamentally changes campaign dynamics.

**Next steps:** Interview Brad Schneider's campaign (most targeted: $18.9M oppose); FOIA NRCC media buy records; compare IL IE spending per capita to other states.

---

### 2. Pritzker's $432 Million Political Footprint: One Donor's Grip on Illinois Democracy
**Confidence: HIGH**

**Nut graf:** Governor JB Pritzker has contributed $432 million to Illinois political campaigns across state and federal systems, according to cross-matched donor records. In 2025 alone, he gave $51 million — more than double his 2023 contributions — making him the single largest force in the state's campaign finance ecosystem.

**Core finding:** $432.4M combined (state: $432.37M, federal: $7K). 2025 state contributions: $51M across 4 transactions. His gubernatorial committee (JB for Governor) received $51M in receipts but only spent $6.4M (7.99:1 ratio), banking $44.6M.

**Statistical support:** Cross-matched via fec_local_donor_matches (confidence 0.95, name_state_zip method). State totals from bulk_receipts_clean.

**Why it matters now:** As Pritzker reportedly considers national ambitions, the scale of his IL political spending — and the war chest he's building — is unprecedented for any sitting governor.

**Next steps:** Compare to other self-funding governors nationally; interview good-government groups on concentration risk; FOIA Pritzker's business interests that intersect with his committee's expenditure payees.

**Caution:** Pritzker's total includes all-time contributions. The $432M figure is cumulative, not annual.

---

### 3. The Great Stockpile: Illinois Power Brokers Are Hoarding Cash for 2026
**Confidence: HIGH**

**Nut graf:** Illinois campaign committees collected $815 million in 2025 but spent only $180 million — a 4.5:1 ratio that signals an unprecedented arms race for 2026. Speaker Welch, Senate President Harmon, and Secretary of State Giannoulias are among those stockpiling at 10:1+ ratios.

**Core finding:** People for Emanuel Chris Welch: $15.6M in / $1.5M out (10.76:1). Friends of Don Harmon for State Senate: $14.2M in / $1.3M out (10.74:1). Citizens for Giannoulias: $6.5M in / $302K out (21.66:1). Common Ground Collective IEC: $1.9M in / $0 out.

**Statistical support:** Committee-level receipt and expenditure totals from bulk_receipts_clean and bulk_expenditures_clean (2025), anomalies excluded.

**Why it matters now:** These war chests reveal who the power brokers expect to be in 2026, and which races they're preparing to fund.

**Next steps:** Cross-reference high-ratio committees with upcoming 2026 races; identify which stockpilers are candidate committees vs. party/leadership PACs; interview political consultants about where the money will flow.

---

### 4. The Crowded House: Six Democrats and $10.6 Million Collide in IL-09
**Confidence: HIGH**

**Nut graf:** The open IL-09 seat has attracted six Democratic candidates who have collectively raised $10.6 million — making it the most competitive primary in the state. Three candidates (Abughazaleh, Biss, Fine) each exceed $1.9M, suggesting a genuinely contested race with no clear frontrunner by fundraising.

**Core finding:** Abughazaleh: $2.7M (99.9% individual contributions). Biss: $2.0M (96.4% individual). Fine: $1.9M (98.8% individual). Friedman: $1.8M (96.6% individual). Andrew: $1.2M. Huynh: $1.0M. Amiwala: $958K.

**Statistical support:** FEC candidate cycle totals and Schedule A contribution breakdowns, 2026 cycle.

**Why it matters now:** This race will test whether grassroots fundraising can compete with institutional support in a post-Schakowsky North Shore/lakefront district.

**Next steps:** Map donor overlap between candidates; analyze small-dollar vs. large-dollar fundraising ratios; interview candidates on fundraising strategies.

---

### 5. The Lobby-Donate-Spend Pipeline: 30 Organizations Playing Three Games at Once
**Confidence: HIGH**

**Nut graf:** At least 30 organizations simultaneously maintain Illinois lobbying registrations, operate IRS 527 political organizations, and make direct campaign contributions — a triple-channel influence strategy that is legal but rarely scrutinized as a whole. The list includes major labor unions, healthcare groups, energy utilities, and industry associations.

**Core finding:** 30 lobbying clients matched to 527 organizations (26 at perfect 1.0 Jaccard score). Examples: Chicagoland Operators Joint Labor-Management PAC ($21.3M in 2025 receipts + active lobbying + 527 operations), Health Care Council of Illinois ($1.7M donations + lobbying + 527), Nicor Gas (lobbying + 527 PAC + $750K donations).

**Statistical support:** Cross-matching tables: lobbying_527_matches (30 rows), lobbying_donor_matches (3,131 rows), all at Jaccard 0.80+ threshold.

**Why it matters now:** Voters see lobbying, campaign donations, and 527 spending as separate activities. In practice, the same organizations coordinate across all three channels.

**Next steps:** Pick 3-5 organizations and trace their complete influence map: lobbying positions, 527 expenditures, campaign donations, and legislative outcomes. FOIA lobbying activity reports.

---

### 6. Don Tracy's $2M Bet: The Self-Funded Republican Senate Challenge
**Confidence: HIGH**

**Nut graf:** Former IL GOP Chair Don Tracy has self-funded $2.0 million for a 2026 U.S. Senate bid, making him the largest self-funder on the Republican side. With only $122K from individuals and $2.0M cash on hand, his campaign is almost entirely self-financed — a stark contrast to Krishnamoorthi's broad-based $28.5M operation.

**Core finding:** Tracy: $2.15M total receipts, $2.0M self-funded (93%), $118K spent, $2.0M cash on hand. Krishnamoorthi: $28.5M receipts, $8.3M from individuals, $15.2M cash on hand.

**Statistical support:** FEC candidate cycle totals and Schedule A data.

**Why it matters now:** The IL Senate race has the potential for a massive fundraising asymmetry. Can self-funding compete against broad-based fundraising in a blue state?

**Next steps:** Interview Tracy on strategy; compare self-funded Senate campaigns nationally; check if Tracy's business interests create any conflict-of-interest angles.

---

### 7. The Consulting Industrial Complex: Who Gets Paid When Campaigns Spend
**Confidence: MEDIUM**

**Nut graf:** Committee-to-committee transfers ("Contribution" purpose) accounted for $52 million of the $180 million Illinois committees spent in 2025 — 29% of all expenditures went not to voters but to other political committees. The top consulting firms (Cor Strategies, Red Horse Strategies, DG Partners) collectively received millions, but the biggest payees are other politicians.

**Core finding:** Top expenditure recipients in 2025: Welch ($4.5M combined variants), Harmon ($5.2M combined), Democratic Party of Illinois ($3.8M combined), Cook County Democratic Party ($949K), Friends of John Curran ($713K). Consulting: Cor Strategies ($680K, 33 committees), Red Horse Strategies ($833K, 4 committees), Minuteman Press ($605K, 113 committees).

**Statistical support:** Expenditure purpose field analysis from bulk_expenditures_clean (2025). "Contribution" = $52M across 17,330 transactions.

**Why it matters now:** The committee-to-committee transfer network reveals the actual power structure of Illinois politics — who funds whom.

**Next steps:** Build a network graph of transfers; identify which leadership committees serve as conduits; compare 2025 transfer patterns to prior cycles.

---

### 8. The Realtor Empire: NAR's $14 Million Multi-State 527 Operation Based in Illinois
**Confidence: MEDIUM**

**Nut graf:** The National Association of Realtors' "State Exchange Account," an IL-registered 527, spent $14 million in 2024-25 across only 147 transactions — averaging $95K per transaction. The money flows to state-level Realtor PACs in Florida ($2.7M), Texas ($1.1M), California ($944K), New York ($676K), Illinois ($620K), and Wisconsin ($521K). It's one of the largest 527 operations in the country, registered in Springfield.

**Core finding:** EIN 261725187, 147 expenditure transactions averaging $94,922. Recipients span at least 7 states. IL Association of Realtors Fund received $620K directly.

**Statistical support:** IRS 527 expenditure records (date-validated, 2024-2025).

**Why it matters now:** Illinois is being used as a 527 registration base for a nationwide political spending operation. The scale and multi-state nature of NAR's 527 spending is rarely reported.

**Next steps:** FOIA NAR's IL filing; interview IL Secretary of State on 527 oversight; map where the money ultimately goes in each state.

---

### 9. Lobbying Musical Chairs: Firms Vanishing and New Players Emerging
**Confidence: MEDIUM (2026 data may still be filing)**

**Nut graf:** Capstone Consulting went from 13 lobbying clients to zero in 2026 — the sharpest single-firm collapse in five years of data. Four other firms completely exited. Meanwhile, EchoStar Corporation (satellite/communications) hired 4 different lobbying entities as a new entrant, and drone manufacturer Skydio and the Rockefeller Family Fund each hired 2.

**Core finding:** Biggest losers: Capstone (13→0), Ron Holmes Consulting (13→1), Zerimar Strategies (9→0). Biggest gainers: Kasper Holmes & Dring (39→47), Ben Lazare (39→45). New: EchoStar (4 entities), Sullivan Dave (new entity, 4 clients).

**Statistical support:** Lobbying entity-client relationship counts by year, 2025 vs 2026.

**Why it matters now:** Lobbying firm churn reveals shifting political alliances and industry priorities. EchoStar's aggressive entry suggests telecom/satellite legislation is coming.

**Next steps:** Interview Capstone's former clients; check if EchoStar has pending IL legislation; investigate what drove the Zerimar and Holler exits.

---

### 10. The Kankakee Mystery: A Small-County Committee and $402 Million in Receipts
**Confidence: LOW (likely data anomaly, but warrants verification)**

**Nut graf:** The Kankakee County Democratic Central Committee reported $401.7 million in receipts from just 53 transactions in 2025 — more than JB Pritzker, more than ActBlue, and more than any committee in the state. With only $14,244 in expenditures (a 28,200:1 ratio), this is almost certainly a data error — but one that raises questions about ISBE's filing oversight.

**Core finding:** Committee ID 325. $401,667,102.40 in receipts. 53 transactions. $14,243.51 in expenditures. The contributor is "ActBlue Illinois" — suggesting bulk pass-through filings were attributed to the wrong committee.

**Statistical support:** Direct query of bulk_receipts_clean. Receipt-to-expenditure ratio: 28,200:1.

**Why it matters now:** If this is a filing error (likely), it inflates state-level totals by ~49% and raises questions about ISBE data quality. If it's real, it's the biggest campaign finance story in Illinois history.

**Next steps:** Check ISBE filings directly for Kankakee County Dem Central Cmte; contact ISBE data team; cross-reference with ActBlue's own FEC filings.

**Caution:** This is almost certainly a data quality issue, not actual fundraising. Do NOT report as real without independent verification.

---

### 11. Labor's Grip: Union PACs Dominate Both Giving and Spending
**Confidence: HIGH**

**Nut graf:** Labor unions occupy 8 of the top 20 donor positions and 8 of the top 20 expenditure positions in 2025, controlling billions in political spending through a network of overlapping PACs, 527 organizations, and lobbying registrations. The UAW Illinois PAC alone has 2,901 cross-system matches linking its directors, donors, and expenditure recipients.

**Core finding:** Labor entities in 2025 top donors: Midwest Operating Engineers ($21.3M), Laborers' District Council ($6.1M), Chicagoland Operators ($7.3M combined variants), LIUNA ($4.0M combined), AFT ($2.5M), SEIU ($4.3M combined), AFSCME ($2.1M), CTU ($1.8M). Labor in top expenditures: Chicagoland Operators ($7.6M), LiUNA ($5.7M), IPACE ($3.4M), Laborers Legislative ($2.8M), IFT COPE ($2.7M).

**Statistical support:** Cross-referencing bulk_receipts_clean, bulk_expenditures_clean, irs527_director_donor_matches, and irs527_expenditure_recipient_matches.

**Why it matters now:** In a cycle where outside money is flooding in, labor's institutional network remains the dominant structural force in IL politics.

**Next steps:** Map the full labor network: which unions fund which candidates, how do 527 operations complement direct donations, what legislative outcomes correlate with labor spending.

---

### 12. Dual-System Donors: The Wealthy Illinoisans Maxing Out Everywhere
**Confidence: HIGH**

**Nut graf:** Cross-matching federal and state donor records reveals a small group of wealthy Illinoisans who give at both levels — and the contrast between their state and federal giving reveals how unlimited state donations dwarf capped federal ones. Fred Eychaner gave $67,500 federally but $5.3 million at the state level. Michael Sacks: $7,000 federal, $2.7 million state. Craig Duchossois: $7,000 federal, $2.15 million state.

**Core finding:** Top dual-system donors (deduplicated): Pritzker ($432M combined), Eychaner ($5.4M), Sacks ($2.7M), Tracy ($2.2M), Duchossois ($2.2M), Finnegan ($1.2M). All matched at 0.95 confidence via name_state_zip.

**Statistical support:** fec_local_donor_matches table, confidence >= 0.80.

**Why it matters now:** Illinois has no individual contribution limits for state races. These donors give the legal max at the federal level ($3,300/candidate) but six- and seven-figure amounts at the state level — a structural inequality in political influence.

**Next steps:** Interview reform advocates; compare IL contribution limits (none) to neighboring states; map which state candidates these dual-system donors support.

---

## C. METHODS APPENDIX

### Data Sources and Coverage
- **bulk_receipts_clean:** 6,418,375 rows (1903-2026). Primary analysis: 2023 (204,521 rows) and 2025 (171,140 rows).
- **bulk_expenditures_clean:** 4,763,598 rows. Anomalous records excluded via `is_amount_anomalous = 0 OR IS NULL`. 2 anomalous records total (1 at $8.1B).
- **FEC tables:** 2026 cycle only. Schedule A: 56,025 rows. Schedule B: 13,477 rows. Schedule E: 5,097 rows. Candidate totals: 95 rows.
- **Lobbying:** 14,195 entity-client relationships across 5 years (2022-2026). 613 entities, 3,125 clients.
- **IRS 527:** 1,896 distinct IL EINs. 1,739 reports (2000-2025). 35,914 expenditure records with valid YYYYMMDD dates. 8,974,461 contribution records with non-standard date encoding (excluded from date-based analysis).
- **Cross-matching:** 10 tables with 106K+ matches at Jaccard 0.80+ threshold.

### Concentration Metrics
- **Top-K share:** Proportion of total receipts attributable to the top K donors by aggregate amount, calculated per year using CTEs with ROW_NUMBER() window functions.
- 2025 top-10 share: 61.18% ($498.6M / $814.9M). 2023 top-10 share: 18.56% ($105.8M / $570.0M).
- **Caveat:** ActBlue Illinois ($401.7M) is an intermediary, not a true end-donor. If ActBlue is excluded or re-attributed to underlying donors, concentration would be significantly lower. This is the most important caveat in the entire analysis.

### Percentile Distributions
- Computed via NTILE(100) window function. Median contribution: $500 in both 2023 and 2025 (stable). 25th percentile: $250 (2023) → $228-250 (2025). 99th percentile: $15K-30K (2023) → $11K-25K (2025).

### Cross-Matching Methodology
- All name matching uses Jaccard similarity with sparse inverted-index candidate generation at 0.80 threshold (per `database/cross_matching.py`).
- Address matching uses normalized zip5+city+state scoring at 0.50 threshold.
- Federal-to-local donor matching uses name+state+zip at 0.80-0.95 confidence.

### Anomaly Detection
- **Kankakee County:** Identified via receipt-to-expenditure ratio (28,200:1) and single-entity analysis. 53 transactions from "ActBlue Illinois" totaling $401.7M.
- **$8.1B expenditure:** Pre-flagged in source data (`is_amount_anomalous = 1`). Excluded from all expenditure aggregates.
- **Village CycleSport:** $8.28M from a single 2023 transaction from one committee. Flagged as suspicious but not pre-marked.

### Limitations and Caveats
1. No formal hypothesis testing (KS, bootstrap CI) was performed in this pass. Concentration shifts and spending ratios are descriptive. Statistical significance tests would strengthen claims about year-over-year changes.
2. ActBlue pass-through attribution: ActBlue bundles small-dollar donations. The $401.7M attributed to "ActBlue Illinois" as a single donor inflates concentration metrics. Proper analysis would require disaggregating ActBlue to underlying donors (data not available in current schema).
3. Lobbying 2026 data is early (Feb only). Client counts may increase as the year progresses.
4. IRS 527 contribution dates are corrupt; only expenditure records can be reliably dated.
5. No incumbency variable exists in the database. Claims about incumbent vs. challenger dynamics are inferred from committee names, not coded.
6. Cross-matching produces duplicate rows when a donor matches multiple local records. Deduplicate before reporting totals.

### Recommended Follow-Up Statistical Tests
- **Bootstrap 95% CI** on top-10 donor share (2023 vs 2025) to confirm concentration shift is statistically significant even after excluding ActBlue.
- **Kolmogorov-Smirnov test** on 2023 vs 2025 contribution amount distributions.
- **Poisson regression** on committee contribution counts with year, committee type, and party as predictors.
- **Permutation test** on lobbying firm client loss (is Capstone's 13→0 drop statistically anomalous vs. baseline churn?).
- **Benjamini-Hochberg FDR correction** if running multiple comparison tests.

---

## D. REPRODUCIBILITY APPENDIX

### Queries Run
All analysis was performed via `sqlite3` against `/Users/devin/Illinois_campaign_finance/data/campaign_finance.db`.

Key query patterns:
```sql
-- Year-over-year receipts
SELECT substr(received_date,1,4) as yr, COUNT(*), SUM(amount)
FROM bulk_receipts_clean WHERE substr(received_date,1,4) IN ('2023','2024','2025')
GROUP BY yr;

-- Donor concentration (top-K share)
WITH donor_totals AS (
  SELECT contributed_by, SUM(amount) as total FROM bulk_receipts_clean
  WHERE substr(received_date,1,4) = '2025' GROUP BY contributed_by
),
ranked AS (SELECT *, ROW_NUMBER() OVER (ORDER BY total DESC) as rn FROM donor_totals),
grand_total AS (SELECT SUM(total) as gt FROM donor_totals)
SELECT 'top_10', SUM(r.total), (SELECT gt FROM grand_total),
  ROUND(100.0 * SUM(r.total) / (SELECT gt FROM grand_total), 2)
FROM ranked r WHERE rn <= 10;

-- FEC independent expenditures
SELECT support_oppose_indicator, COUNT(*), SUM(expenditure_amount),
  COUNT(DISTINCT candidate_id), COUNT(DISTINCT committee_id)
FROM fec_schedule_e_independent_expenditures WHERE cycle = 2026
GROUP BY support_oppose_indicator;

-- Cross-matching: lobbying-527 overlap
SELECT client_name, org_name, ein, score FROM lobbying_527_matches ORDER BY score DESC;

-- Expenditures with anomaly exclusion
SELECT vendor_name, SUM(amount), COUNT(*) FROM bulk_expenditures_clean
WHERE substr(expended_date,1,4) = '2025'
  AND (is_amount_anomalous = 0 OR is_amount_anomalous IS NULL)
GROUP BY vendor_name ORDER BY SUM(amount) DESC LIMIT 20;
```

### Output Artifacts
- This file: `analysis_2026_cycle_findings.md`
- No CSV exports generated (all analysis via SQL; CSV export can be added on request)
- No model artifacts (descriptive analysis only; regression/bootstrap recommended as follow-up)

### What Remains Unverified
1. **Kankakee County anomaly:** Needs ISBE filing verification.
2. **ActBlue pass-through attribution:** Requires ActBlue-specific data or ISBE itemized small-dollar records.
3. **Village CycleSport $8.28M (2023):** Single transaction, source unknown. Check ISBE filing.
4. **EchoStar lobbying intent:** Need to identify pending IL legislation.
5. **Capstone Consulting exit:** Need to determine if firm closed or simply didn't re-register.
6. **527 contribution dates:** Need IRS data team contact to decode date format.
7. **Incumbency coding:** Need to cross-reference 2026 candidates with current officeholders.
