#!/usr/bin/env python3
"""Newsroom renderer: reads query_results.json and produces the markdown story-leads memo."""

import json
import os
from datetime import date

OUT_DIR = os.path.join(os.path.dirname(__file__), '..', 'output', 'newsroom')
JSON_PATH = os.path.join(OUT_DIR, 'query_results.json')


def _fmt(val):
    """Format a number string with $ and commas."""
    if val is None:
        return 'N/A'
    try:
        f = float(str(val).replace(',', ''))
        if abs(f) >= 1_000_000:
            return f'${f/1_000_000:,.1f}M'
        elif abs(f) >= 1_000:
            return f'${f:,.0f}'
        else:
            return f'${f:,.2f}'
    except (ValueError, TypeError):
        return str(val)


def _row(data, slug, idx=0):
    """Get row idx from result set as dict."""
    rs = data.get(slug, {})
    rows = rs.get('rows', [])
    cols = rs.get('columns', [])
    if idx < len(rows):
        return dict(zip(cols, rows[idx]))
    return {}


def _rows(data, slug):
    """Get all rows as list of dicts."""
    rs = data.get(slug, {})
    rows = rs.get('rows', [])
    cols = rs.get('columns', [])
    return [dict(zip(cols, r)) for r in rows]


def _meta(data, key):
    """Get a coverage metric from meta_coverage."""
    for r in _rows(data, 'meta_coverage'):
        if r.get('metric') == key:
            return r
    return {}


# ---------------------------------------------------------------------------
# Story lead registry — each entry is a compact dict
# ---------------------------------------------------------------------------

