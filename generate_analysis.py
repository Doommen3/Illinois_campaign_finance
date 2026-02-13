#!/usr/bin/env python3
"""Generate the 7-phase analysis as PDF and CSV."""

import csv
import os
from reportlab.lib import colors
from reportlab.lib.pagesizes import letter
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
from reportlab.lib.units import inch
from reportlab.platypus import (
    SimpleDocTemplate, Paragraph, Spacer, Table, TableStyle,
    PageBreak, ListFlowable, ListItem, KeepTogether
)
from reportlab.lib.enums import TA_LEFT, TA_CENTER

OUTPUT_DIR = os.path.dirname(os.path.abspath(__file__))
PDF_PATH = os.path.join(OUTPUT_DIR, "analysis_report.pdf")
CSV_PATH = os.path.join(OUTPUT_DIR, "analysis_report.csv")

# ---------------------------------------------------------------------------
# Styles
# ---------------------------------------------------------------------------

styles = getSampleStyleSheet()

styles.add(ParagraphStyle(
    name="SectionTitle",
    parent=styles["Heading1"],
    fontSize=16,
    spaceAfter=10,
    spaceBefore=18,
    textColor=colors.HexColor("#1a365d"),
))
styles.add(ParagraphStyle(
    name="SubSection",
    parent=styles["Heading2"],
    fontSize=13,
    spaceAfter=6,
    spaceBefore=12,
    textColor=colors.HexColor("#2c5282"),
))
styles.add(ParagraphStyle(
    name="SubSubSection",
    parent=styles["Heading3"],
    fontSize=11,
    spaceAfter=4,
    spaceBefore=8,
    textColor=colors.HexColor("#2b6cb0"),
))
styles.add(ParagraphStyle(
    name="BodyText2",
    parent=styles["BodyText"],
    fontSize=9,
    leading=12,
    spaceAfter=4,
))
styles.add(ParagraphStyle(
    name="BulletText",
    parent=styles["BodyText"],
    fontSize=9,
    leading=12,
    leftIndent=18,
    spaceAfter=2,
))
styles.add(ParagraphStyle(
    name="SmallItalic",
    parent=styles["BodyText"],
    fontSize=8,
    leading=10,
    textColor=colors.grey,
    spaceAfter=2,
))

def make_table(headers, rows, col_widths=None):
    data = [headers] + rows
    t = Table(data, colWidths=col_widths, repeatRows=1)
    style = [
        ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#1a365d")),
        ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
        ("FONTSIZE", (0, 0), (-1, 0), 9),
        ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),
        ("FONTSIZE", (0, 1), (-1, -1), 8),
        ("ALIGN", (0, 0), (-1, -1), "LEFT"),
        ("VALIGN", (0, 0), (-1, -1), "TOP"),
        ("GRID", (0, 0), (-1, -1), 0.5, colors.HexColor("#cbd5e0")),
        ("ROWBACKGROUNDS", (0, 1), (-1, -1), [colors.white, colors.HexColor("#f7fafc")]),
        ("TOPPADDING", (0, 0), (-1, -1), 4),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 4),
        ("LEFTPADDING", (0, 0), (-1, -1), 6),
        ("RIGHTPADDING", (0, 0), (-1, -1), 6),
    ]
    t.setStyle(TableStyle(style))
    return t

def p(text, style_name="BodyText2"):
    return Paragraph(text, styles[style_name])

def bullet(text):
    return Paragraph(f"• {text}", styles["BulletText"])

# ---------------------------------------------------------------------------
# Content
# ---------------------------------------------------------------------------