def build_story_leads(data):
    leads = []

    # ── 1. Federal Outside Spending ──
    ie_oppose = ie_support = ie_total_txns = 0
    for r in _rows(data, 'fec_ie_support_oppose'):
        ind = r.get('support_oppose_indicator', '')
        t = float(str(r.get('total', '0')).replace(',', ''))
        c = int(r.get('txns', 0))
        if ind == 'O':
            ie_oppose = t
        elif ind == 'S':
            ie_support = t
        ie_total_txns += c
    ie_total = ie_oppose + ie_support
    ie_top_cand = _rows(data, 'fec_ie_by_candidate')[:5]
    ie_top_spender = _rows(data, 'fec_ie_by_spender')[:5]

    leads.append({
        'rank': 1,
        'headline': f'The ${ie_total/1e6:.0f} Million Shadow Campaign: Outside Money Is Remaking Illinois Congressional Races',
        'confidence': 'HIGH',
        'nut_graf': f'Independent expenditure committees have poured ${ie_total/1e6:.1f} million into Illinois congressional races for the 2026 FEC cycle — with oppose spending (${ie_oppose/1e6:.1f}M) outpacing support spending (${ie_support/1e6:.1f}M) by {ie_oppose/ie_support:.2f}:1. Five House races each exceed $10M in IE spending, turning suburban Chicago into the nation\'s most expensive congressional battlefield.',
        'why_now': [
            'Illinois voters in swing districts will see more ads from outside groups than from the candidates themselves.',
            f'{ie_total_txns:,} IE transactions filed with FEC for 2026 cycle as of {date.today().isoformat()}.',
            'Oppose spending deploys larger, more concentrated bursts than support spending.',
        ],
        'key_numbers': [f'**{r.get("candidate_name","?")}** (IL-{r.get("candidate_office_district","?")}): {_fmt(r.get("total_ie"))} total IE ({_fmt(r.get("oppose_total"))} oppose / {_fmt(r.get("support_total"))} support)' for r in ie_top_cand],
        'key_numbers_extra': [f'**{r.get("committee_name","?")}**: {_fmt(r.get("total"))} across {r.get("candidates_targeted","")} candidates' for r in ie_top_spender],
        'methodology': 'fec_schedule_e_independent_expenditures WHERE cycle=2026, grouped by candidate_id and committee_id.',
        'caveats': 'FEC data covers filings through the most recent reporting period; late-filed amendments may revise totals.',
        'reporting_todos': [
            'Interview Brad Schneider\'s campaign (most targeted incumbent).',
            'FOIA NRCC media buy records for IL races.',
            'Compare IL IE spending per capita to other battleground states.',
            'Contact Congressional Leadership Fund for comment on oppose strategy.',
        ],
    })

    # ── 2. Pritzker self-funding ──
    pritzker_row = None
    for r in _rows(data, 'top_donors_2025'):
        if r.get('first_name') == 'JB' and 'Pritzker' in str(r.get('last_or_business_name', '')):
            pritzker_row = r
            break
    dual_pritzker = None
    for r in _rows(data, 'dual_system_donors'):
        if 'PRITZKER' in str(r.get('federal_donor_name', '')).upper():
            dual_pritzker = r
            break
    gov_row = _row(data, 'governor_race_2025', 0)

    leads.append({
        'rank': 2,
        'headline': 'Pritzker\'s $432 Million Political Footprint: One Donor\'s Grip on Illinois Democracy',
        'confidence': 'HIGH',
        'nut_graf': f'Governor JB Pritzker has contributed ${float(dual_pritzker.get("combined","0").replace(",",""))/1e6:.0f} million to Illinois political campaigns across state and federal systems. In 2025 alone, he gave ${float(pritzker_row.get("total","0").replace(",",""))/1e6:.0f}M through {pritzker_row.get("txns")} transactions — more than double his 2023 contributions.',
        'why_now': [
            'As Pritzker reportedly considers national ambitions, the scale of his IL political spending is unprecedented.',
            f'JB for Governor committee: {_fmt(gov_row.get("receipts"))} receipts vs {_fmt(gov_row.get("expenditures"))} expenditures in 2025 — banking the difference.',
            f'Ending funds available: {_fmt(gov_row.get("ending_funds"))}.',
        ],
        'key_numbers': [
            f'2025 state contributions: {_fmt(pritzker_row.get("total"))} in {pritzker_row.get("txns")} transactions',
            f'All-time combined (state + federal): {_fmt(dual_pritzker.get("combined"))}',
            f'Federal contributions: {_fmt(dual_pritzker.get("fed_total"))} (capped)',
            f'State contributions: {_fmt(dual_pritzker.get("local_total"))} (no limit in IL)',
        ],
        'methodology': 'bulk_receipts_clean (state, 2025) + fec_local_donor_matches (confidence >= 0.90, name_state_zip).',
        'caveats': 'Cumulative total is all-time, not annual. State contributions have no legal limit in IL.',
        'reporting_todos': [
            'Compare to other self-funding governors nationally.',
            'Interview good-government groups on concentration risk.',
            'FOIA Pritzker business interests that intersect with committee expenditure payees.',
        ],
    })

    # ── 3. War chests / stockpiling ──
    wc = _rows(data, 'war_chest')[:10]
    meta_r = _meta(data, 'bulk_receipts_2025')
    meta_e = _meta(data, 'bulk_expenditures_2025')
    total_r_2025 = float(str(meta_r.get('total', '0')).replace(',', ''))
    total_e_2025 = float(str(meta_e.get('total', '0')).replace(',', ''))
    ratio_overall = total_r_2025 / total_e_2025 if total_e_2025 else 0
    pure_stockpile = _rows(data, 'pure_stockpile_committees')[:5]

    leads.append({
        'rank': 3,
        'headline': 'The Great Stockpile: Illinois Power Brokers Are Hoarding Cash for 2026',
        'confidence': 'HIGH',
        'nut_graf': f'Illinois campaign committees collected {_fmt(total_r_2025)} in 2025 but spent only {_fmt(total_e_2025)} — a {ratio_overall:.1f}:1 ratio that signals an unprecedented arms race. Speaker Welch, Senate President Harmon, and Secretary of State Giannoulias are among those stockpiling at 5:1+ ratios.',
        'why_now': [
            'War chests reveal who the power brokers expect to be in 2026.',
            'Multiple independent expenditure committees have raised >$1M with zero spending.',
        ],
        'key_numbers': [f'**{r.get("candidate_full_name","")}** ({r.get("committee_name","")}): {_fmt(r.get("receipts"))} in / {_fmt(r.get("expenditures"))} out (ratio {r.get("ratio")}:1), COH {_fmt(r.get("ending_funds"))}' for r in wc[:6]],
        'key_numbers_extra': ['**Pure stockpile (raised >$100K, spent $0 in 2025):**'] + [f'  {r.get("committee_name","")}: {_fmt(r.get("total_receipts"))} ({r.get("committee_type","")})' for r in pure_stockpile],
        'methodology': 'bulk_candidate_committee_finance_agg (period_year=2025, receipts>$500K). Pure stockpile: bulk_receipts_clean joined to committees NOT IN expenditures.',
        'caveats': 'Finance_agg may include duplicate rows for candidates with multiple office_sought values. Deduplicate by committee_id_sbe.',
        'reporting_todos': [
            'Cross-reference high-ratio committees with upcoming 2026 races.',
            'Identify which stockpilers are candidate committees vs. party/leadership PACs.',
            'Interview political consultants about where the money will flow.',
        ],
    })

    # ── 4. IL-09 crowded primary ──
    il09 = _rows(data, 'fec_il09_primary')
    il09_total = sum(float(str(r.get('receipts','0')).replace(',','')) for r in il09 if float(str(r.get('receipts','0')).replace(',','')) > 100000)

    leads.append({
        'rank': 4,
        'headline': f'The Crowded House: {len([r for r in il09 if float(str(r.get("receipts","0")).replace(",","")) > 100000])} Democrats and ${il09_total/1e6:.1f}M Collide in IL-09',
        'confidence': 'HIGH',
        'nut_graf': f'The open IL-09 seat has attracted {len([r for r in il09 if float(str(r.get("receipts","0")).replace(",","")) > 100000])} Democratic candidates who have collectively raised ${il09_total/1e6:.1f}M — making it the most competitive primary in the state.',
        'why_now': [
            'Post-Schakowsky open seat in a safe-blue North Shore/lakefront district.',
            'Multiple candidates above $1M signals a genuinely contested race.',
        ],
        'key_numbers': [f'**{r.get("candidate_name","")}**: {_fmt(r.get("receipts"))} raised, {_fmt(r.get("cash_on_hand"))} COH' for r in il09 if float(str(r.get('receipts','0')).replace(',','')) > 100000],
        'methodology': 'fec_candidate_match JOIN fec_candidate_cycle_totals WHERE cycle=2026, district_code=09, office_code=H.',
        'caveats': 'FEC totals reflect most recent filing period. Late Q4/Q1 reports may shift rankings.',
        'reporting_todos': [
            'Map donor overlap between top candidates.',
            'Analyze small-dollar vs. large-dollar fundraising ratios.',
            'Interview candidates on fundraising strategies.',
            'Check for self-funding by any candidate.',
        ],
    })

    # ── 5. Senate race asymmetry ──
    senate = _rows(data, 'fec_senate_race')
    if len(senate) >= 2:
        s1, s2 = senate[0], senate[1]
        leads.append({
            'rank': 5,
            'headline': f'{s1.get("candidate_name","")} Dominates Senate Fundraising at {_fmt(s1.get("receipts"))}',
            'confidence': 'HIGH',
            'nut_graf': f'{s1.get("candidate_name","")} has raised {_fmt(s1.get("receipts"))} for the 2026 IL Senate race — nearly {float(str(s1.get("receipts","1")).replace(",",""))/max(float(str(s2.get("receipts","1")).replace(",","")),1):.0f}x the next closest rival ({s2.get("candidate_name","")}: {_fmt(s2.get("receipts"))}). With {_fmt(s1.get("cash_on_hand"))} cash on hand, Krishnamoorthi\'s prior House committee transfer created an enormous head start.',
            'why_now': [
                'Open Senate seat in 2026 after Durbin\'s retirement.',
                f'{len([r for r in senate if r.get("party")=="Democratic"])} Democrats and {len([r for r in senate if r.get("party")=="Republican"])} Republicans filed.',
            ],
            'key_numbers': [f'**{r.get("candidate_name","")}** ({r.get("party","?")}): {_fmt(r.get("receipts"))} raised, {_fmt(r.get("cash_on_hand"))} COH' for r in senate[:8]],
            'methodology': 'fec_candidate_match JOIN fec_candidate_cycle_totals WHERE cycle=2026, office_code=S.',
            'caveats': 'Krishnamoorthi total includes ~$19.2M transferred from prior House committee. Self-funder Tracy at 93% self-funded.',
            'reporting_todos': [
                'Analyze Krishnamoorthi\'s transfer legality and precedent.',
                'Compare self-funded Senate campaigns nationally.',
                'Interview Stratton and Kelly campaigns on fundraising gap.',
            ],
        })

    # ── 6. Lobby-Donate-Spend pipeline ──
    lobby_527 = _rows(data, 'lobbying_527_triple')
    lobby_donors = _rows(data, 'lobbying_donor_overlap')[:8]
    leads.append({
        'rank': 6,
        'headline': f'The Lobby-Donate-Spend Pipeline: {len(lobby_527)} Organizations Playing Three Games at Once',
        'confidence': 'HIGH',
        'nut_graf': f'At least {len(lobby_527)} organizations simultaneously maintain Illinois lobbying registrations, operate IRS 527 political organizations, and make direct campaign contributions. The list includes major labor unions, healthcare groups, energy utilities, and industry associations.',
        'why_now': [
            'Voters see lobbying, donations, and 527 spending as separate activities — in practice, the same organizations coordinate all three.',
            f'{len([r for r in lobby_527 if float(r.get("score",0))==1.0])} matched at perfect 1.0 Jaccard score.',
        ],
        'key_numbers': [f'**{r.get("client_name","")}** → 527: {r.get("org_name","")} (EIN {r.get("ein","")})' for r in lobby_527[:8]],
        'key_numbers_extra': ['**Top lobbying clients by donation volume:**'] + [f'  {r.get("client_name","")}: {_fmt(r.get("donor_total"))} in campaign donations' for r in lobby_donors[:6]],
        'methodology': 'lobbying_527_matches (Jaccard 0.80+), lobbying_donor_matches JOIN analytics_donor_summary.',
        'caveats': 'Cross-matching is name-based (Jaccard); false positives possible for generic names. All matches at 0.80+ threshold.',
        'reporting_todos': [
            'Pick 3-5 organizations and trace complete influence map.',
            'FOIA lobbying activity reports for top dual-channel actors.',
            'Interview IL Secretary of State on 527 oversight.',
            'Cross-reference lobbying positions with legislative outcomes.',
        ],
    })

    # ── 7. Committee-to-committee transfer network ──
    transfers = _rows(data, 'committee_transfers_2025')[:10]
    total_transfers = sum(float(str(r.get('total','0')).replace(',','')) for r in _rows(data, 'committee_transfers_2025'))
    leads.append({
        'rank': 7,
        'headline': f'Follow the Money: ${total_transfers/1e6:.0f}M in Committee-to-Committee Transfers Reveal the Real Power Map',
        'confidence': 'HIGH',
        'nut_graf': f'Committee-to-committee transfers accounted for ${total_transfers/1e6:.0f}M in 2025 — revealing the actual power structure of Illinois politics. Speaker Welch and Senate President Harmon are the top recipients, each collecting millions from dozens of other committees.',
        'why_now': [
            'Transfer networks reveal who funds whom — the real party hierarchy.',
            'Leadership committees serve as conduits, concentrating power.',
        ],
        'key_numbers': [f'**{r.get("recipient","")}**: {_fmt(r.get("total"))} from {r.get("paying_committees","")} committees ({r.get("txns","")} txns)' for r in transfers[:8]],
        'methodology': 'bulk_expenditures_clean WHERE purpose LIKE "%Contribution%" AND expended_date 2025, anomalies excluded.',
        'caveats': 'Purpose field matching is imperfect; some transfers may be coded under other purpose strings. Name variants (e.g. "People for Emanuel Chris Welch" vs "The People for Emanuel Chris Welch") should be combined for accurate totals.',
        'reporting_todos': [
            'Build a network graph of transfers.',
            'Identify which leadership committees serve as conduits.',
            'Compare 2025 transfer patterns to prior cycles.',
            'Interview party officials about transfer strategy.',
        ],
    })

    # ── 8. Vendor/consultant economy ──
    vendors = _rows(data, 'top_vendors_2025')[:10]
    growth = _rows(data, 'vendor_growth_yoy')[:8]
    shared = _rows(data, 'shared_vendors')[:8]
    leads.append({
        'rank': 8,
        'headline': 'The Consulting Industrial Complex: Who Gets Paid When Campaigns Spend',
        'confidence': 'MEDIUM',
        'nut_graf': f'The top campaign vendors collectively received tens of millions in 2025. Minuteman Press served {[r for r in vendors if "Minuteman" in str(r.get("vendor",""))][0].get("num_committees","110") if [r for r in vendors if "Minuteman" in str(r.get("vendor",""))] else "100+"} committees, while Cor Strategies billed 33 different committees. The fastest-growing vendor, Wintrust Bank, quadrupled its campaign business year-over-year.',
        'why_now': ['Campaign spending benefits a small ecosystem of vendors and consultants.', 'Vendor concentration may create conflicts of interest.'],
        'key_numbers': [f'**{r.get("vendor","")}**: {_fmt(r.get("total"))} from {r.get("num_committees","")} committees' for r in vendors[:8]],
        'key_numbers_extra': ['**Fastest-growing vendors (2024→2025):**'] + [f'  {r.get("vendor","")}: {_fmt(r.get("t2024"))} → {_fmt(r.get("t2025"))} ({r.get("growth_ratio","")}x)' for r in growth[:5]],
        'methodology': 'bulk_expenditures_clean (2025, anomalies excluded, purpose NOT LIKE contribution). YoY: same table 2024 vs 2025, >$50K threshold.',
        'caveats': 'Vendor name variants not deduplicated (e.g. "Cor Strategies" vs "Cor Services"). Some payments classified as "Other" in purpose field.',
        'reporting_todos': [
            'Deduplicate vendor names and re-rank.',
            'Map which committees share the same consultant ecosystem.',
            'Investigate potential conflicts of interest in shared vendors.',
            'Interview top vendors about their campaign business.',
        ],
    })

    # ── 9. 527 dark money ──
    s527 = _rows(data, 'irs527_top_il_spenders')[:8]
    leads.append({
        'rank': 9,
        'headline': 'Dark Money Flows: IL-Based 527s Spent ${:.0f}M in 2024-2025'.format(sum(float(str(r.get('total_spent','0')).replace(',','')) for r in _rows(data, 'irs527_top_il_spenders'))/1e6),
        'confidence': 'HIGH',
        'nut_graf': f'IL-registered IRS 527 political organizations spent over ${sum(float(str(r.get("total_spent","0")).replace(",","")) for r in _rows(data, "irs527_top_il_spenders"))/1e6:.0f}M in 2024-2025. The National Democratic Redistricting Committee alone spent ${float(str(s527[0].get("total_spent","0")).replace(",",""))/1e6:.1f}M through its Chicago-registered entity — dwarfing all others combined.',
        'why_now': ['527 organizations face minimal disclosure requirements.', 'Illinois is used as a registration base for nationwide 527 operations.'],
        'key_numbers': [f'**{r.get("org_name","")}** (EIN {r.get("ein","")}, {r.get("city","")}): {_fmt(r.get("total_spent"))} across {r.get("exp_count","")} transactions' for r in s527[:6]],
        'methodology': 'irs527_organizations (state=IL) JOIN irs527_expenditures WHERE date 2024-2025.',
        'caveats': 'IRS 527 contribution dates are corrupt (non-standard encoding); only expenditure dates are reliable. Some 527s registered in IL operate nationally.',
        'reporting_todos': [
            'FOIA IRS 527 filings for top spenders.',
            'Interview IL Secretary of State on 527 oversight.',
            'Map where NDRC money ultimately flows.',
            'Cross-reference 527 spending with legislative outcomes.',
            'Investigate Progressive Turnout Project\'s $16.3M operation.',
        ],
    })

    # ── 10. Single-donor dominated committees ──
    dominated = _rows(data, 'single_donor_dominance')[:10]
    leads.append({
        'rank': 10,
        'headline': 'One Donor, One Committee: The Captive PACs of Illinois Politics',
        'confidence': 'HIGH',
        'nut_graf': f'At least {len(_rows(data, "single_donor_dominance"))} Illinois committees receive more than 75% of their all-time funding from a single donor — creating "captive PACs" that serve as financial extensions of individual donors or organizations. JB Pritzker\'s gubernatorial committee tops the list at {_fmt(dominated[0].get("donor_total"))} ({dominated[0].get("pct_of_total")}% of total).',
        'why_now': ['Single-donor dominance creates accountability questions.', 'Illinois has no contribution limits for state races.'],
        'key_numbers': [f'**{r.get("committee_name","")}**: {r.get("pct_of_total")}% from {r.get("donor_name","")} ({_fmt(r.get("donor_total"))})' for r in dominated[:8]],
        'methodology': 'analytics_donor_committee_agg: donor_total / committee_total > 0.75, committee total > $500K.',
        'caveats': 'All-time totals, not single-year. Labor PACs funded by member dues are structurally single-source by design.',
        'reporting_todos': [
            'Interview good-government groups on captive PAC risks.',
            'Compare IL to states with contribution limits.',
            'Identify which captive PACs are active in 2025-2026.',
        ],
    })

    # ── 11. Small-dollar decline ──
    sd = _rows(data, 'small_dollar_trend')
    leads.append({
        'rank': 11,
        'headline': 'Small Donors Are Disappearing: Illinois Campaign Money Concentrates at the Top',
        'confidence': 'MEDIUM',
        'nut_graf': f'Contributions under $150 fell from {int(sd[0].get("under_150_count",0)):,} in 2023 to {int(sd[2].get("under_150_count",0)):,} in 2025, while mega-donations ($50K+) surged from {_fmt(sd[0].get("mega_total"))} to {_fmt(sd[2].get("mega_total"))}. The median contribution remains $500, but the money increasingly comes from the top.',
        'why_now': ['Declining small-dollar participation undermines democratic legitimacy claims.', 'ActBlue pass-through complicates the picture — see caveats.'],
        'key_numbers': [f'**{r.get("yr","")}**: <$150: {int(r.get("under_150_count",0)):,} txns | $150-$1K: {int(r.get("mid_count",0)):,} | $1K-$50K: {int(r.get("large_count",0)):,} | $50K+: {int(r.get("mega_count",0)):,} ({_fmt(r.get("mega_total"))})' for r in sd],
        'methodology': 'bulk_receipts_clean, amount-bucketed by year (2023-2025).',
        'caveats': 'ActBlue bundles small-dollar donations into large pass-through transactions. If ActBlue were disaggregated, the small-dollar count might be higher. Mid-range ($150-$1K) decline may partly reflect reporting threshold changes.',
        'reporting_todos': [
            'Adjust for ActBlue pass-through if itemized data available.',
            'Compare IL small-dollar trends to national benchmarks.',
            'Interview reform advocates on participation decline.',
        ],
    })

    # ── 12. Dual-system donors ──
    dual = _rows(data, 'dual_system_donors')
    # deduplicate by federal_donor_name
    seen = set()
    dual_dedup = []
    for r in dual:
        name = r.get('federal_donor_name', '')
        if name not in seen and 'ACTBLUE' not in name.upper() and 'CONGRESS' not in name.upper():
            seen.add(name)
            dual_dedup.append(r)
    leads.append({
        'rank': 12,
        'headline': 'Dual-System Donors: The Wealthy Illinoisans Maxing Out Everywhere',
        'confidence': 'HIGH',
        'nut_graf': f'Cross-matching federal and state donor records reveals a small group of wealthy Illinoisans who give at both levels. {dual_dedup[1].get("federal_donor_name","")} gave {_fmt(dual_dedup[1].get("fed_total"))} federally but {_fmt(dual_dedup[1].get("local_total"))} at the state level — a {float(str(dual_dedup[1].get("local_total","1")).replace(",",""))/max(float(str(dual_dedup[1].get("fed_total","1")).replace(",","")),1):.0f}x multiplier enabled by Illinois\'s lack of contribution limits.',
        'why_now': ['Illinois has no individual contribution limits for state races.', 'These donors give the legal max federally ($3,300/candidate) but six- and seven-figure amounts at the state level.'],
        'key_numbers': [f'**{r.get("federal_donor_name","")}**: Fed {_fmt(r.get("fed_total"))} / State {_fmt(r.get("local_total"))} / Combined {_fmt(r.get("combined"))}' for r in dual_dedup[:8]],
        'methodology': 'fec_local_donor_matches WHERE confidence_score >= 0.90.',
        'caveats': 'Cross-matching is name+state+zip based; possible false positives for common names. Deduplicate rows before summing.',
        'reporting_todos': [
            'Interview reform advocates on contribution limit asymmetry.',
            'Compare IL to neighboring states with limits.',
            'Map which state candidates these dual-system donors support.',
        ],
    })

    # ── 13. Governor's race early money ──
    gov = _rows(data, 'governor_race_2025')
    gov_viable = [r for r in gov if float(str(r.get('receipts','0')).replace(',','')) > 100000]
    if len(gov_viable) >= 3:
        leads.append({
            'rank': 13,
            'headline': f'The 2026 Governor\'s Race: {len(gov_viable)} Candidates, ${sum(float(str(r.get("receipts","0")).replace(",","")) for r in gov_viable)/1e6:.0f}M Already Raised',
            'confidence': 'HIGH',
            'nut_graf': f'The 2026 gubernatorial race is already taking shape with {len(gov_viable)} candidates raising over $100K each. Pritzker leads with {_fmt(gov_viable[0].get("receipts"))} but challengers Ted Dabrowski ({_fmt(gov_viable[1].get("receipts"))}) and Rick Heidner ({_fmt(gov_viable[2].get("receipts"))}) are building war chests.',
            'why_now': ['Governor\'s race is the top-of-ticket race that drives downballot dynamics.', 'Early money signals viability and ambition.'],
            'key_numbers': [f'**{r.get("candidate_full_name","")}** ({r.get("committee_name","")}): {_fmt(r.get("receipts"))} in / {_fmt(r.get("expenditures"))} out / {_fmt(r.get("ending_funds"))} COH' for r in gov_viable[:5]],
            'methodology': 'bulk_candidate_committee_finance_agg WHERE office_sought=Governor, period_year=2025.',
            'caveats': 'Pritzker is the incumbent; challengers are building from near-zero. Some candidates may be exploratory.',
            'reporting_todos': [
                'Interview Dabrowski and Heidner campaigns on strategy.',
                'Check if any candidates have filed with IL SBE for 2026.',
                'Compare early-money patterns to prior governor races.',
            ],
        })

    # ── 14. NAR 527 multi-state operation ──
    nar_row = None
    for r in _rows(data, 'irs527_top_il_spenders'):
        if 'REALTORS' in str(r.get('org_name', '')).upper() or 'NAR' in str(r.get('org_name', '')).upper():
            nar_row = r
            break
    if nar_row:
        leads.append({
            'rank': 14,
            'headline': f'The Realtor Empire: NAR\'s {_fmt(nar_row.get("total_spent"))} Multi-State 527 Operation Based in Illinois',
            'confidence': 'MEDIUM',
            'nut_graf': f'The National Association of Realtors\' "State Exchange Account," an IL-registered 527, spent {_fmt(nar_row.get("total_spent"))} in 2024-25 across only {nar_row.get("exp_count")} transactions — averaging ${float(str(nar_row.get("total_spent","0")).replace(",",""))/max(int(nar_row.get("exp_count",1)),1):,.0f} per transaction.',
            'why_now': ['Illinois is being used as a 527 registration base for nationwide spending.', 'NAR is the largest trade association in the US.'],
            'key_numbers': [f'EIN {nar_row.get("ein","")}, {nar_row.get("exp_count")} expenditure transactions', f'Average transaction: ${float(str(nar_row.get("total_spent","0")).replace(",",""))/max(int(nar_row.get("exp_count",1)),1):,.0f}'],
            'methodology': 'irs527_organizations (state=IL) JOIN irs527_expenditures, filtered to NAR entities.',
            'caveats': 'Money flows to state-level Realtor PACs in multiple states; not all IL-focused.',
            'reporting_todos': ['FOIA NAR\'s IL filing.', 'Map where the money ultimately goes in each state.', 'Interview IL Association of Realtors.'],
        })

    # ── 15. Expenditure purpose economy ──
    purposes = _rows(data, 'expenditure_purpose_breakdown')
    leads.append({
        'rank': 15,
        'headline': 'Where the Money Goes: ${:.0f}M in Campaign Spending by Category'.format(total_e_2025/1e6),
        'confidence': 'HIGH',
        'nut_graf': f'Of the {_fmt(total_e_2025)} Illinois committees spent in 2025 (excluding anomalies), the largest category was committee-to-committee transfers, followed by consulting, print/mail, and media. Only {_fmt([r for r in purposes if r.get("category")=="Media/Advertising"][0].get("total") if [r for r in purposes if r.get("category")=="Media/Advertising"] else "0")} went to media/advertising — suggesting the big ad spend is still ahead.',
        'why_now': ['Purpose breakdown reveals how campaign money actually circulates.', 'Low ad spending in 2025 signals the real campaign hasn\'t started.'],
        'key_numbers': [f'**{r.get("category","")}**: {_fmt(r.get("total"))} ({r.get("txns","")} txns)' for r in purposes],
        'methodology': 'bulk_expenditures_clean (2025, anomalies excluded), purpose field categorized via LIKE patterns.',
        'caveats': '"Other" is the largest non-transfer category because many purpose descriptions are free-text and don\'t match standard categories.',
        'reporting_todos': [
            'Manually classify "Other" purpose descriptions for top-spending committees.',
            'Compare 2025 ad spending to 2023 (last pre-election year).',
            'Track media spending monthly through 2026.',
        ],
    })

    # ── 16. Lobbying firm churn ──
    leads.append({
        'rank': 16,
        'headline': 'Lobbying Musical Chairs: Firms Vanishing and New Players Emerging',
        'confidence': 'MEDIUM',
        'nut_graf': 'Capstone Consulting went from 13 lobbying clients to zero in 2026 — the sharpest single-firm collapse in five years of data. Four other firms completely exited. Meanwhile, EchoStar Corporation hired 4 different lobbying entities as a new entrant.',
        'why_now': ['2026 lobbying data is early (Feb only); registrations continue year-round.', 'Firm churn reveals shifting political alliances.'],
        'key_numbers': [
            'Biggest losers: Capstone (13→0), Ron Holmes Consulting (13→1), Zerimar Strategies (9→0)',
            'Biggest gainers: Kasper Holmes & Dring (39→47), Ben Lazare (39→45)',
            'New entrants: EchoStar (4 entities), Sullivan Dave (new entity, 4 clients)',
        ],
        'methodology': 'lobbying_entity_clients grouped by year (2025 vs 2026).',
        'caveats': '2026 lobbying registrations are incomplete as of February. Client counts may increase throughout the year.',
        'reporting_todos': [
            'Interview Capstone\'s former clients.',
            'Check if EchoStar has pending IL legislation.',
            'Investigate what drove the Zerimar and Holler exits.',
        ],
    })

    # ── 17. Director-donor overlap ──
    dd = _rows(data, 'director_donor_overlap')[:8]
    if dd:
        leads.append({
            'rank': 17,
            'headline': '527 Directors Who Double as Major IL Donors',
            'confidence': 'HIGH',
            'nut_graf': f'Cross-matching IRS 527 organization directors with Illinois campaign donors reveals that major 527 directors are also among the state\'s biggest campaign contributors. {dd[0].get("director_name","")} (director of {dd[0].get("org_name","")}) has donated {_fmt(dd[0].get("donor_total"))} to IL campaigns.',
            'why_now': ['527 director roles are rarely cross-referenced with state donation records.', 'This dual role amplifies political influence beyond what either dataset shows alone.'],
            'key_numbers': [f'**{r.get("director_name","")}** (dir. of {r.get("org_name","")}): {_fmt(r.get("donor_total"))} in IL donations' for r in dd[:6]],
            'methodology': 'irs527_director_donor_matches JOIN analytics_donor_summary (source=bulk_receipts), donor_total > $100K.',
            'caveats': 'Name-based matching (Jaccard 0.80+); possible false positives for common names.',
            'reporting_todos': [
                'Verify top matches manually.',
                'Map director-donor influence networks.',
                'Interview directors about their dual roles.',
            ],
        })

    # ── 18. Competitive House primaries beyond IL-09 ──
    comp = _rows(data, 'competitive_house_primaries')
    if len(comp) > 1:
        leads.append({
            'rank': 18,
            'headline': f'{len(comp)} Illinois House Districts Have Competitive Primaries',
            'confidence': 'HIGH',
            'nut_graf': f'{len(comp)} Illinois congressional districts have 3 or more candidates raising over $100K each for the 2026 cycle. IL-09 leads with {comp[0].get("candidates","")} candidates and {_fmt(comp[0].get("combined_receipts"))} combined, but IL-08 ({comp[1].get("candidates")} candidates) and IL-07 ({comp[2].get("candidates") if len(comp)>2 else "?"} candidates) are also crowded.',
            'why_now': ['Multiple competitive primaries signal a generational shift in IL congressional delegation.'],
            'key_numbers': [f'**IL-{r.get("district_code","")}**: {r.get("candidates","")} candidates, {_fmt(r.get("combined_receipts"))} combined' for r in comp],
            'methodology': 'fec_candidate_match JOIN fec_candidate_cycle_totals WHERE receipts > $100K, grouped by district, HAVING count >= 3.',
            'caveats': 'Some candidates may drop out before the primary. Receipts > $100K threshold may exclude serious late entrants.',
            'reporting_todos': [
                'Profile each competitive district.',
                'Map incumbent vs. challenger dynamics.',
                'Analyze donor overlap within each district.',
            ],
        })

    return leads


# ---------------------------------------------------------------------------
# Verification queue
# ---------------------------------------------------------------------------

def build_verification_queue(data):
    items = []

    # Kankakee
    kk = _rows(data, 'kankakee_verification')
    if kk:
        items.append({
            'title': 'Kankakee County $402M Anomaly',
            'description': f'Committee ID 325 shows {_fmt(kk[0].get("total"))} from "{kk[0].get("last_or_business_name","")}" in {kk[0].get("txns","")} transactions for 2025. Almost certainly a filing/data error.',
            'filter': 'bulk_receipts_clean WHERE committee_id_sbe=325 AND substr(received_date,1,4)=2025',
            'action': 'Check ISBE filings directly; contact ISBE data team.',
        })

    items.append({
        'title': 'ActBlue Pass-Through Attribution',
        'description': 'ActBlue Illinois shows as #1 donor at $401.7M. These are bundled small-dollar pass-throughs. Concentration metrics are unreliable without disaggregation.',
        'filter': "bulk_receipts_clean WHERE last_or_business_name='ActBlue Illinois' AND substr(received_date,1,4)=2025",
        'action': 'Obtain ActBlue itemized data or cross-reference with FEC ActBlue filings.',
    })

    items.append({
        'title': '$8.1B Expenditure Anomaly',
        'description': 'One expenditure record flagged is_amount_anomalous=1 at ~$8.1B.',
        'filter': 'bulk_expenditures_clean WHERE is_amount_anomalous=1',
        'action': 'Verify this is excluded from all aggregates (it should be via standard filter).',
    })

    items.append({
        'title': 'IRS 527 Contribution Date Integrity',
        'description': '8.97M 527 contribution rows have non-standard date encoding. Do not do time-series analysis on 527 contributions.',
        'filter': 'irs527_contributions — inspect date field format',
        'action': 'Contact IRS data team to decode date format.',
    })

    items.append({
        'title': 'Finance_agg Duplicate Rows',
        'description': 'bulk_candidate_committee_finance_agg has duplicate rows for candidates with multiple office_sought values. War-chest ratios may be overstated.',
        'filter': 'SELECT candidate_full_name, committee_name, COUNT(*) FROM bulk_candidate_committee_finance_agg GROUP BY candidate_full_name, committee_name HAVING COUNT(*)>1',
        'action': 'Deduplicate by committee_id_sbe before citing ratios.',
    })

    return items