def build_story():
    story = []

    # ── Title Page ──
    story.append(Spacer(1, 2 * inch))
    story.append(Paragraph("Illinois Campaign Finance<br/>Intelligence Platform", ParagraphStyle(
        "TitleMain", parent=styles["Title"], fontSize=26, leading=32,
        textColor=colors.HexColor("#1a365d"), alignment=TA_CENTER,
    )))
    story.append(Spacer(1, 0.3 * inch))
    story.append(Paragraph("Comprehensive 7-Phase Analysis", ParagraphStyle(
        "TitleSub", parent=styles["Heading2"], fontSize=16, alignment=TA_CENTER,
        textColor=colors.HexColor("#4a5568"),
    )))
    story.append(Spacer(1, 0.5 * inch))
    story.append(Paragraph("Product Engineering · UX · Growth · Data Science · Monetization", ParagraphStyle(
        "TitleRoles", parent=styles["Normal"], fontSize=10, alignment=TA_CENTER,
        textColor=colors.HexColor("#718096"),
    )))
    story.append(Spacer(1, 0.3 * inch))
    story.append(Paragraph("February 2026", ParagraphStyle(
        "TitleDate", parent=styles["Normal"], fontSize=10, alignment=TA_CENTER,
        textColor=colors.HexColor("#a0aec0"),
    )))
    story.append(PageBreak())

    # ── Executive Summary ──
    story.append(p("Executive Summary", "SectionTitle"))
    story.append(p(
        "The Illinois Campaign Finance Intelligence Platform is a sophisticated civic-tech application "
        "tracking Illinois local and federal campaign finance data. Built with Python 3.12, Flask 3.0, "
        "and SQLite (WAL mode), it manages a 5.5 GB database with 38 tables spanning 6.4M+ state "
        "receipts and 20K+ federal contributions. The platform features 11 Flask blueprints, 40+ routes, "
        "38 Jinja2 templates, Playwright-based web scraping, FEC API integration, and a materialized "
        "analytics engine with network analysis, anomaly detection, and donor entity resolution."
    ))
    story.append(Spacer(1, 6))
    story.append(p(
        "Key strengths include a deep data model, a mature analytics layer (HHI, Gini, centrality scoring), "
        "and strong entity resolution with confidence-scored merging. Critical gaps include missing CSRF "
        "protection, no API authentication or rate limiting, SQL injection risks in sort parameters, "
        "unbounded query results on several pages, and CSV exports that buffer 500K rows in memory. "
        "The platform has strong bones for scaling into a defensible civic-tech SaaS product."
    ))
    story.append(Spacer(1, 12))

    # ── Top 10 Quick Wins ──
    story.append(p("Top 10 Quick Wins", "SubSection"))
    qw_rows = [
        ["1", "Add CSRF protection", "Security", "2 hrs", "High"],
        ["2", "Parameterize SQL sort fields", "Security", "1 hr", "Critical"],
        ["3", "Add streaming CSV exports", "Performance", "3 hrs", "High"],
        ["4", "Paginate report contributions", "Performance", "2 hrs", "High"],
        ["5", "Set production SECRET_KEY from env", "Security", "15 min", "Critical"],
        ["6", "Add API rate limiting", "Security", "2 hrs", "Medium"],
        ["7", "Add connection pooling", "Performance", "2 hrs", "Medium"],
        ["8", "Add login rate limiting", "Security", "1 hr", "Medium"],
        ["9", "Add 500 error handler", "UX", "30 min", "Low"],
        ["10", "Add cache-busting versioned static assets", "UX", "1 hr", "Low"],
    ]
    story.append(make_table(
        ["#", "Action", "Category", "Effort", "Priority"],
        qw_rows,
        col_widths=[0.3*inch, 2.8*inch, 1*inch, 0.7*inch, 0.8*inch],
    ))
    story.append(Spacer(1, 12))

    # ── NOW / NEXT / LATER Roadmap ──
    story.append(p("NOW / NEXT / LATER Roadmap", "SubSection"))
    roadmap_rows = [
        ["NOW\n(0-4 wks)", "CSRF + SQL injection fix\nStreaming CSV\nPaginate all unbounded queries\nAPI auth + rate limiting\nProduction secret key"],
        ["NEXT\n(1-3 mo)", "Full-text search (FTS5)\nContribution velocity scoring\nDonor similarity clusters\nSaved filter presets + shareable URLs\nMobile-responsive tables"],
        ["LATER\n(3-6 mo)", "Graph export (CSV, GraphML)\nPredictive donor modeling\nPublic API with tiered access\nEmail/Slack alerts for anomalies\nFederal/state cross-network explorer"],
    ]
    story.append(make_table(
        ["Horizon", "Key Deliverables"],
        roadmap_rows,
        col_widths=[1*inch, 5.2*inch],
    ))
    story.append(PageBreak())

    # ── Phase 0: Repo Recon ──
    story.append(p("Phase 0 — Repo Recon", "SectionTitle"))

    story.append(p("Stack & Architecture", "SubSection"))
    stack_rows = [
        ["Language", "Python 3.12"],
        ["Web Framework", "Flask 3.0 with 11 Blueprints"],
        ["Database", "SQLite 5.5 GB, WAL mode, 38 tables"],
        ["Scraping", "Playwright (async), ASP.NET postback handling"],
        ["CLI", "Click command groups"],
        ["Templates", "Jinja2 (38 templates), raw SVG charts"],
        ["API Integration", "FEC API with caching via raw_extractions"],
        ["Deployment", "Hetzner VPS, gunicorn, systemd timers"],
        ["Testing", "pytest + pytest-asyncio, 12 modules (~5,300 lines)"],
    ]
    story.append(make_table(
        ["Component", "Details"],
        stack_rows,
        col_widths=[1.5*inch, 4.7*inch],
    ))
    story.append(Spacer(1, 8))

    story.append(p("Directory Structure", "SubSection"))
    dir_rows = [
        ["cli/", "Click commands: scraping, FEC sync, bulk import, maintenance, analytics refresh"],
        ["database/", "Schema, models, analytics engine (2,265 lines), bulk loader, FEC client, entity resolution"],
        ["webapp/", "Flask app factory, 11 route modules, 38 templates, 3 JS files, CSS"],
        ["tests/", "12 test modules covering parsing, analytics, FEC, entity resolution, webapp routes"],
        ["docs/", "Feature checklist, bulk mapping, data expansion roadmap, update guide"],
        ["data/", "SQLite DB file, bulk download staging"],
    ]
    story.append(make_table(
        ["Directory", "Contents"],
        dir_rows,
        col_widths=[1.2*inch, 5*inch],
    ))
    story.append(Spacer(1, 8))

    story.append(p("Data Model Summary", "SubSection"))
    dm_rows = [
        ["Core (SBE)", "committees, reports, donors, contributions, raw_extractions, scrape_state"],
        ["D-2 Reports", "d2_reports, d2_itemized_links, d2_itemized_entries"],
        ["Bulk Download", "bulk_committees_clean, bulk_d2_totals_clean, bulk_candidates_clean, bulk_receipts_clean + 7 join/agg tables"],
        ["FEC Federal", "fec_il_candidate_seed, fec_candidate_match, fec_candidate_committees, fec_schedule_a_contributions, fec_local_donor_matches"],
        ["Analytics", "analytics_donor_committee_agg, analytics_committee_monthly_totals, analytics_large_contributions, analytics_donor_summary, analytics_snapshots, analytics_materialized_meta"],
        ["Entity Resolution", "donor_entity_local, donor_entity_local_member"],
        ["Admin", "app_users, manual_entry_queue"],
    ]
    story.append(make_table(
        ["Layer", "Tables"],
        dm_rows,
        col_widths=[1.3*inch, 4.9*inch],
    ))
    story.append(Spacer(1, 8))

    story.append(p("Quality & Security Gaps", "SubSection"))
    for item in [
        "SQL injection risk in models.py sort parameter (f-string interpolation without whitelist)",
        "No CSRF protection on form routes (Flask-WTF not used)",
        "No API authentication or rate limiting on 15 JSON endpoints",
        "Production SECRET_KEY defaults to 'dev-secret-key-change-in-production'",
        "CSV exports buffer up to 500K rows in memory (no streaming)",
        "Report detail page loads ALL contributions without pagination",
        "No connection pooling — each request opens a new SQLite connection",
        "No 500 error handler configured",
        "Scraper commands (Playwright automation) have 0% test coverage",
        "No full-text search — large text columns unindexed",
    ]:
        story.append(bullet(item))
    story.append(PageBreak())

    # ── Phase 1: High-Impact Improvements ──
    story.append(p("Phase 1 — High-Impact Improvements", "SectionTitle"))

    story.append(p("15 Shippable Improvements", "SubSection"))
    imp_rows = [
        ["1", "Add CSRF tokens to all forms", "Security", "High", "All POST routes lack CSRF. Add Flask-WTF or manual token."],
        ["2", "Whitelist SQL sort fields", "Security", "Critical", "models.py uses f-string for ORDER BY. Validate against allowed list."],
        ["3", "Stream CSV exports", "Performance", "High", "Replace in-memory buffering with generator + StreamingResponse."],
        ["4", "Paginate report contributions", "Performance", "High", "/reports/<id> loads all rows. Add LIMIT/OFFSET pagination."],
        ["5", "Set SECRET_KEY from env", "Security", "Critical", "Hardcoded dev key. Read from FLASK_SECRET_KEY env var."],
        ["6", "Add API rate limiting", "Security", "Medium", "flask-limiter with per-IP throttling on /api/ endpoints."],
        ["7", "Add connection pooling", "Performance", "Medium", "Each get_db() opens new connection. Use connection pool."],
        ["8", "Add login rate limiting", "Security", "Medium", "No brute-force protection on /auth/login. Add delay/lockout."],
        ["9", "Add 500 error handler", "UX", "Low", "Unhandled exceptions show raw traceback. Add friendly error page."],
        ["10", "Pagination on committee contributions", "UX", "Medium", "Hard-limited to 100 rows with no 'next page'. Add pagination."],
        ["11", "Add FTS5 full-text search", "Feature", "Medium", "Text searches scan full tables. SQLite FTS5 for descriptions."],
        ["12", "Atomic materialization refresh", "Data", "Medium", "Partial refresh leaves stale data. Wrap in transaction."],
        ["13", "Snapshot build progress indicator", "UX", "Low", "Full-mode analytics shows no progress. Add polling endpoint."],
        ["14", "API response envelope", "API", "Low", "Add consistent {data, meta, errors} JSON envelope."],
        ["15", "Consolidate donor detail routes", "UX", "Low", "3 routes (id/key/entity) confuse users. Unify with smart routing."],
    ]
    story.append(make_table(
        ["#", "Improvement", "Category", "Priority", "Details"],
        imp_rows,
        col_widths=[0.3*inch, 1.7*inch, 0.7*inch, 0.6*inch, 2.9*inch],
    ))
    story.append(Spacer(1, 10))

    story.append(p("10 Paper Cuts", "SubSection"))
    pc_rows = [
        ["1", "Search results show no total count", "Add 'Showing X of Y results' header"],
        ["2", "Compare mode loses selections on dropdown change", "Debounce form submission"],
        ["3", "No 'back to list' breadcrumb on detail pages", "Add consistent breadcrumb nav"],
        ["4", "Date filters use plain text input on some pages", "Standardize HTML5 date inputs"],
        ["5", "Truncated names have no tooltip", "Add title attribute for full text on hover"],
        ["6", "Flash messages don't auto-dismiss", "Add JS fade-out after 5 seconds"],
        ["7", "Tables not responsive on mobile", "Add horizontal scroll wrapper"],
        ["8", "No empty-state illustrations", "Add friendly messages when no data found"],
        ["9", "Sort direction indicators inconsistent", "Standardize across all sortable tables"],
        ["10", "Footer disclaimer is dense", "Collapse into expandable accordion"],
    ]
    story.append(make_table(
        ["#", "Paper Cut", "Fix"],
        pc_rows,
        col_widths=[0.3*inch, 2.7*inch, 3.2*inch],
    ))
    story.append(PageBreak())

    # ── Phase 2: Feature Ideation ──
    story.append(p("Phase 2 — Feature Ideation & Prioritization", "SectionTitle"))

    story.append(p("Core Civic-Tech Features (10)", "SubSection"))
    civic_rows = [
        ["1", "Election Countdown Dashboard", "Real-time days-to-election with fundraising pace tracker"],
        ["2", "Candidate Report Card", "Auto-generated score card: diversity, small-dollar %, transparency"],
        ["3", "Voter-Facing Contribution Lookup", "'Who funds my candidates?' search by address/district"],
        ["4", "Dark Money Tracker", "Flag committees with opaque funding sources or shell donors"],
        ["5", "Legislative Vote + Money Correlation", "Cross-reference voting records with top donors"],
        ["6", "Lobbyist-to-Contribution Pipeline", "Match registered lobbyists to contribution patterns"],
        ["7", "Public Campaign Finance Scorecard API", "Open API for journalists and researchers"],
        ["8", "Civic Engagement Alerts", "Email/SMS when new large contributions filed in user's district"],
        ["9", "Historical Trend Explorer", "Multi-cycle trend visualization with inflation adjustment"],
        ["10", "Redistricting Impact Analyzer", "Show how district changes affect donor networks"],
    ]
    story.append(make_table(
        ["#", "Feature", "Description"],
        civic_rows,
        col_widths=[0.3*inch, 2.2*inch, 3.7*inch],
    ))
    story.append(Spacer(1, 10))

    story.append(p("Data Science Features (10)", "SubSection"))
    ds_rows = [
        ["1", "Contribution Velocity Scoring", "Rate-of-change analysis flagging sudden fundraising spikes"],
        ["2", "Donor Similarity Clusters", "K-means/DBSCAN grouping donors by giving patterns"],
        ["3", "Anomaly Detection ML", "Isolation forest or autoencoder for unusual contribution patterns"],
        ["4", "Predictive Fundraising Model", "Forecast next-quarter totals from historical trends"],
        ["5", "Network Community Detection", "Louvain/Label Propagation to find donor communities"],
        ["6", "Influence Propagation Score", "PageRank-style scoring across donor-committee-candidate graph"],
        ["7", "Donor Lifetime Value Model", "Predict donor retention and future contribution amounts"],
        ["8", "Geographic Hotspot Analysis", "Spatial clustering of contribution density by ZIP code"],
        ["9", "Text Classification for Expenditures", "NLP model to categorize spending beyond keyword matching"],
        ["10", "Cross-Network Bridge Detection", "Identify donors bridging otherwise disconnected networks"],
    ]
    story.append(make_table(
        ["#", "Feature", "Description"],
        ds_rows,
        col_widths=[0.3*inch, 2.2*inch, 3.7*inch],
    ))
    story.append(Spacer(1, 10))

    story.append(p("Non-DS Enhancements (10)", "SubSection"))
    nonds_rows = [
        ["1", "Saved Filter Presets", "Save and share complex filter combinations via URL"],
        ["2", "Shareable Report Links", "Permalink to any filtered view with OG meta tags"],
        ["3", "Email Digest Subscriptions", "Weekly summary of new filings in tracked races"],
        ["4", "Embeddable Widgets", "iframe-ready charts for journalists and bloggers"],
        ["5", "Bulk Data Export Portal", "Self-service download of cleaned datasets (CSV/JSON)"],
        ["6", "Audit Log & Activity Feed", "Track admin actions and data changes"],
        ["7", "Multi-State Expansion Framework", "Abstract state-specific logic for other states' data"],
        ["8", "Dark Mode", "CSS custom properties for theme switching"],
        ["9", "Accessibility Audit + Fixes", "WCAG 2.1 AA compliance across all pages"],
        ["10", "Progressive Web App (PWA)", "Offline support for saved reports and bookmarks"],
    ]
    story.append(make_table(
        ["#", "Feature", "Description"],
        nonds_rows,
        col_widths=[0.3*inch, 2.2*inch, 3.7*inch],
    ))
    story.append(PageBreak())

    # ── Phase 3: Data Science Roadmap ──
    story.append(p("Phase 3 — Data Science Roadmap", "SectionTitle"))

    story.append(p("10 Practical DS/Analytics Features", "SubSection"))
    ds_detail = [
        ["1", "Contribution Velocity Scoring", "Calculate 7/30/90-day contribution rate per committee. Flag acceleration > 2σ. Uses existing analytics_committee_monthly_totals.", "NOW", "Low"],
        ["2", "Donor Similarity Clusters", "K-means on (total_amount, frequency, committee_count, recency). Already prototyped in federal_fec.py.", "NOW", "Low"],
        ["3", "Anomaly Detection ML", "Isolation forest on contribution vectors. Upgrade from current rule-based flags in analytics.py.", "NEXT", "Medium"],
        ["4", "Network Community Detection", "Louvain algorithm on donor-committee bipartite graph. Leverages existing get_network_graph().", "NEXT", "Medium"],
        ["5", "Influence Propagation Score", "PageRank on weighted donor→committee→candidate edges. Extends centrality scoring.", "NEXT", "Medium"],
        ["6", "Predictive Fundraising Model", "Linear regression → gradient boosting on historical quarterly totals. Monthly retraining.", "LATER", "High"],
        ["7", "Donor Lifetime Value", "Survival analysis (Kaplan-Meier) on donor retention. Segment by first-contribution size.", "LATER", "High"],
        ["8", "Geographic Hotspot Analysis", "DBSCAN on ZIP centroids weighted by contribution amounts. Extends get_geo_summary().", "LATER", "Medium"],
        ["9", "Expenditure Text Classification", "Fine-tuned classifier replacing keyword NLP in get_nlp_spending_summary(). 8→20+ categories.", "LATER", "High"],
        ["10", "Cross-Network Bridge Detection", "Betweenness centrality on multi-component graph. Identify donors connecting partisan clusters.", "LATER", "Medium"],
    ]
    story.append(make_table(
        ["#", "Feature", "Approach", "Horizon", "Complexity"],
        ds_detail,
        col_widths=[0.3*inch, 1.5*inch, 2.6*inch, 0.6*inch, 0.7*inch],
    ))
    story.append(Spacer(1, 10))

    story.append(p("Easy Wins (ship in < 1 week)", "SubSubSection"))
    for item in [
        "<b>Contribution Velocity Scoring</b> — Pure SQL over analytics_committee_monthly_totals. Add velocity_score column, flag committees with 30-day rate > 2× trailing average. Display as sparkline on candidate pages.",
        "<b>Donor Similarity Clusters</b> — Reuse existing K-means code from federal_fec.py. Run on analytics_donor_summary table. Store cluster_id in new column. Show cluster labels on donor list page.",
    ]:
        story.append(bullet(item))
    story.append(Spacer(1, 8))

    story.append(p("Defensibility Features", "SubSubSection"))
    for item in [
        "<b>Entity Resolution Quality Score</b> — Confidence-weighted merge quality metric. Surfaces low-confidence merges for human review. Builds on donor_entity_local scoring. Differentiator: most civic-tech tools skip entity resolution entirely.",
        "<b>Cross-Jurisdiction Network Analysis</b> — Federal↔state donor overlap network. Uses fec_local_donor_matches + analytics_donor_committee_agg. Shows money flowing across jurisdictional boundaries. Unique dataset advantage.",
    ]:
        story.append(bullet(item))
    story.append(Spacer(1, 8))

    story.append(p("Data Quality Scoring System", "SubSubSection"))
    story.append(p("A 0-100 composite score per entity measuring data completeness and reliability:"))
    dq_rows = [
        ["Completeness", "25%", "% of non-null fields (name, address, occupation, employer, dates)"],
        ["Freshness", "20%", "Days since last update vs. expected refresh cadence"],
        ["Consistency", "20%", "Cross-table agreement (D-2 totals vs. receipt sums, entity merge confidence)"],
        ["Coverage", "20%", "% of expected filings present (election cycles, quarterly reports)"],
        ["Accuracy", "15%", "Reconciliation delta, duplicate rate, garbage detection score"],
    ]
    story.append(make_table(
        ["Dimension", "Weight", "Metric"],
        dq_rows,
        col_widths=[1.2*inch, 0.7*inch, 4.3*inch],
    ))
    story.append(PageBreak())

    # ── Phase 4: Monetization ──
    story.append(p("Phase 4 — Monetization", "SectionTitle"))

    mon_rows = [
        ["1", "Freemium Civic-Tech SaaS", "Free public dashboard; paid tiers for API, alerts, bulk export, custom reports", "$0 / $29 / $99 / mo", "Recommended"],
        ["2", "API Access Tiers", "Free: 100 req/day; Pro: 10K req/day; Enterprise: unlimited + webhooks", "$0 / $49 / $299 / mo", "Recommended"],
        ["3", "Consulting & Custom Analysis", "On-demand research reports for campaigns, journalists, advocacy orgs", "$500-5,000 / report", "Supplement"],
        ["4", "Data Licensing", "Cleaned, linked datasets for academic researchers and news organizations", "$1,000-10,000 / yr", "Supplement"],
        ["5", "Sponsored Research", "Foundation/nonprofit-funded investigations published as open reports", "Grant-funded", "Long-term"],
        ["6", "Training & Workshops", "Campaign finance data literacy for journalists, civic groups, students", "$200-1,000 / session", "Long-term"],
    ]
    story.append(make_table(
        ["#", "Model", "Description", "Pricing", "Status"],
        mon_rows,
        col_widths=[0.3*inch, 1.5*inch, 2.3*inch, 1.2*inch, 0.9*inch],
    ))
    story.append(Spacer(1, 10))

    story.append(p("Recommended Pricing Tiers", "SubSection"))
    pricing_rows = [
        ["Free (Civic)", "Full public dashboard, search, compare, basic analytics", "$0"],
        ["Pro (Researcher)", "API access (10K req/day), CSV bulk export, saved filters, email alerts", "$29/mo"],
        ["Team (Newsroom)", "Everything in Pro + 5 seats, priority data freshness, custom reports", "$99/mo"],
        ["Enterprise", "Unlimited API, webhooks, white-label embeds, SLA, dedicated support", "Custom"],
    ]
    story.append(make_table(
        ["Tier", "Includes", "Price"],
        pricing_rows,
        col_widths=[1.2*inch, 3.8*inch, 1.2*inch],
    ))
    story.append(Spacer(1, 10))

    story.append(p("Paywall Strategy", "SubSection"))
    for item in [
        "Keep all current public-facing pages free forever — civic transparency is the mission.",
        "Gate power-user features: API access, bulk CSV export (>1,000 rows), saved filter presets, email alerts.",
        "Gate data freshness: free tier gets weekly refresh; paid gets daily + real-time FEC sync.",
        "Gate depth: free shows top-50 donors; paid shows full donor list with entity resolution details.",
        "Never gate: basic search, candidate lookup, contribution totals, anomaly flags.",
    ]:
        story.append(bullet(item))
    story.append(PageBreak())

    # ── Phase 5: Growth & Promotion ──
    story.append(p("Phase 5 — Growth & Promotion", "SectionTitle"))

    story.append(p("10 Distribution Channels", "SubSection"))
    ch_rows = [
        ["1", "Civic Tech Twitter/X", "Share visualizations, anomaly flags, election-cycle insights"],
        ["2", "Reddit r/illinois, r/politics, r/dataisbeautiful", "Post data visualizations with source links"],
        ["3", "Hacker News", "Show HN posts on technical architecture, entity resolution, data pipeline"],
        ["4", "Local Journalism Partnerships", "Provide data to reporters covering IL campaigns"],
        ["5", "University Data Science Programs", "Offer as teaching dataset for Northwestern, UChicago, UIUC"],
        ["6", "Civic Tech Meetups", "Chicago civic tech, Code for America brigade presentations"],
        ["7", "Substack / Blog", "Weekly 'Follow the Money' analysis posts with embedded charts"],
        ["8", "LinkedIn", "Build-in-public technical content (see Phase 6)"],
        ["9", "Government Transparency Orgs", "Partner with Sunlight Foundation, OpenSecrets, BGA"],
        ["10", "SEO / Content Marketing", "Target 'Illinois campaign finance' + candidate name long-tail queries"],
    ]
    story.append(make_table(
        ["#", "Channel", "Strategy"],
        ch_rows,
        col_widths=[0.3*inch, 2.2*inch, 3.7*inch],
    ))
    story.append(Spacer(1, 10))

    story.append(p("5 Partnership Targets", "SubSection"))
    partner_rows = [
        ["1", "Better Government Association (BGA)", "Chicago-based watchdog; could co-publish investigations"],
        ["2", "Illinois Campaign for Political Reform", "Advocacy org; could embed our data in their reports"],
        ["3", "Block Club Chicago / Chicago Sun-Times", "Newsrooms covering local politics; data partnership"],
        ["4", "Northwestern Medill Data Journalism", "Student projects using our API; credibility + backlinks"],
        ["5", "OpenSecrets / FollowTheMoney.org", "Cross-link federal/state data; mutual SEO benefit"],
    ]
    story.append(make_table(
        ["#", "Partner", "Value Proposition"],
        partner_rows,
        col_widths=[0.3*inch, 2.5*inch, 3.4*inch],
    ))
    story.append(Spacer(1, 10))

    story.append(p("5 Virality Loops", "SubSection"))
    viral_rows = [
        ["1", "Shareable Candidate Cards", "Auto-generated OG-image cards for any candidate (link → share → traffic)"],
        ["2", "Embeddable Charts", "Copy-paste iframe widgets for journalists writing about IL races"],
        ["3", "'Check Your District' Widget", "Enter address → see who funds your representatives → share result"],
        ["4", "Anomaly Alerts on Social", "Auto-post when large/unusual contributions detected"],
        ["5", "Data Quality Leaderboard", "Gamify completeness: 'Help us verify 100 more contributions'"],
    ]
    story.append(make_table(
        ["#", "Loop", "Mechanism"],
        viral_rows,
        col_widths=[0.3*inch, 2.2*inch, 3.7*inch],
    ))
    story.append(Spacer(1, 10))

    story.append(p("30-Day Launch Plan", "SubSection"))
    launch_rows = [
        ["Week 1", "Ship CSRF + SQL injection fixes. Write 'Why I Built This' blog post. Post to HN Show HN."],
        ["Week 2", "Add streaming CSV + paginated reports. Reach out to BGA and 2 journalists. Post Reddit r/dataisbeautiful visualization."],
        ["Week 3", "Launch shareable candidate cards. Submit to civic tech directories. Present at Chi Hack Night."],
        ["Week 4", "Publish API documentation. Contact Northwestern Medill. Post LinkedIn case study. Measure: traffic, API signups, press mentions."],
    ]
    story.append(make_table(
        ["Week", "Actions"],
        launch_rows,
        col_widths=[0.8*inch, 5.4*inch],
    ))
    story.append(PageBreak())

    # ── Phase 6: LinkedIn Content ──
    story.append(p("Phase 6 — LinkedIn Content Plan", "SectionTitle"))

    story.append(p("10 LinkedIn Post Ideas", "SubSection"))
    li_rows = [
        ["1", "I tracked $2B in IL campaign money. Here's what the data reveals.", "Hook: scale + discovery"],
        ["2", "How I built entity resolution for 6.4M political donations (no ML required)", "Technical credibility"],
        ["3", "The 5 patterns that predict campaign finance anomalies", "Insight-led, shareable"],
        ["4", "Why SQLite handles our 5.5 GB political finance database just fine", "Contrarian tech take"],
        ["5", "I found the donors funding both sides of IL's biggest races", "Controversy + data"],
        ["6", "Building a civic tech product alone: lessons from 18 months of scraping ISBE", "Founder story"],
        ["7", "The entity resolution problem nobody talks about in government data", "Educational, niche authority"],
        ["8", "How federal and state campaign money connects (and why it matters)", "Cross-jurisdiction insight"],
        ["9", "I automated Illinois campaign finance monitoring. Here's the architecture.", "System design content"],
        ["10", "Open data isn't enough. Here's what it takes to make it useful.", "Thought leadership"],
    ]
    story.append(make_table(
        ["#", "Post Title", "Angle"],
        li_rows,
        col_widths=[0.3*inch, 3.5*inch, 2.4*inch],
    ))
    story.append(Spacer(1, 10))

    story.append(p("2 Build-in-Public Series", "SubSection"))
    story.append(p("<b>Series 1: 'Scraping Government Websites' (5 parts)</b>", "BodyText2"))
    for item in [
        "Part 1: Why government data is stuck in the 1990s",
        "Part 2: Reverse-engineering ASP.NET postbacks with Playwright",
        "Part 3: Building a resilient scraper (rate limiting, retry, resumability)",
        "Part 4: Parsing messy financial tables at scale",
        "Part 5: From raw HTML to queryable analytics",
    ]:
        story.append(bullet(item))
    story.append(Spacer(1, 6))

    story.append(p("<b>Series 2: 'Entity Resolution for Civic Data' (4 parts)</b>", "BodyText2"))
    for item in [
        "Part 1: The donor deduplication problem (why 'John Smith' isn't one person)",
        "Part 2: Jaccard similarity, confidence scoring, and bridge rules",
        "Part 3: Building a human-in-the-loop merge review system",
        "Part 4: Matching donors across federal and state systems",
    ]:
        story.append(bullet(item))
    story.append(Spacer(1, 10))

    story.append(p("2 Case Studies", "SubSection"))
    for item in [
        "<b>Case Study 1:</b> 'How we identified $X in potentially misreported contributions using D-2 reconciliation' — Walk through the bulk_d2_receipts_recon pipeline, show real discrepancy examples, explain the audit flag system.",
        "<b>Case Study 2:</b> 'Mapping Illinois's donor network: 6.4M contributions, one graph' — Visualize the network analysis output, highlight power players, show community detection results, demonstrate the civic value.",
    ]:
        story.append(bullet(item))
    story.append(Spacer(1, 10))

    story.append(p("1 Technical Deep Dive", "SubSection"))
    story.append(p(
        "<b>'Materialized Analytics at Scale with SQLite'</b> — How we serve complex analytics dashboards "
        "from a single SQLite database. Covers: WAL mode tuning, materialized view refresh strategy, "
        "snapshot caching with SHA1(params) keys, background ThreadPoolExecutor refresh, HHI/Gini "
        "computation, and why this architecture works up to ~10 GB before needing PostgreSQL."
    ))
    story.append(PageBreak())

    # ── Phase 7: Risk Register ──
    story.append(p("Phase 7 — Risk Register", "SectionTitle"))

    risk_rows = [
        ["R1", "SQL Injection", "Critical", "High", "Security", "Whitelist sort fields; parameterize all dynamic SQL"],
        ["R2", "Missing CSRF", "High", "High", "Security", "Add Flask-WTF CSRF tokens to all forms"],
        ["R3", "No API Auth", "High", "High", "Security", "Add API key auth + rate limiting via flask-limiter"],
        ["R4", "Hardcoded Secret Key", "Critical", "High", "Security", "Move to env var; rotate immediately in production"],
        ["R5", "Memory OOM on CSV Export", "High", "Medium", "Performance", "Implement streaming response with generator"],
        ["R6", "Unbounded Report Queries", "High", "Medium", "Performance", "Add pagination to /reports/<id> and /api/reports/<id>"],
        ["R7", "ISBE Website Changes", "Medium", "High", "Data", "Add HTML structure validation tests; alert on parse failures"],
        ["R8", "FEC API Rate Limits", "Medium", "Medium", "Data", "Implement proper exponential backoff; cache aggressively"],
        ["R9", "Stale Analytics Cache", "Medium", "Medium", "Data", "Add cache invalidation on data refresh; show freshness badge"],
        ["R10", "Single-Server Deployment", "Medium", "Low", "Infra", "Document recovery procedure; add automated backups"],
        ["R11", "No Audit Trail", "Medium", "Low", "Compliance", "Log all admin actions; add activity feed"],
        ["R12", "Privacy (Donor PII)", "High", "Low", "Legal", "All data is public record; add disclaimer; no SSN/DOB stored"],
        ["R13", "Bus Factor = 1", "High", "Medium", "Maintainability", "Document architecture; add contributor guide; write ADRs"],
        ["R14", "Test Coverage Gaps", "Medium", "Medium", "Quality", "Add scraper tests with mocked HTML; increase to 80% coverage"],
        ["R15", "SQLite Scale Ceiling", "Low", "Low", "Scalability", "Monitor DB size; plan PostgreSQL migration path at ~10 GB"],
    ]
    story.append(make_table(
        ["ID", "Risk", "Severity", "Likelihood", "Category", "Mitigation"],
        risk_rows,
        col_widths=[0.35*inch, 1.2*inch, 0.6*inch, 0.7*inch, 0.75*inch, 2.6*inch],
    ))
    story.append(PageBreak())

    # ── Implementation Notes: Top 5 ──
    story.append(p("Implementation Notes — Top 5 Roadmap Items", "SectionTitle"))

    items = [
        ("1. CSRF Protection", [
            "<b>File:</b> webapp/app.py — Add Flask-WTF CSRFProtect(app) in create_app()",
            "<b>Files:</b> All 38 templates with &lt;form&gt; tags — Add {{ csrf_token() }} hidden input",
            "<b>Dependency:</b> pip install flask-wtf",
            "<b>Test:</b> Verify POST requests without token return 400",
        ]),
        ("2. SQL Sort Field Whitelist", [
            "<b>File:</b> database/models.py — Committee.get_all(), Report.get_all(), Donor.get_all()",
            "<b>Fix:</b> Replace f-string ORDER BY with ALLOWED_SORTS dict lookup",
            "<b>Pattern:</b> sort_field = ALLOWED_SORTS.get(sort_field, 'id')",
            "<b>Test:</b> Verify injection attempt ('id; DROP TABLE') is rejected",
        ]),
        ("3. Streaming CSV Export", [
            "<b>Files:</b> webapp/routes/candidate_finance.py, d2_receipts_recon.py",
            "<b>Fix:</b> Replace list accumulation with generator function",
            "<b>Pattern:</b> return Response(stream_with_context(generate()), mimetype='text/csv')",
            "<b>Impact:</b> Memory usage drops from O(n) to O(1) for 500K-row exports",
        ]),
        ("4. Report Pagination", [
            "<b>File:</b> webapp/routes/reports.py — report_detail() function",
            "<b>File:</b> webapp/routes/api.py — api_report_detail() function",
            "<b>Fix:</b> Add page/per_page params with LIMIT/OFFSET query",
            "<b>Template:</b> webapp/templates/reports/detail.html — Add pagination controls",
        ]),
        ("5. API Rate Limiting", [
            "<b>File:</b> webapp/app.py — Add flask-limiter initialization",
            "<b>File:</b> webapp/routes/api.py — Add @limiter.limit() decorators",
            "<b>Config:</b> Default 100/hour for anonymous; 1000/hour for API key holders",
            "<b>Dependency:</b> pip install flask-limiter",
        ]),
    ]
    for title, bullets_list in items:
        story.append(p(title, "SubSection"))
        for b in bullets_list:
            story.append(bullet(b))
        story.append(Spacer(1, 6))

    return story