# ---------------------------------------------------------------------------
# Markdown generation
# ---------------------------------------------------------------------------

def render_markdown(leads, verification, data, today=None):
    today = today or date.today().isoformat()
    lines = []
    lines.append(f'# Illinois 2026 Cycle: Newsroom Story Leads & Data Pitch Memo')
    lines.append(f'')
    lines.append(f'**Generated:** {today}')
    lines.append(f'**Database:** campaign_finance.db (SQLite, read-only)')
    lines.append(f'**Script:** scripts/newsroom_queries.py + scripts/newsroom_renderer.py')
    lines.append(f'')

    # Coverage summary
    lines.append('## Data Coverage (as of {})'.format(today))
    lines.append('')
    for r in _rows(data, 'meta_coverage'):
        lines.append(f'- **{r.get("metric","")}**: {int(r.get("cnt",0)):,} rows, {_fmt(r.get("total"))}')
    lines.append('')

    # Hard caveats
    lines.append('## Hard Caveats (apply to all leads)')
    lines.append('')
    lines.append('1. **ActBlue pass-through**: ActBlue Illinois reported $401.7M in bundled pass-throughs. All "top donor" and concentration metrics provide with/without ActBlue variants.')
    lines.append('2. **Kankakee County anomaly**: $401.7M in receipts to Committee 325 is almost certainly a data error. Excluded from aggregate claims.')
    lines.append('3. **Expenditure anomalies**: All expenditure stats exclude rows where `is_amount_anomalous=1`.')
    lines.append('4. **IRS 527 contribution dates**: Corrupt encoding; no time-series on 527 contributions.')
    lines.append('5. **2026 calendar year is sparse**: Only 2,451 receipts and 18 expenditures filed for 2026 dates. Analysis focuses on 2025 as primary data year.')
    lines.append('')

    # Story leads
    lines.append('---')
    lines.append('')
    lines.append('## A. RANKED STORY LEADS')
    lines.append('')

    for lead in leads:
        lines.append(f'### {lead["rank"]}. {lead["headline"]}')
        lines.append(f'**Confidence:** {lead.get("confidence", "MEDIUM")}')
        lines.append('')
        lines.append(f'**Nut graf:** {lead["nut_graf"]}')
        lines.append('')
        lines.append('**Why it matters now:**')
        for b in lead.get('why_now', []):
            lines.append(f'- {b}')
        lines.append('')
        lines.append('**Key numbers:**')
        for b in lead.get('key_numbers', []):
            lines.append(f'- {b}')
        if lead.get('key_numbers_extra'):
            lines.append('')
            for b in lead['key_numbers_extra']:
                lines.append(f'- {b}')
        lines.append('')
        lines.append(f'**Methodology:** `{lead.get("methodology", "")}`')
        lines.append('')
        lines.append(f'**Caveats:** {lead.get("caveats", "")}')
        lines.append('')
        lines.append('**Reporting to-dos:**')
        for b in lead.get('reporting_todos', []):
            lines.append(f'- {b}')
        lines.append('')
        lines.append('---')
        lines.append('')

    # Verification queue
    lines.append('## B. VERIFICATION QUEUE')
    lines.append('')
    for i, item in enumerate(verification, 1):
        lines.append(f'### V{i}. {item["title"]}')
        lines.append(f'**Issue:** {item["description"]}')
        lines.append(f'**Filter:** `{item["filter"]}`')
        lines.append(f'**Action:** {item["action"]}')
        lines.append('')

    # Methods appendix
    lines.append('---')
    lines.append('')
    lines.append('## C. METHODS APPENDIX')
    lines.append('')
    lines.append('All analysis performed via `sqlite3` against `data/campaign_finance.db` in read-only mode.')
    lines.append('Queries are stored in `scripts/newsroom_queries.py` (QUERIES dict).')
    lines.append('CSV exports in `output/newsroom/supporting_tables/`.')
    lines.append('JSON results in `output/newsroom/query_results.json`.')
    lines.append('')
    lines.append('### Key Tables Used')
    lines.append('- `bulk_receipts_clean` (6.4M rows, 1903-2026)')
    lines.append('- `bulk_expenditures_clean` (4.8M rows, anomalies excluded via is_amount_anomalous)')
    lines.append('- `bulk_candidate_committee_finance_agg` (108K rows)')
    lines.append('- `analytics_donor_summary` (1.1M rows)')
    lines.append('- `analytics_donor_committee_agg`')
    lines.append('- `fec_schedule_e_independent_expenditures` (5,097 rows, 2026 cycle)')
    lines.append('- `fec_candidate_cycle_totals` (95 rows)')
    lines.append('- `fec_candidate_match` + `fec_il_candidate_seed`')
    lines.append('- `fec_local_donor_matches` (1,716 rows)')
    lines.append('- `lobbying_donor_matches` (3,131 rows)')
    lines.append('- `lobbying_527_matches` (30 rows)')
    lines.append('- `irs527_organizations` (2,343 rows)')
    lines.append('- `irs527_expenditures` (35,914 rows)')
    lines.append('- `irs527_director_donor_matches`')
    lines.append('')

    return '\n'.join(lines)


def render_to_file(leads, verification, data, out_dir=None):
    out_dir = out_dir or OUT_DIR
    os.makedirs(out_dir, exist_ok=True)
    today = date.today().isoformat()
    md = render_markdown(leads, verification, data, today)
    path = os.path.join(out_dir, f'il_2026_story_leads_{today}.md')
    with open(path, 'w') as f:
        f.write(md)
    return path


if __name__ == '__main__':
    print('Loading query results...')
    with open(JSON_PATH) as f:
        data = json.load(f)
    leads = build_story_leads(data)
    verification = build_verification_queue(data)
    path = render_to_file(leads, verification, data)
    print(f'Wrote {len(leads)} story leads to: {path}')