# ---------------------------------------------------------------------------
# CSV Data
# ---------------------------------------------------------------------------

def build_csv_rows():
    """Return (headers, rows) for the flat CSV export."""
    headers = ["Phase", "Category", "Item #", "Title", "Description", "Priority", "Horizon", "Effort", "Risk/Severity"]
    rows = []

    # Quick Wins
    for i, (title, cat, effort, pri) in enumerate([
        ("Add CSRF protection", "Security", "2 hrs", "High"),
        ("Parameterize SQL sort fields", "Security", "1 hr", "Critical"),
        ("Add streaming CSV exports", "Performance", "3 hrs", "High"),
        ("Paginate report contributions", "Performance", "2 hrs", "High"),
        ("Set production SECRET_KEY from env", "Security", "15 min", "Critical"),
        ("Add API rate limiting", "Security", "2 hrs", "Medium"),
        ("Add connection pooling", "Performance", "2 hrs", "Medium"),
        ("Add login rate limiting", "Security", "1 hr", "Medium"),
        ("Add 500 error handler", "UX", "30 min", "Low"),
        ("Add cache-busting versioned static assets", "UX", "1 hr", "Low"),
    ], 1):
        rows.append(["Quick Wins", cat, str(i), title, "", pri, "NOW", effort, ""])

    # Phase 1 improvements
    for i, (title, cat, pri, desc) in enumerate([
        ("Add CSRF tokens to all forms", "Security", "High", "All POST routes lack CSRF protection"),
        ("Whitelist SQL sort fields", "Security", "Critical", "models.py uses f-string for ORDER BY"),
        ("Stream CSV exports", "Performance", "High", "Replace in-memory buffering with generator"),
        ("Paginate report contributions", "Performance", "High", "/reports/<id> loads all rows"),
        ("Set SECRET_KEY from env", "Security", "Critical", "Hardcoded dev key in production"),
        ("Add API rate limiting", "Security", "Medium", "No throttling on /api/ endpoints"),
        ("Add connection pooling", "Performance", "Medium", "Each request opens new connection"),
        ("Add login rate limiting", "Security", "Medium", "No brute-force protection"),
        ("Add 500 error handler", "UX", "Low", "Unhandled exceptions show raw traceback"),
        ("Pagination on committee contributions", "UX", "Medium", "Hard-limited to 100 rows"),
        ("Add FTS5 full-text search", "Feature", "Medium", "Text searches scan full tables"),
        ("Atomic materialization refresh", "Data", "Medium", "Partial refresh leaves stale data"),
        ("Snapshot build progress indicator", "UX", "Low", "No progress feedback in full mode"),
        ("API response envelope", "API", "Low", "Inconsistent JSON response format"),
        ("Consolidate donor detail routes", "UX", "Low", "3 routes confuse users"),
    ], 1):
        rows.append(["Phase 1: Improvements", cat, str(i), title, desc, pri, "NOW", "", ""])

    # Phase 1 paper cuts
    for i, (title, fix) in enumerate([
        ("Search results show no total count", "Add 'Showing X of Y results' header"),
        ("Compare mode loses selections on dropdown change", "Debounce form submission"),
        ("No breadcrumb on detail pages", "Add consistent breadcrumb nav"),
        ("Date filters use plain text on some pages", "Standardize HTML5 date inputs"),
        ("Truncated names have no tooltip", "Add title attribute for hover"),
        ("Flash messages don't auto-dismiss", "Add JS fade-out after 5 seconds"),
        ("Tables not responsive on mobile", "Add horizontal scroll wrapper"),
        ("No empty-state illustrations", "Add friendly messages for no data"),
        ("Sort direction indicators inconsistent", "Standardize across tables"),
        ("Footer disclaimer is dense", "Collapse into expandable accordion"),
    ], 1):
        rows.append(["Phase 1: Paper Cuts", "UX", str(i), title, fix, "Low", "NOW", "", ""])

    # Phase 2 features
    for i, (title, desc, cat) in enumerate([
        ("Election Countdown Dashboard", "Real-time days-to-election with fundraising pace tracker", "Civic-Tech"),
        ("Candidate Report Card", "Auto-generated score card: diversity, small-dollar %, transparency", "Civic-Tech"),
        ("Voter-Facing Contribution Lookup", "Search by address/district", "Civic-Tech"),
        ("Dark Money Tracker", "Flag committees with opaque funding sources", "Civic-Tech"),
        ("Legislative Vote + Money Correlation", "Cross-reference voting records with donors", "Civic-Tech"),
        ("Lobbyist-to-Contribution Pipeline", "Match lobbyists to contribution patterns", "Civic-Tech"),
        ("Public Campaign Finance Scorecard API", "Open API for journalists/researchers", "Civic-Tech"),
        ("Civic Engagement Alerts", "Email/SMS for new large contributions", "Civic-Tech"),
        ("Historical Trend Explorer", "Multi-cycle visualization with inflation adjustment", "Civic-Tech"),
        ("Redistricting Impact Analyzer", "Show district changes affect donor networks", "Civic-Tech"),
        ("Contribution Velocity Scoring", "Rate-of-change analysis for fundraising spikes", "Data Science"),
        ("Donor Similarity Clusters", "K-means/DBSCAN grouping donors by patterns", "Data Science"),
        ("Anomaly Detection ML", "Isolation forest for unusual patterns", "Data Science"),
        ("Predictive Fundraising Model", "Forecast next-quarter totals", "Data Science"),
        ("Network Community Detection", "Louvain algorithm on donor-committee graph", "Data Science"),
        ("Influence Propagation Score", "PageRank-style scoring", "Data Science"),
        ("Donor Lifetime Value Model", "Survival analysis on donor retention", "Data Science"),
        ("Geographic Hotspot Analysis", "Spatial clustering by ZIP code", "Data Science"),
        ("Text Classification for Expenditures", "NLP model beyond keyword matching", "Data Science"),
        ("Cross-Network Bridge Detection", "Betweenness centrality on multi-component graph", "Data Science"),
        ("Saved Filter Presets", "Save and share complex filter combinations", "Non-DS"),
        ("Shareable Report Links", "Permalink with OG meta tags", "Non-DS"),
        ("Email Digest Subscriptions", "Weekly summary of new filings", "Non-DS"),
        ("Embeddable Widgets", "iframe-ready charts for journalists", "Non-DS"),
        ("Bulk Data Export Portal", "Self-service download of cleaned datasets", "Non-DS"),
        ("Audit Log & Activity Feed", "Track admin actions and data changes", "Non-DS"),
        ("Multi-State Expansion Framework", "Abstract state-specific logic", "Non-DS"),
        ("Dark Mode", "CSS custom properties for theme switching", "Non-DS"),
        ("Accessibility Audit + Fixes", "WCAG 2.1 AA compliance", "Non-DS"),
        ("Progressive Web App (PWA)", "Offline support for saved reports", "Non-DS"),
    ], 1):
        horizon = "NOW" if i <= 4 else ("NEXT" if i <= 15 else "LATER")
        rows.append(["Phase 2: Features", cat, str(i), title, desc, "", horizon, "", ""])

    # Phase 3 DS roadmap
    for i, (title, approach, horizon, complexity) in enumerate([
        ("Contribution Velocity Scoring", "Pure SQL over monthly totals", "NOW", "Low"),
        ("Donor Similarity Clusters", "K-means on donor features", "NOW", "Low"),
        ("Anomaly Detection ML", "Isolation forest upgrade", "NEXT", "Medium"),
        ("Network Community Detection", "Louvain on bipartite graph", "NEXT", "Medium"),
        ("Influence Propagation Score", "PageRank on weighted edges", "NEXT", "Medium"),
        ("Predictive Fundraising Model", "Gradient boosting on quarterly totals", "LATER", "High"),
        ("Donor Lifetime Value", "Survival analysis", "LATER", "High"),
        ("Geographic Hotspot Analysis", "DBSCAN on ZIP centroids", "LATER", "Medium"),
        ("Expenditure Text Classification", "Fine-tuned classifier", "LATER", "High"),
        ("Cross-Network Bridge Detection", "Betweenness centrality", "LATER", "Medium"),
    ], 1):
        rows.append(["Phase 3: DS Roadmap", "Data Science", str(i), title, approach, "", horizon, complexity, ""])

    # Phase 4 monetization
    for i, (title, desc, pricing) in enumerate([
        ("Freemium Civic-Tech SaaS", "Free dashboard; paid API/alerts/export", "$0/$29/$99/mo"),
        ("API Access Tiers", "Tiered request limits", "$0/$49/$299/mo"),
        ("Consulting & Custom Analysis", "On-demand research reports", "$500-5,000/report"),
        ("Data Licensing", "Cleaned datasets for researchers", "$1,000-10,000/yr"),
        ("Sponsored Research", "Foundation-funded investigations", "Grant-funded"),
        ("Training & Workshops", "Data literacy sessions", "$200-1,000/session"),
    ], 1):
        rows.append(["Phase 4: Monetization", "Business", str(i), title, desc, "", "", "", pricing])

    # Phase 7 risks
    for rid, title, severity, likelihood, cat, mitigation in [
        ("R1", "SQL Injection", "Critical", "High", "Security", "Whitelist sort fields; parameterize all dynamic SQL"),
        ("R2", "Missing CSRF", "High", "High", "Security", "Add Flask-WTF CSRF tokens to all forms"),
        ("R3", "No API Auth", "High", "High", "Security", "Add API key auth + rate limiting"),
        ("R4", "Hardcoded Secret Key", "Critical", "High", "Security", "Move to env var; rotate immediately"),
        ("R5", "Memory OOM on CSV Export", "High", "Medium", "Performance", "Implement streaming response"),
        ("R6", "Unbounded Report Queries", "High", "Medium", "Performance", "Add pagination"),
        ("R7", "ISBE Website Changes", "Medium", "High", "Data", "Add HTML structure validation tests"),
        ("R8", "FEC API Rate Limits", "Medium", "Medium", "Data", "Proper exponential backoff + caching"),
        ("R9", "Stale Analytics Cache", "Medium", "Medium", "Data", "Cache invalidation on refresh"),
        ("R10", "Single-Server Deployment", "Medium", "Low", "Infra", "Document recovery; add backups"),
        ("R11", "No Audit Trail", "Medium", "Low", "Compliance", "Log admin actions; activity feed"),
        ("R12", "Privacy (Donor PII)", "High", "Low", "Legal", "All public record; add disclaimer"),
        ("R13", "Bus Factor = 1", "High", "Medium", "Maintainability", "Document architecture; contributor guide"),
        ("R14", "Test Coverage Gaps", "Medium", "Medium", "Quality", "Add scraper tests; target 80% coverage"),
        ("R15", "SQLite Scale Ceiling", "Low", "Low", "Scalability", "Plan PostgreSQL migration at ~10 GB"),
    ]:
        rows.append(["Phase 7: Risks", cat, rid, title, mitigation, "", "", "", severity + " / " + likelihood])

    return headers, rows


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main():
    # ── PDF ──
    doc = SimpleDocTemplate(
        PDF_PATH,
        pagesize=letter,
        topMargin=0.6*inch,
        bottomMargin=0.6*inch,
        leftMargin=0.7*inch,
        rightMargin=0.7*inch,
    )
    story = build_story()
    doc.build(story)
    print(f"PDF written to {PDF_PATH}")

    # ── CSV ──
    headers, rows = build_csv_rows()
    with open(CSV_PATH, "w", newline="", encoding="utf-8") as f:
        writer = csv.writer(f)
        writer.writerow(headers)
        writer.writerows(rows)
    print(f"CSV written to {CSV_PATH}")


if __name__ == "__main__":
    main()
