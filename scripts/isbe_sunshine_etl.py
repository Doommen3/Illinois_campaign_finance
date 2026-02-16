#!/usr/bin/env python3
"""
ISBE Sunshine ETL — PostgreSQL-native bulk loader for Illinois campaign finance data.

Adapts the illinois-sunshine (datamade) ETL approach:
  1. Downloads tab-delimited ISBE bulk files from elections.il.gov
  2. Loads into isbe_* tables via PostgreSQL COPY
  3. Creates materialized views for deduplication (condensed_receipts/expenditures)

Usage:
    python scripts/isbe_sunshine_etl.py [--download] [--bulk-dir Bulk_download]
    python scripts/isbe_sunshine_etl.py --tables committees,receipts  # load specific tables only
"""
import argparse
import csv
import io
import os
import sys
import time
import urllib.request
from pathlib import Path

import psycopg
from psycopg import sql as pgsql

# Increase CSV field size limit for large ISBE fields
csv.field_size_limit(10 * 1024 * 1024)  # 10 MB

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------
DB_URL = os.environ.get("DATABASE_URL", "postgresql://devin@localhost/ilcf")
ISBE_BASE_URL = "https://elections.il.gov/campaigndisclosuredatafiles"
DEFAULT_BULK_DIR = Path(__file__).resolve().parent.parent / "Bulk_download"

# Map of ISBE filename → our target table
ISBE_FILES = {
    "Committees.txt":        "isbe_committees",
    "Candidates.txt":        "isbe_candidates",
    "Receipts.txt":          "isbe_receipts",
    "Expenditures.txt":      "isbe_expenditures",
    "D2Totals.txt":          "isbe_d2_reports",
    "CmteCandidateLinks.txt":"isbe_candidate_committees",
    "FiledDocs.txt":         "isbe_filed_docs",
    "CanElections.txt":      "isbe_candidacies",
    "Officers.txt":          "isbe_officers",
    "PrevOfficers.txt":      "isbe_prev_officers",  # merged into isbe_officers later
    "CmteOfficerLinks.txt":  "isbe_officer_committees",
    "Investments.txt":       "isbe_investments",
}

# Canonical column name for each ISBE file → stable column order
# Derived from the ISBE data dictionary and sunshine models
FILE_COLUMNS = {
    "Committees.txt": [
        "id", "type_of_committee", "state_committee", "local_committee",
        "refer_name", "name", "address1", "address2", "address3",
        "city", "state", "zipcode", "status", "status_date",
        "creation_date", "creation_amount", "disp_funds_return",
        "disp_funds_political_committee", "disp_funds_charity",
        "disp_funds_95", "candidate_position", "policy_position",
        "party_affiliation", "purpose",
    ],
    "Candidates.txt": [
        "id", "last_name", "first_name", "address1", "address2",
        "city", "state", "zipcode", "office", "district_type",
        "district", "residence_county", "party_affiliation",
        "redaction_requested",
    ],
    "Receipts.txt": [
        "id", "committee_id", "filed_doc_id", "etrans_id",
        "last_name", "first_name", "received_date", "amount",
        "aggregate_amount", "loan_amount", "occupation", "employer",
        "address1", "address2", "city", "state", "zipcode",
        "d2_part", "description",
        "vendor_last_name", "vendor_first_name",
        "vendor_address1", "vendor_address2",
        "vendor_city", "vendor_state", "vendor_zipcode",
        "archived", "country", "redaction_requested",
    ],
    "Expenditures.txt": [
        "id", "committee_id", "filed_doc_id", "etrans_id",
        "last_name", "first_name", "expended_date", "amount",
        "aggregate_amount", "address1", "address2",
        "city", "state", "zipcode", "d2_part", "purpose",
        "candidate_name", "office", "supporting", "opposing",
        "archived", "country", "redaction_requested",
    ],
    "D2Totals.txt": [
        "id", "committee_id", "filed_doc_id",
        "beginning_funds_avail", "individual_itemized",
        "individual_non_itemized", "transfer_in", "loan_received",
        "other_receipts", "total_receipts",
        "inkind_itemized", "inkind_non_itemized", "total_inkind",
        "expenditures_itemized", "expenditures_non_itemized",
        "independent_expenditures_itemized", "independent_expenditures_non_itemized",
        "total_expenditures", "debts_itemized", "debts_non_itemized",
        "total_debts", "total_investments", "end_funds_available",
        "archived",
    ],
    "CmteCandidateLinks.txt": [
        "id", "committee_id", "candidate_id",
    ],
    "FiledDocs.txt": [
        "id", "committee_id", "filed_doc_type", "doc_name", "amended",
        "comment", "pages", "election_type", "election_year",
        "reporting_period_begin", "reporting_period_end",
        "received_at", "received_datetime",
        "source", "provider",
        "signer_last_name", "signer_first_name",
        "submitter_last_name", "submitter_first_name",
        "submitter_address1", "submitter_address2",
        "submitter_city", "submitter_state", "submitter_zipcode",
        "b9_signer_last_name", "b9_signer_first_name",
        "archived", "clarification", "redaction_requested",
    ],
    "CanElections.txt": [
        "id", "candidate_id", "election_type", "election_year",
        "inc_chall_open", "won_lost", "fair_campaign",
        "limits_off", "limits_off_reason",
    ],
    "Officers.txt": [
        "id", "last_name", "first_name",
        "address1", "address2", "city", "state", "zipcode",
        "title", "phone", "redaction_requested",
    ],
    "PrevOfficers.txt": [
        "id", "committee_id", "last_name", "first_name",
        "address1", "address2", "city", "state", "zipcode",
        "title", "resign_date", "redaction_requested",
    ],
    "CmteOfficerLinks.txt": [
        "id", "committee_id", "officer_id",
    ],
    "Investments.txt": [
        "id", "committee_id", "filed_doc_id", "description",
        "purchase_date", "purchase_shares", "purchase_price",
        "current_value", "liquid_value", "liquid_date",
        "last_name", "first_name",
        "address1", "address2", "city", "state", "zipcode",
        "archived", "country",
    ],
}

# ---------------------------------------------------------------------------
# DDL — Table creation
# ---------------------------------------------------------------------------
SCHEMA_DDL = """
-- ISBE Sunshine Tables (PostgreSQL-native)
-- Adapted from datamade/illinois-sunshine schema

DROP MATERIALIZED VIEW IF EXISTS isbe_committee_money CASCADE;
DROP MATERIALIZED VIEW IF EXISTS isbe_candidate_money CASCADE;
DROP MATERIALIZED VIEW IF EXISTS isbe_condensed_expenditures CASCADE;
DROP MATERIALIZED VIEW IF EXISTS isbe_condensed_receipts CASCADE;
DROP MATERIALIZED VIEW IF EXISTS isbe_most_recent_filings CASCADE;

DROP TABLE IF EXISTS isbe_investments CASCADE;
DROP TABLE IF EXISTS isbe_officer_committees CASCADE;
DROP TABLE IF EXISTS isbe_candidate_committees CASCADE;
DROP TABLE IF EXISTS isbe_candidacies CASCADE;
DROP TABLE IF EXISTS isbe_officers CASCADE;
DROP TABLE IF EXISTS isbe_prev_officers CASCADE;
DROP TABLE IF EXISTS isbe_d2_reports CASCADE;
DROP TABLE IF EXISTS isbe_expenditures CASCADE;
DROP TABLE IF EXISTS isbe_receipts CASCADE;
DROP TABLE IF EXISTS isbe_filed_docs CASCADE;
DROP TABLE IF EXISTS isbe_candidates CASCADE;
DROP TABLE IF EXISTS isbe_committees CASCADE;

-- ENUMs (drop + create for idempotency)
DO $$ BEGIN
    DROP TYPE IF EXISTS isbe_committee_position CASCADE;
    CREATE TYPE isbe_committee_position AS ENUM ('support', 'oppose');
EXCEPTION WHEN duplicate_object THEN NULL;
END $$;

DO $$ BEGIN
    DROP TYPE IF EXISTS isbe_candidacy_race_type CASCADE;
    CREATE TYPE isbe_candidacy_race_type AS ENUM ('incumbent', 'challenger', 'open seat', 'retired');
EXCEPTION WHEN duplicate_object THEN NULL;
END $$;

DO $$ BEGIN
    DROP TYPE IF EXISTS isbe_candidacy_outcome CASCADE;
    CREATE TYPE isbe_candidacy_outcome AS ENUM ('won', 'lost');
EXCEPTION WHEN duplicate_object THEN NULL;
END $$;

CREATE TABLE isbe_committees (
    id              INTEGER PRIMARY KEY,
    type            TEXT,
    state_committee BOOLEAN,
    local_committee BOOLEAN,
    refer_name      TEXT,
    name            TEXT,
    address1        TEXT,
    address2        TEXT,
    address3        TEXT,
    city            TEXT,
    state           TEXT,
    zipcode         TEXT,
    active          BOOLEAN DEFAULT TRUE,
    status_date     DATE,
    creation_date   DATE,
    creation_amount DOUBLE PRECISION,
    disp_funds_return TEXT,
    disp_funds_political_committee TEXT,
    disp_funds_charity TEXT,
    disp_funds_95   TEXT,
    candidate_position isbe_committee_position,
    policy_position    isbe_committee_position,
    party           TEXT,
    purpose         TEXT
);

CREATE TABLE isbe_candidates (
    id                  INTEGER PRIMARY KEY,
    last_name           TEXT,
    first_name          TEXT,
    address1            TEXT,
    address2            TEXT,
    city                TEXT,
    state               TEXT,
    zipcode             TEXT,
    office              TEXT,
    district_type       TEXT,
    district            TEXT,
    residence_county    TEXT,
    party               TEXT,
    redaction_requested BOOLEAN DEFAULT FALSE
);

CREATE TABLE isbe_filed_docs (
    id                      INTEGER PRIMARY KEY,
    committee_id            INTEGER REFERENCES isbe_committees(id),
    filed_doc_type          TEXT,
    doc_name                TEXT,
    amended                 BOOLEAN DEFAULT FALSE,
    comment                 TEXT,
    pages                   INTEGER,
    election_type           TEXT,
    election_year           INTEGER,
    reporting_period_begin  DATE,
    reporting_period_end    DATE,
    received_at             DATE,
    received_datetime       TIMESTAMP,
    source                  TEXT,
    provider                TEXT,
    signer_last_name        TEXT,
    signer_first_name       TEXT,
    submitter_last_name     TEXT,
    submitter_first_name    TEXT,
    submitter_address1      TEXT,
    submitter_address2      TEXT,
    submitter_city          TEXT,
    submitter_state         TEXT,
    submitter_zipcode       TEXT,
    b9_signer_last_name     TEXT,
    b9_signer_first_name    TEXT,
    archived                BOOLEAN DEFAULT FALSE,
    clarification           TEXT,
    redaction_requested     BOOLEAN DEFAULT FALSE
);
CREATE INDEX idx_isbe_filed_docs_committee_id ON isbe_filed_docs(committee_id);
CREATE INDEX idx_isbe_filed_docs_doc_name ON isbe_filed_docs(doc_name);
CREATE INDEX idx_isbe_filed_docs_reporting ON isbe_filed_docs(committee_id, reporting_period_end DESC, received_datetime DESC);

CREATE TABLE isbe_receipts (
    id                  INTEGER PRIMARY KEY,
    committee_id        INTEGER REFERENCES isbe_committees(id),
    filed_doc_id        INTEGER REFERENCES isbe_filed_docs(id),
    etrans_id           TEXT,
    last_name           TEXT,
    first_name          TEXT,
    received_date       DATE,
    amount              DOUBLE PRECISION,
    aggregate_amount    DOUBLE PRECISION,
    loan_amount         DOUBLE PRECISION,
    occupation          TEXT,
    employer            TEXT,
    address1            TEXT,
    address2            TEXT,
    city                TEXT,
    state               TEXT,
    zipcode             TEXT,
    d2_part             TEXT,
    description         TEXT,
    vendor_last_name    TEXT,
    vendor_first_name   TEXT,
    vendor_address1     TEXT,
    vendor_address2     TEXT,
    vendor_city         TEXT,
    vendor_state        TEXT,
    vendor_zipcode      TEXT,
    archived            BOOLEAN DEFAULT FALSE,
    country             TEXT,
    redaction_requested BOOLEAN DEFAULT FALSE
);
CREATE INDEX idx_isbe_receipts_committee_id ON isbe_receipts(committee_id);
CREATE INDEX idx_isbe_receipts_filed_doc_id ON isbe_receipts(filed_doc_id);
CREATE INDEX idx_isbe_receipts_received_date ON isbe_receipts(received_date);
CREATE INDEX idx_isbe_receipts_amount ON isbe_receipts(amount);
CREATE INDEX idx_isbe_receipts_committee_date_doc ON isbe_receipts(committee_id, received_date, filed_doc_id);

CREATE TABLE isbe_expenditures (
    id                  INTEGER PRIMARY KEY,
    committee_id        INTEGER REFERENCES isbe_committees(id),
    filed_doc_id        INTEGER REFERENCES isbe_filed_docs(id),
    etrans_id           TEXT,
    last_name           TEXT,
    first_name          TEXT,
    expended_date       DATE,
    amount              DOUBLE PRECISION,
    aggregate_amount    DOUBLE PRECISION,
    address1            TEXT,
    address2            TEXT,
    city                TEXT,
    state               TEXT,
    zipcode             TEXT,
    d2_part             TEXT,
    purpose             TEXT,
    candidate_name      TEXT,
    office              TEXT,
    supporting          BOOLEAN DEFAULT FALSE,
    opposing            BOOLEAN DEFAULT FALSE,
    archived            BOOLEAN DEFAULT FALSE,
    country             TEXT,
    redaction_requested BOOLEAN DEFAULT FALSE
);
CREATE INDEX idx_isbe_expenditures_committee_id ON isbe_expenditures(committee_id);
CREATE INDEX idx_isbe_expenditures_filed_doc_id ON isbe_expenditures(filed_doc_id);
CREATE INDEX idx_isbe_expenditures_expended_date ON isbe_expenditures(expended_date);
CREATE INDEX idx_isbe_expenditures_committee_date_doc ON isbe_expenditures(committee_id, expended_date, filed_doc_id);

CREATE TABLE isbe_d2_reports (
    id                              INTEGER PRIMARY KEY,
    committee_id                    INTEGER REFERENCES isbe_committees(id),
    filed_doc_id                    INTEGER REFERENCES isbe_filed_docs(id),
    beginning_funds_avail           DOUBLE PRECISION,
    individual_itemized             DOUBLE PRECISION,
    individual_non_itemized         DOUBLE PRECISION,
    transfer_in                     DOUBLE PRECISION,
    loan_received                   DOUBLE PRECISION,
    other_receipts                  DOUBLE PRECISION,
    total_receipts                  DOUBLE PRECISION,
    inkind_itemized                 DOUBLE PRECISION,
    inkind_non_itemized             DOUBLE PRECISION,
    total_inkind                    DOUBLE PRECISION,
    expenditures_itemized           DOUBLE PRECISION,
    expenditures_non_itemized       DOUBLE PRECISION,
    independent_expenditures_itemized     DOUBLE PRECISION,
    independent_expenditures_non_itemized DOUBLE PRECISION,
    total_expenditures              DOUBLE PRECISION,
    debts_itemized                  DOUBLE PRECISION,
    debts_non_itemized              DOUBLE PRECISION,
    total_debts                     DOUBLE PRECISION,
    total_investments               DOUBLE PRECISION,
    end_funds_available             DOUBLE PRECISION,
    archived                        BOOLEAN DEFAULT FALSE
);
CREATE INDEX idx_isbe_d2_reports_committee_id ON isbe_d2_reports(committee_id);
CREATE INDEX idx_isbe_d2_reports_filed_doc_id ON isbe_d2_reports(filed_doc_id);
CREATE INDEX idx_isbe_d2_reports_filed_doc_committee ON isbe_d2_reports(filed_doc_id, committee_id);

CREATE TABLE isbe_candidate_committees (
    id              INTEGER PRIMARY KEY,
    committee_id    INTEGER REFERENCES isbe_committees(id),
    candidate_id    INTEGER REFERENCES isbe_candidates(id)
);
CREATE INDEX idx_isbe_cc_committee ON isbe_candidate_committees(committee_id);
CREATE INDEX idx_isbe_cc_candidate ON isbe_candidate_committees(candidate_id);
CREATE INDEX idx_isbe_cc_candidate_committee ON isbe_candidate_committees(candidate_id, committee_id);

CREATE TABLE isbe_candidacies (
    id              INTEGER PRIMARY KEY,
    candidate_id    INTEGER REFERENCES isbe_candidates(id),
    election_type   TEXT,
    election_year   INTEGER,
    race_type       isbe_candidacy_race_type,
    outcome         isbe_candidacy_outcome,
    fair_campaign   BOOLEAN DEFAULT FALSE,
    limits_off      BOOLEAN DEFAULT FALSE,
    limits_off_reason TEXT
);
CREATE INDEX idx_isbe_candidacies_candidate ON isbe_candidacies(candidate_id);

CREATE TABLE isbe_officers (
    id                  INTEGER PRIMARY KEY,
    committee_id        INTEGER,
    last_name           TEXT,
    first_name          TEXT,
    address1            TEXT,
    address2            TEXT,
    city                TEXT,
    state               TEXT,
    zipcode             TEXT,
    title               TEXT,
    phone               TEXT,
    resign_date         DATE,
    redaction_requested BOOLEAN DEFAULT FALSE,
    current             BOOLEAN DEFAULT TRUE
);

-- Staging table for PrevOfficers (merged into isbe_officers after load)
CREATE TABLE isbe_prev_officers (
    id                  INTEGER PRIMARY KEY,
    committee_id        INTEGER,
    last_name           TEXT,
    first_name          TEXT,
    address1            TEXT,
    address2            TEXT,
    city                TEXT,
    state               TEXT,
    zipcode             TEXT,
    title               TEXT,
    resign_date         DATE,
    redaction_requested BOOLEAN DEFAULT FALSE
);

CREATE TABLE isbe_officer_committees (
    id              INTEGER PRIMARY KEY,
    committee_id    INTEGER REFERENCES isbe_committees(id),
    officer_id      INTEGER
);

CREATE TABLE isbe_investments (
    id              INTEGER PRIMARY KEY,
    committee_id    INTEGER REFERENCES isbe_committees(id),
    filed_doc_id    INTEGER REFERENCES isbe_filed_docs(id),
    description     TEXT,
    purchase_date   DATE,
    purchase_shares DOUBLE PRECISION,
    purchase_price  DOUBLE PRECISION,
    current_value   DOUBLE PRECISION,
    liquid_value    DOUBLE PRECISION,
    liquid_date     DATE,
    last_name       TEXT,
    first_name      TEXT,
    address1        TEXT,
    address2        TEXT,
    city            TEXT,
    state           TEXT,
    zipcode         TEXT,
    archived        BOOLEAN DEFAULT FALSE,
    country         TEXT
);
"""

# ---------------------------------------------------------------------------
# Materialized view DDL (from illinois-sunshine, adapted with isbe_ prefix)
# ---------------------------------------------------------------------------
MATVIEW_DDL = """
-- Most recent filing per committee (non-amendment, non-admin docs)
CREATE MATERIALIZED VIEW isbe_most_recent_filings AS (
  SELECT
    COALESCE(d2.end_funds_available, 0) AS end_funds_available,
    COALESCE(d2.total_investments, 0) AS total_investments,
    COALESCE(d2.total_debts, 0) AS total_debts,
    COALESCE((d2.inkind_itemized + d2.inkind_non_itemized), 0) AS total_inkind,
    cm.name AS committee_name,
    cm.id AS committee_id,
    cm.type AS committee_type,
    cm.active AS committee_active,
    fd.id AS filed_doc_id,
    fd.doc_name,
    fd.reporting_period_end,
    fd.reporting_period_begin,
    fd.received_datetime
  FROM isbe_committees AS cm
  LEFT JOIN (
    SELECT DISTINCT ON (committee_id)
      f.*
    FROM (
      SELECT DISTINCT ON (committee_id, reporting_period_end)
        id, committee_id, doc_name,
        reporting_period_end, reporting_period_begin, received_datetime
      FROM isbe_filed_docs
      WHERE doc_name NOT IN ('A-1', 'Statement of Organization',
                             'Letter/Correspondence', 'B-1', 'Nonparticipation')
      ORDER BY committee_id, reporting_period_end DESC, received_datetime DESC
    ) AS f
    ORDER BY f.committee_id, f.reporting_period_end DESC
  ) AS fd ON fd.committee_id = cm.id
  LEFT JOIN isbe_d2_reports AS d2 ON fd.id = d2.filed_doc_id
);
CREATE UNIQUE INDEX ON isbe_most_recent_filings (committee_id);

-- Condensed receipts: deduplicates amended filings
CREATE MATERIALIZED VIEW isbe_condensed_receipts AS (
  (
    SELECT r.* FROM isbe_receipts AS r
    LEFT JOIN isbe_most_recent_filings AS m USING(committee_id)
    WHERE r.received_date > COALESCE(m.reporting_period_end, '1900-01-01')
  ) UNION (
    SELECT r.* FROM isbe_receipts AS r
    JOIN (
      SELECT DISTINCT ON (reporting_period_begin, reporting_period_end, committee_id)
        id AS filed_doc_id
      FROM isbe_filed_docs
      WHERE doc_name != 'Pre-election'
      ORDER BY reporting_period_begin, reporting_period_end, committee_id, received_datetime DESC
    ) AS f USING(filed_doc_id)
  )
);
CREATE UNIQUE INDEX ON isbe_condensed_receipts (id);

-- Condensed expenditures: deduplicates amended filings
CREATE MATERIALIZED VIEW isbe_condensed_expenditures AS (
  (
    SELECT e.* FROM isbe_expenditures AS e
    JOIN isbe_most_recent_filings AS m USING(committee_id)
    WHERE e.expended_date > COALESCE(m.reporting_period_end, '1900-01-01')
  ) UNION (
    SELECT e.* FROM isbe_expenditures AS e
    JOIN (
      SELECT DISTINCT ON (reporting_period_begin, reporting_period_end, committee_id)
        id AS filed_doc_id
      FROM isbe_filed_docs
      WHERE doc_name != 'Pre-election'
      ORDER BY reporting_period_begin, reporting_period_end, committee_id, received_datetime DESC
    ) AS f USING(filed_doc_id)
  )
);
CREATE UNIQUE INDEX ON isbe_condensed_expenditures (id);

-- Committee money totals
CREATE MATERIALIZED VIEW isbe_committee_money AS (
  SELECT
    MAX(filings.end_funds_available) AS end_funds_available,
    MAX(filings.total_inkind) AS total_inkind,
    MAX(filings.committee_name) AS committee_name,
    MAX(filings.committee_id) AS committee_id,
    MAX(filings.committee_type) AS committee_type,
    bool_and(filings.committee_active) AS committee_active,
    MAX(filings.doc_name) AS doc_name,
    MAX(filings.reporting_period_end) AS reporting_period_end,
    MAX(filings.reporting_period_begin) AS reporting_period_begin,
    (SUM(COALESCE(receipts.amount, 0)) +
     MAX(COALESCE(filings.end_funds_available, 0)) +
     MAX(COALESCE(filings.total_investments, 0)) -
     MAX(COALESCE(filings.total_debts, 0))) AS total,
    MAX(receipts.received_date) AS last_receipt_date
  FROM isbe_most_recent_filings AS filings
  LEFT JOIN isbe_receipts AS receipts
    ON receipts.committee_id = filings.committee_id
    AND receipts.received_date > filings.reporting_period_end
  GROUP BY filings.committee_id
  ORDER BY total DESC NULLS LAST
);
CREATE UNIQUE INDEX ON isbe_committee_money (committee_id);

-- Candidate money totals
CREATE MATERIALIZED VIEW isbe_candidate_money AS (
  SELECT
    cd.id AS candidate_id,
    cd.first_name AS candidate_first_name,
    cd.last_name AS candidate_last_name,
    cd.office AS candidate_office,
    cm.id AS committee_id,
    cm.name AS committee_name,
    cm.type AS committee_type,
    m.total,
    m.last_receipt_date
  FROM isbe_candidates AS cd
  JOIN isbe_candidate_committees AS cc ON cd.id = cc.candidate_id
  JOIN isbe_committees AS cm ON cc.committee_id = cm.id
  JOIN isbe_committee_money AS m ON cm.id = m.committee_id
  ORDER BY m.total DESC NULLS LAST
);
CREATE UNIQUE INDEX ON isbe_candidate_money (candidate_id, committee_id);
"""

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def connect():
    """Return a psycopg connection."""
    return psycopg.connect(DB_URL)


def find_file(bulk_dir: Path, stem: str) -> Path | None:
    """Find ISBE file by stem, handling our timestamped names."""
    # Exact match first
    exact = bulk_dir / stem
    if exact.exists():
        return exact
    # Timestamped pattern: e.g. receipts_639060048226403871.txt
    prefix = stem.replace(".txt", "").lower()
    for f in sorted(bulk_dir.glob("*.txt")):
        if f.name.lower().startswith(prefix):
            return f
    return None


def download_file(filename: str, dest: Path):
    """Download a single ISBE bulk file."""
    url = f"{ISBE_BASE_URL}/{filename}"
    print(f"  Downloading {url} → {dest}")
    urllib.request.urlretrieve(url, dest)


def parse_bool(val: str) -> bool | None:
    """Parse ISBE boolean-like values."""
    if not val or val.strip() == "":
        return None
    v = val.strip().upper()
    if v in ("1", "TRUE", "YES", "Y"):
        return True
    if v in ("0", "FALSE", "NO", "N"):
        return False
    return None


def parse_float(val: str) -> float | None:
    if not val or val.strip() == "":
        return None
    try:
        return float(val.strip().replace(",", ""))
    except ValueError:
        return None


def parse_int(val: str) -> int | None:
    if not val or val.strip() == "":
        return None
    try:
        return int(float(val.strip().replace(",", "")))
    except ValueError:
        return None


def parse_date(val: str) -> str | None:
    """Return ISO date string or None."""
    if not val or val.strip() == "":
        return None
    v = val.strip()
    # Handle various date formats
    for fmt in ("%m/%d/%Y", "%Y-%m-%d", "%m/%d/%Y %H:%M:%S",
                "%m/%d/%Y %I:%M:%S %p", "%Y-%m-%d %H:%M:%S"):
        try:
            from datetime import datetime
            dt = datetime.strptime(v, fmt)
            return dt.strftime("%Y-%m-%d")
        except ValueError:
            continue
    return None


def parse_datetime(val: str) -> str | None:
    """Return ISO datetime string or None."""
    if not val or val.strip() == "":
        return None
    v = val.strip()
    for fmt in ("%m/%d/%Y %H:%M:%S", "%m/%d/%Y %I:%M:%S %p",
                "%Y-%m-%d %H:%M:%S", "%m/%d/%Y"):
        try:
            from datetime import datetime
            dt = datetime.strptime(v, fmt)
            return dt.isoformat()
        except ValueError:
            continue
    return None


def clean_text(val: str) -> str | None:
    """Clean text value: strip whitespace, convert empty to None."""
    if val is None:
        return None
    v = val.strip()
    return v if v else None


# ---------------------------------------------------------------------------
# Transform functions — convert raw ISBE row dicts to clean tuples
# ---------------------------------------------------------------------------

def transform_committees(row: dict) -> tuple:
    status = row.get("status", "")
    active = status.strip().upper() == "A" if status else True
    # Map candidate/policy position
    def map_position(val):
        if not val:
            return None
        v = val.strip().upper()
        if v == "S":
            return "support"
        if v == "O":
            return "oppose"
        return None

    return (
        parse_int(row["id"]),
        clean_text(row.get("type_of_committee")),
        parse_bool(row.get("state_committee")),
        parse_bool(row.get("local_committee")),
        clean_text(row.get("refer_name")),
        clean_text(row.get("name")),
        clean_text(row.get("address1")),
        clean_text(row.get("address2")),
        clean_text(row.get("address3")),
        clean_text(row.get("city")),
        clean_text(row.get("state")),
        clean_text(row.get("zipcode")),
        active,
        parse_date(row.get("status_date")),
        parse_date(row.get("creation_date")),
        parse_float(row.get("creation_amount")),
        clean_text(row.get("disp_funds_return")),
        clean_text(row.get("disp_funds_political_committee")),
        clean_text(row.get("disp_funds_charity")),
        clean_text(row.get("disp_funds_95")),
        map_position(row.get("candidate_position")),
        map_position(row.get("policy_position")),
        clean_text(row.get("party_affiliation")),
        clean_text(row.get("purpose")),
    )


def transform_candidates(row: dict) -> tuple:
    return (
        parse_int(row["id"]),
        clean_text(row.get("last_name")),
        clean_text(row.get("first_name")),
        clean_text(row.get("address1")),
        clean_text(row.get("address2")),
        clean_text(row.get("city")),
        clean_text(row.get("state")),
        clean_text(row.get("zipcode")),
        clean_text(row.get("office")),
        clean_text(row.get("district_type")),
        clean_text(row.get("district")),
        clean_text(row.get("residence_county")),
        clean_text(row.get("party_affiliation")),
        parse_bool(row.get("redaction_requested")) or False,
    )


def transform_receipts(row: dict) -> tuple:
    return (
        parse_int(row["id"]),
        parse_int(row.get("committee_id")),
        parse_int(row.get("filed_doc_id")),
        clean_text(row.get("etrans_id")),
        clean_text(row.get("last_name")),
        clean_text(row.get("first_name")),
        parse_date(row.get("received_date")),
        parse_float(row.get("amount")),
        parse_float(row.get("aggregate_amount")),
        parse_float(row.get("loan_amount")),
        clean_text(row.get("occupation")),
        clean_text(row.get("employer")),
        clean_text(row.get("address1")),
        clean_text(row.get("address2")),
        clean_text(row.get("city")),
        clean_text(row.get("state")),
        clean_text(row.get("zipcode")),
        clean_text(row.get("d2_part")),
        clean_text(row.get("description")),
        clean_text(row.get("vendor_last_name")),
        clean_text(row.get("vendor_first_name")),
        clean_text(row.get("vendor_address1")),
        clean_text(row.get("vendor_address2")),
        clean_text(row.get("vendor_city")),
        clean_text(row.get("vendor_state")),
        clean_text(row.get("vendor_zipcode")),
        parse_bool(row.get("archived")) or False,
        clean_text(row.get("country")),
        parse_bool(row.get("redaction_requested")) or False,
    )


def transform_expenditures(row: dict) -> tuple:
    return (
        parse_int(row["id"]),
        parse_int(row.get("committee_id")),
        parse_int(row.get("filed_doc_id")),
        clean_text(row.get("etrans_id")),
        clean_text(row.get("last_name")),
        clean_text(row.get("first_name")),
        parse_date(row.get("expended_date")),
        parse_float(row.get("amount")),
        parse_float(row.get("aggregate_amount")),
        clean_text(row.get("address1")),
        clean_text(row.get("address2")),
        clean_text(row.get("city")),
        clean_text(row.get("state")),
        clean_text(row.get("zipcode")),
        clean_text(row.get("d2_part")),
        clean_text(row.get("purpose")),
        clean_text(row.get("candidate_name")),
        clean_text(row.get("office")),
        parse_bool(row.get("supporting")) or False,
        parse_bool(row.get("opposing")) or False,
        parse_bool(row.get("archived")) or False,
        clean_text(row.get("country")),
        parse_bool(row.get("redaction_requested")) or False,
    )


def transform_d2_reports(row: dict) -> tuple:
    # D2 has complex column mapping — handle actual ISBE names directly
    # Itemized fields: XferInI, LoanRcvI, OtherRctI, InKindI, ExpendI, etc.
    # We sum I+NI pairs into single fields where our schema expects one value
    def _sum_pair(key_i, key_ni):
        v1 = parse_float(row.get(key_i)) or 0.0
        v2 = parse_float(row.get(key_ni)) or 0.0
        return v1 + v2 if (row.get(key_i) or row.get(key_ni)) else None

    return (
        parse_int(row["id"]),
        parse_int(row.get("committee_id")),
        parse_int(row.get("filed_doc_id")),
        parse_float(row.get("beginning_funds_avail")),
        parse_float(row.get("individual_itemized")) or parse_float(row.get("indivcontribi")),
        parse_float(row.get("individual_non_itemized")) or parse_float(row.get("indivcontribni")),
        _sum_pair("xferini", "xferinni") if not row.get("transfer_in") else parse_float(row.get("transfer_in")),
        _sum_pair("loanrcvi", "loanrcvni") if not row.get("loan_received") else parse_float(row.get("loan_received")),
        _sum_pair("otherrcti", "otherrctni") if not row.get("other_receipts") else parse_float(row.get("other_receipts")),
        parse_float(row.get("total_receipts")) or parse_float(row.get("totalreceipts")),
        parse_float(row.get("inkind_itemized")) or parse_float(row.get("inkindi")),
        parse_float(row.get("inkind_non_itemized")) or parse_float(row.get("inkindni")),
        parse_float(row.get("total_inkind")) or parse_float(row.get("totalinkind")),
        parse_float(row.get("expenditures_itemized")) or parse_float(row.get("expendi")),
        parse_float(row.get("expenditures_non_itemized")) or parse_float(row.get("expendni")),
        parse_float(row.get("independent_expenditures_itemized")) or parse_float(row.get("independentexpi")),
        parse_float(row.get("independent_expenditures_non_itemized")) or parse_float(row.get("independentexpni")),
        parse_float(row.get("total_expenditures")) or parse_float(row.get("totalexpend")),
        parse_float(row.get("debts_itemized")) or parse_float(row.get("debtsi")),
        parse_float(row.get("debts_non_itemized")) or parse_float(row.get("debtsni")),
        parse_float(row.get("total_debts")) or parse_float(row.get("totaldebts")),
        parse_float(row.get("total_investments")) or parse_float(row.get("totalinvest")),
        parse_float(row.get("end_funds_available")) or parse_float(row.get("endfundsavail")),
        parse_bool(row.get("archived")) or False,
    )


def transform_candidate_committees(row: dict) -> tuple:
    return (
        parse_int(row["id"]),
        parse_int(row.get("committee_id")),
        parse_int(row.get("candidate_id")),
    )


def transform_filed_docs(row: dict) -> tuple:
    amended = row.get("amended", "")
    is_amended = amended.strip().upper() in ("1", "TRUE", "YES", "Y") if amended else False

    return (
        parse_int(row["id"]),
        parse_int(row.get("committee_id")),
        clean_text(row.get("filed_doc_type")),
        clean_text(row.get("doc_name")),
        is_amended,
        clean_text(row.get("comment")),
        parse_int(row.get("pages")),
        clean_text(row.get("election_type")),
        parse_int(row.get("election_year")),
        parse_date(row.get("reporting_period_begin")),
        parse_date(row.get("reporting_period_end")),
        parse_date(row.get("received_at")),
        parse_datetime(row.get("received_datetime")),
        clean_text(row.get("source")),
        clean_text(row.get("provider")),
        clean_text(row.get("signer_last_name")),
        clean_text(row.get("signer_first_name")),
        clean_text(row.get("submitter_last_name")),
        clean_text(row.get("submitter_first_name")),
        clean_text(row.get("submitter_address1")),
        clean_text(row.get("submitter_address2")),
        clean_text(row.get("submitter_city")),
        clean_text(row.get("submitter_state")),
        clean_text(row.get("submitter_zipcode")),
        clean_text(row.get("b9_signer_last_name")),
        clean_text(row.get("b9_signer_first_name")),
        parse_bool(row.get("archived")) or False,
        clean_text(row.get("clarification")),
        parse_bool(row.get("redaction_requested")) or False,
    )


def transform_candidacies(row: dict) -> tuple:
    # Map election type codes
    et_map = {"CE": "Consolidated Election", "GP": "General Primary",
              "GE": "General Election", "CP": "Consolidated Primary",
              "SE": "Special Election", "SP": "Special Primary",
              "NE": "Nonpartisan Election"}
    et_raw = clean_text(row.get("election_type"))
    election_type = et_map.get(et_raw, et_raw) if et_raw else None

    # Map race type
    rt_map = {"Inc": "incumbent", "Chal": "challenger",
              "Open": "open seat", "Ret": "retired"}
    rt_raw = clean_text(row.get("inc_chall_open"))
    race_type = rt_map.get(rt_raw, None) if rt_raw else None

    # Map outcome
    oc_map = {"Won": "won", "Lost": "lost"}
    oc_raw = clean_text(row.get("won_lost"))
    outcome = oc_map.get(oc_raw, None) if oc_raw else None

    return (
        parse_int(row["id"]),
        parse_int(row.get("candidate_id")),
        election_type,
        parse_int(row.get("election_year")),
        race_type,
        outcome,
        parse_bool(row.get("fair_campaign")) or False,
        parse_bool(row.get("limits_off")) or False,
        clean_text(row.get("limits_off_reason")),
    )


def transform_officers(row: dict) -> tuple:
    """Officers.txt — current officers (no committee_id in this file)."""
    return (
        parse_int(row["id"]),
        None,  # committee_id filled from CmteOfficerLinks
        clean_text(row.get("last_name")),
        clean_text(row.get("first_name")),
        clean_text(row.get("address1")),
        clean_text(row.get("address2")),
        clean_text(row.get("city")),
        clean_text(row.get("state")),
        clean_text(row.get("zipcode")),
        clean_text(row.get("title")),
        clean_text(row.get("phone")),
        None,  # resign_date
        parse_bool(row.get("redaction_requested")) or False,
        True,  # current
    )


def transform_prev_officers(row: dict) -> tuple:
    """PrevOfficers.txt staging table."""
    return (
        parse_int(row["id"]),
        parse_int(row.get("committee_id")),
        clean_text(row.get("last_name")),
        clean_text(row.get("first_name")),
        clean_text(row.get("address1")),
        clean_text(row.get("address2")),
        clean_text(row.get("city")),
        clean_text(row.get("state")),
        clean_text(row.get("zipcode")),
        clean_text(row.get("title")),
        parse_date(row.get("resign_date")),
        parse_bool(row.get("redaction_requested")) or False,
    )


def transform_officer_committees(row: dict) -> tuple:
    return (
        parse_int(row["id"]),
        parse_int(row.get("committee_id")),
        parse_int(row.get("officer_id")),
    )


def transform_investments(row: dict) -> tuple:
    return (
        parse_int(row["id"]),
        parse_int(row.get("committee_id")),
        parse_int(row.get("filed_doc_id")),
        clean_text(row.get("description")),
        parse_date(row.get("purchase_date")),
        parse_float(row.get("purchase_shares")),
        parse_float(row.get("purchase_price")),
        parse_float(row.get("current_value")),
        parse_float(row.get("liquid_value")),
        parse_date(row.get("liquid_date")),
        clean_text(row.get("last_name")),
        clean_text(row.get("first_name")),
        clean_text(row.get("address1")),
        clean_text(row.get("address2")),
        clean_text(row.get("city")),
        clean_text(row.get("state")),
        clean_text(row.get("zipcode")),
        parse_bool(row.get("archived")) or False,
        clean_text(row.get("country")),
    )


# Map filename → transform function
TRANSFORMS = {
    "Committees.txt": transform_committees,
    "Candidates.txt": transform_candidates,
    "Receipts.txt": transform_receipts,
    "Expenditures.txt": transform_expenditures,
    "D2Totals.txt": transform_d2_reports,
    "CmteCandidateLinks.txt": transform_candidate_committees,
    "FiledDocs.txt": transform_filed_docs,
    "CanElections.txt": transform_candidacies,
    "Officers.txt": transform_officers,
    "PrevOfficers.txt": transform_prev_officers,
    "CmteOfficerLinks.txt": transform_officer_committees,
    "Investments.txt": transform_investments,
}

# Table column lists for INSERT (must match transform output order)
TABLE_COLUMNS = {
    "isbe_committees": [
        "id", "type", "state_committee", "local_committee", "refer_name",
        "name", "address1", "address2", "address3", "city", "state", "zipcode",
        "active", "status_date", "creation_date", "creation_amount",
        "disp_funds_return", "disp_funds_political_committee",
        "disp_funds_charity", "disp_funds_95",
        "candidate_position", "policy_position", "party", "purpose",
    ],
    "isbe_candidates": [
        "id", "last_name", "first_name", "address1", "address2",
        "city", "state", "zipcode", "office", "district_type",
        "district", "residence_county", "party", "redaction_requested",
    ],
    "isbe_receipts": [
        "id", "committee_id", "filed_doc_id", "etrans_id",
        "last_name", "first_name", "received_date", "amount",
        "aggregate_amount", "loan_amount", "occupation", "employer",
        "address1", "address2", "city", "state", "zipcode",
        "d2_part", "description",
        "vendor_last_name", "vendor_first_name",
        "vendor_address1", "vendor_address2",
        "vendor_city", "vendor_state", "vendor_zipcode",
        "archived", "country", "redaction_requested",
    ],
    "isbe_expenditures": [
        "id", "committee_id", "filed_doc_id", "etrans_id",
        "last_name", "first_name", "expended_date", "amount",
        "aggregate_amount", "address1", "address2",
        "city", "state", "zipcode", "d2_part", "purpose",
        "candidate_name", "office", "supporting", "opposing",
        "archived", "country", "redaction_requested",
    ],
    "isbe_d2_reports": [
        "id", "committee_id", "filed_doc_id",
        "beginning_funds_avail", "individual_itemized",
        "individual_non_itemized", "transfer_in", "loan_received",
        "other_receipts", "total_receipts",
        "inkind_itemized", "inkind_non_itemized", "total_inkind",
        "expenditures_itemized", "expenditures_non_itemized",
        "independent_expenditures_itemized", "independent_expenditures_non_itemized",
        "total_expenditures", "debts_itemized", "debts_non_itemized",
        "total_debts", "total_investments", "end_funds_available",
        "archived",
    ],
    "isbe_candidate_committees": ["id", "committee_id", "candidate_id"],
    "isbe_filed_docs": [
        "id", "committee_id", "filed_doc_type", "doc_name", "amended",
        "comment", "pages", "election_type", "election_year",
        "reporting_period_begin", "reporting_period_end",
        "received_at", "received_datetime",
        "source", "provider",
        "signer_last_name", "signer_first_name",
        "submitter_last_name", "submitter_first_name",
        "submitter_address1", "submitter_address2",
        "submitter_city", "submitter_state", "submitter_zipcode",
        "b9_signer_last_name", "b9_signer_first_name",
        "archived", "clarification", "redaction_requested",
    ],
    "isbe_candidacies": [
        "id", "candidate_id", "election_type", "election_year",
        "race_type", "outcome", "fair_campaign", "limits_off", "limits_off_reason",
    ],
    "isbe_officers": [
        "id", "committee_id", "last_name", "first_name",
        "address1", "address2", "city", "state", "zipcode",
        "title", "phone", "resign_date", "redaction_requested", "current",
    ],
    "isbe_prev_officers": [
        "id", "committee_id", "last_name", "first_name",
        "address1", "address2", "city", "state", "zipcode",
        "title", "resign_date", "redaction_requested",
    ],
    "isbe_officer_committees": ["id", "committee_id", "officer_id"],
    "isbe_investments": [
        "id", "committee_id", "filed_doc_id", "description",
        "purchase_date", "purchase_shares", "purchase_price",
        "current_value", "liquid_value", "liquid_date",
        "last_name", "first_name",
        "address1", "address2", "city", "state", "zipcode",
        "archived", "country",
    ],
}

# Load order matters for FK constraints
LOAD_ORDER = [
    "Committees.txt",
    "Candidates.txt",
    "FiledDocs.txt",
    "Receipts.txt",
    "Expenditures.txt",
    "D2Totals.txt",
    "CmteCandidateLinks.txt",
    "CanElections.txt",
    "Officers.txt",
    "PrevOfficers.txt",
    "CmteOfficerLinks.txt",
    "Investments.txt",
]

# ---------------------------------------------------------------------------
# Core ETL functions
# ---------------------------------------------------------------------------

def create_schema(conn):
    """Create all isbe_* tables (drops existing)."""
    print("Creating isbe_* schema...")
    with conn.cursor() as cur:
        cur.execute(SCHEMA_DDL)
    conn.commit()
    print("  Schema created.")


def load_file(conn, filename: str, filepath: Path, batch_size: int = 100000):
    """Load one ISBE file into its target table using COPY for speed."""
    table = ISBE_FILES[filename]
    transform = TRANSFORMS[filename]
    columns = TABLE_COLUMNS[table]

    print(f"  Loading {filename} → {table}...")
    t0 = time.time()

    col_list = ", ".join(columns)
    row_count = 0
    error_count = 0

    with open(filepath, "r", encoding="utf-8", errors="replace") as f:
        reader = csv.DictReader(f, delimiter="\t")
        col_map = _build_column_map(filename, reader.fieldnames)

        batch = []
        for raw_row in reader:
            row = {col_map.get(k, k): v for k, v in raw_row.items()}
            try:
                record = transform(row)
                if record[0] is None:
                    continue
                batch.append(record)
                row_count += 1
            except (ValueError, KeyError, TypeError) as e:
                error_count += 1
                if error_count <= 5:
                    print(f"    Warning: row error in {filename}: {e}")
                continue

            if len(batch) >= batch_size:
                _copy_batch(conn, table, columns, batch)
                batch = []
                if row_count % 500000 == 0:
                    elapsed = time.time() - t0
                    print(f"    ... {row_count:,} rows ({elapsed:.0f}s)")

        if batch:
            _copy_batch(conn, table, columns, batch)

    elapsed = time.time() - t0
    rate = row_count / elapsed if elapsed > 0 else 0
    print(f"    {row_count:,} rows loaded in {elapsed:.1f}s ({rate:,.0f} rows/s)"
          f" | {error_count} errors")
    return row_count


def _copy_batch(conn, table: str, columns: list, batch: list):
    """Use COPY FROM for fast bulk insert via psycopg3."""
    col_list = ", ".join(columns)
    # Build CSV buffer
    buf = io.StringIO()
    writer = csv.writer(buf)
    for row in batch:
        writer.writerow([_pg_val(v) for v in row])
    buf.seek(0)

    with conn.cursor() as cur:
        try:
            with cur.copy(f"COPY {table} ({col_list}) FROM STDIN WITH (FORMAT CSV, NULL '\\N')") as copy:
                while data := buf.read(8192):
                    copy.write(data.encode("utf-8"))
            conn.commit()
        except Exception as e:
            conn.rollback()
            # Fallback to executemany for this batch
            placeholders = ", ".join(["%s"] * len(columns))
            insert_sql = f"INSERT INTO {table} ({col_list}) VALUES ({placeholders}) ON CONFLICT DO NOTHING"
            ok = 0
            for row in batch:
                try:
                    cur.execute(insert_sql, row)
                    conn.commit()
                    ok += 1
                except Exception:
                    conn.rollback()
            if ok < len(batch):
                print(f"    Batch fallback: {ok}/{len(batch)} rows inserted ({e})")


def _pg_val(v):
    """Convert Python value to CSV-safe PostgreSQL value."""
    if v is None:
        return "\\N"
    if isinstance(v, bool):
        return "t" if v else "f"
    return str(v)


def _build_column_map(filename: str, fieldnames: list[str]) -> dict:
    """Map raw ISBE column headers to our expected names."""
    expected = FILE_COLUMNS[filename]
    mapping = {}

    if fieldnames is None:
        return mapping

    # Clean fieldnames (remove BOM, whitespace)
    clean_fields = [f.strip().lstrip('\ufeff') for f in fieldnames]

    # Known ISBE header → our column name mappings
    isbe_to_ours = {
        "ID": "id",
        "CommitteeID": "committee_id",
        "CandidateID": "candidate_id",
        "OfficerID": "officer_id",
        "FiledDocID": "filed_doc_id",
        "LastName": "last_name", "LastOnlyName": "last_name",
        "FirstName": "first_name",
        "Address1": "address1", "Address2": "address2", "Address3": "address3",
        "City": "city", "State": "state", "Zip": "zipcode",
        "Office": "office",
        "DistrictType": "district_type", "District": "district",
        "ResidenceCounty": "residence_county",
        "PartyAffiliation": "party_affiliation",
        "RedactionRequested": "redaction_requested",
        "TypeOfCommittee": "type_of_committee",
        "StateCommittee": "state_committee", "LocalCommittee": "local_committee",
        "ReferName": "refer_name", "Name": "name",
        "Status": "status", "StatusDate": "status_date",
        "CreationDate": "creation_date", "CreationAmount": "creation_amount",
        "DispFundsReturn": "disp_funds_return",
        "DispFundsPoliticalCommittee": "disp_funds_political_committee",
        "DispFundsPolComm": "disp_funds_political_committee",
        "DispFundsDescrip": "disp_funds_95",  # maps to same field
        "DispFundsCharity": "disp_funds_charity",
        "DispFunds95": "disp_funds_95",
        "CanSuppOpp": "candidate_position", "PolicySuppOpp": "policy_position",
        "Purpose": "purpose",
        "ETransID": "etrans_id",
        "RcvDate": "received_date", "RcvdDate": "received_date", "Amount": "amount",
        "AggregateAmount": "aggregate_amount", "LoanAmount": "loan_amount",
        "Occupation": "occupation", "Employer": "employer",
        "D2Part": "d2_part", "Description": "description",
        "VendorLastOnlyName": "vendor_last_name", "VendorFirstName": "vendor_first_name",
        "VendorAddress1": "vendor_address1", "VendorAddress2": "vendor_address2",
        "VendorCity": "vendor_city", "VendorState": "vendor_state",
        "VendorZip": "vendor_zipcode",
        "Archived": "archived", "Country": "country",
        "ExpendedDate": "expended_date",
        "CandidateName": "candidate_name",
        "Supporting": "supporting", "Opposing": "opposing",
        "FiledDocType": "filed_doc_type", "DocName": "doc_name",
        "Amend": "amended", "Comment": "comment", "Pages": "pages",
        "ElectionType": "election_type", "ElectionYear": "election_year",
        "RptPdBegDate": "reporting_period_begin", "RptPdEndDate": "reporting_period_end",
        "RcvdAt": "received_at", "RcvdDateTime": "received_datetime",
        "Source": "source", "Provider": "provider",
        "SignerLastOnlyName": "signer_last_name", "SignerFirstName": "signer_first_name",
        "SbmttrLastOnlyName": "submitter_last_name", "SbmttrFirstName": "submitter_first_name",
        "SbmttrAddress1": "submitter_address1", "SbmttrAddress2": "submitter_address2",
        "SbmttrCity": "submitter_city", "SbmttrState": "submitter_state",
        "SbmttrZip": "submitter_zipcode",
        "B9SignerLastOnlyName": "b9_signer_last_name", "B9SignerFirstName": "b9_signer_first_name",
        "Clarification": "clarification",
        "IncChallOpen": "inc_chall_open", "WonLost": "won_lost",
        "FairCampaign": "fair_campaign", "LimitsOff": "limits_off",
        "LimitsOffReason": "limits_off_reason",
        "Title": "title", "Phone": "phone", "ResignDate": "resign_date",
        "PurchaseDate": "purchase_date", "PurchaseShares": "purchase_shares",
        "PurchasePrice": "purchase_price", "CurrentValue": "current_value",
        "LiquidValue": "liquid_value", "LiquidDate": "liquid_date",
        # D2 fields — actual ISBE headers use abbreviated names
        "BegFundsAvail": "beginning_funds_avail",
        "IndivContribI": "individual_itemized",
        "IndivContribNI": "individual_non_itemized",
        "IndivContItemized": "individual_itemized",
        "IndivContNonItemized": "individual_non_itemized",
        "XferInI": "transfer_in",
        "XferInNI": "transfer_in",  # combined into single field
        "XferIn": "transfer_in",
        "LoanRcvI": "loan_received",
        "LoanRcvNI": "loan_received",
        "LoanRcvd": "loan_received",
        "OtherRctI": "other_receipts",
        "OtherRctNI": "other_receipts",
        "OtherRctItemized": "other_receipts",
        "OtherReceipts": "other_receipts",
        "TotalReceipts": "total_receipts",
        "InKindI": "inkind_itemized",
        "InKindNI": "inkind_non_itemized",
        "InkindItemized": "inkind_itemized",
        "InkindNonItemized": "inkind_non_itemized",
        "TotalInKind": "total_inkind",
        "TotalInkind": "total_inkind",
        "XferOutI": "expenditures_itemized",   # transfers out are in expenditures section
        "XferOutNI": "expenditures_non_itemized",
        "LoanMadeI": "expenditures_itemized",
        "LoanMadeNI": "expenditures_non_itemized",
        "ExpendI": "expenditures_itemized",
        "ExpendNI": "expenditures_non_itemized",
        "ExpdItemized": "expenditures_itemized",
        "ExpdNonItemized": "expenditures_non_itemized",
        "IndependentExpI": "independent_expenditures_itemized",
        "IndependentExpNI": "independent_expenditures_non_itemized",
        "IndExpdItemized": "independent_expenditures_itemized",
        "IndExpdNonItemized": "independent_expenditures_non_itemized",
        "TotalExpend": "total_expenditures",
        "TotalExpd": "total_expenditures",
        "DebtsI": "debts_itemized",
        "DebtsNI": "debts_non_itemized",
        "DebtsItemized": "debts_itemized",
        "DebtsNonItemized": "debts_non_itemized",
        "TotalDebts": "total_debts",
        "TotalInvest": "total_investments",
        "TotalInvestments": "total_investments",
        "EndFundsAvail": "end_funds_available",
    }

    for raw_name in clean_fields:
        mapped = isbe_to_ours.get(raw_name, raw_name.lower())
        mapping[raw_name] = mapped

    return mapping


def merge_prev_officers(conn):
    """Merge prev_officers into isbe_officers with current=FALSE."""
    print("  Merging PrevOfficers into isbe_officers...")
    with conn.cursor() as cur:
        cur.execute("""
            INSERT INTO isbe_officers
                (id, committee_id, last_name, first_name,
                 address1, address2, city, state, zipcode,
                 title, phone, resign_date, redaction_requested, current)
            SELECT
                id, committee_id, last_name, first_name,
                address1, address2, city, state, zipcode,
                title, NULL, resign_date, redaction_requested, FALSE
            FROM isbe_prev_officers
            ON CONFLICT (id) DO NOTHING
        """)
        merged = cur.rowcount
    conn.commit()
    print(f"    {merged:,} prev officers merged.")


def update_officer_committees(conn):
    """Set committee_id on isbe_officers from CmteOfficerLinks."""
    print("  Updating officer committee_id from links...")
    with conn.cursor() as cur:
        cur.execute("""
            UPDATE isbe_officers o
            SET committee_id = oc.committee_id
            FROM isbe_officer_committees oc
            WHERE oc.officer_id = o.id
              AND o.committee_id IS NULL
        """)
        updated = cur.rowcount
    conn.commit()
    print(f"    {updated:,} officers updated with committee_id.")


def infer_missing_candidate_links(conn):
    """Infer candidate-committee links from refer_name when CmteCandidateLinks is incomplete.

    ISBE's CmteCandidateLinks.txt doesn't always contain rows for every
    candidate-committee pair, especially for newer committees.  The committee
    refer_name field (e.g. 'Atcha, Haroon') can be matched to candidates.
    """
    print("  Inferring missing candidate-committee links from refer_name...")
    with conn.cursor() as cur:
        cur.execute("""
            INSERT INTO isbe_candidate_committees (id, committee_id, candidate_id)
            SELECT (SELECT COALESCE(MAX(id), 0) FROM isbe_candidate_committees) + ROW_NUMBER() OVER (),
                   c.id, ca.id
            FROM (
                SELECT DISTINCT c2.id, ca2.id AS candidate_id
                FROM isbe_committees c2
                JOIN isbe_candidates ca2
                  ON LOWER(TRIM(c2.refer_name)) = LOWER(TRIM(ca2.last_name || ', ' || ca2.first_name))
                LEFT JOIN isbe_candidate_committees cc
                  ON cc.committee_id = c2.id AND cc.candidate_id = ca2.id
                WHERE cc.id IS NULL
                  AND c2.refer_name IS NOT NULL
                  AND c2.refer_name != ''
            ) sub
            JOIN isbe_committees c ON c.id = sub.id
            JOIN isbe_candidates ca ON ca.id = sub.candidate_id
        """)
        inserted = cur.rowcount
    conn.commit()
    print(f"    {inserted:,} inferred links inserted.")


def create_materialized_views(conn):
    """Create all materialized views."""
    print("Creating materialized views...")
    t0 = time.time()
    with conn.cursor() as cur:
        cur.execute(MATVIEW_DDL)
    conn.commit()
    elapsed = time.time() - t0
    print(f"  Materialized views created in {elapsed:.1f}s")


COMPAT_VIEWS_DDL = """
-- Backward-compatible views: isbe_* tables → bulk_*_clean shape
-- Column names MUST match bulk_*_clean exactly so existing routes work.

DROP VIEW IF EXISTS isbe_bulk_receipts_clean_compat CASCADE;
CREATE VIEW isbe_bulk_receipts_clean_compat AS
SELECT
  r.id AS bulk_row_id,
  r.id AS receipt_record_id,
  r.committee_id AS committee_id_sbe,
  r.filed_doc_id,
  r.etrans_id AS electronic_transaction_id,
  r.last_name AS last_or_business_name,
  r.first_name,
  r.received_date::text AS received_date,
  r.amount,
  r.aggregate_amount,
  r.loan_amount,
  r.occupation,
  r.employer,
  r.address1 AS address_line_1,
  r.address2 AS address_line_2,
  r.city, r.state,
  r.zipcode AS postal_code,
  r.d2_part AS d2_part_code,
  r.description,
  r.vendor_last_name AS vendor_last_or_business_name,
  r.vendor_first_name,
  r.vendor_address1 AS vendor_address_line_1,
  r.vendor_address2 AS vendor_address_line_2,
  r.vendor_city,
  r.vendor_state,
  r.vendor_zipcode AS vendor_postal_code,
  CASE WHEN r.archived THEN 1 ELSE 0 END AS is_archived,
  r.country,
  CASE WHEN r.redaction_requested THEN 1 ELSE 0 END AS redaction_requested,
  NULL::text AS source_file,
  NULL::bigint AS source_row_number,
  r.received_date::text AS received_datetime_raw
FROM isbe_condensed_receipts r;

DROP VIEW IF EXISTS isbe_bulk_expenditures_clean_compat CASCADE;
CREATE VIEW isbe_bulk_expenditures_clean_compat AS
SELECT
  e.id AS bulk_row_id,
  e.id AS expenditure_record_id,
  e.committee_id AS committee_id_sbe,
  e.filed_doc_id,
  e.etrans_id AS electronic_transaction_id,
  e.last_name AS payee_last_or_business_name,
  e.first_name AS payee_first_name,
  e.expended_date::text AS expended_date,
  e.amount,
  e.aggregate_amount,
  e.address1 AS address_line_1,
  e.address2 AS address_line_2,
  e.city, e.state,
  e.zipcode AS postal_code,
  e.d2_part AS d2_part_code,
  e.purpose,
  e.candidate_name,
  e.office,
  CASE WHEN e.supporting THEN 1 ELSE 0 END AS is_supporting,
  CASE WHEN e.opposing THEN 1 ELSE 0 END AS is_opposing,
  CASE WHEN e.archived THEN 1 ELSE 0 END AS is_archived,
  e.country,
  CASE WHEN e.redaction_requested THEN 1 ELSE 0 END AS redaction_requested,
  0::integer AS is_amount_anomalous,
  NULL::text AS anomaly_reason,
  NULL::text AS source_file,
  NULL::bigint AS source_row_number
FROM isbe_condensed_expenditures e;

DROP VIEW IF EXISTS isbe_bulk_committees_clean_compat CASCADE;
CREATE VIEW isbe_bulk_committees_clean_compat AS
SELECT
  c.id AS committee_id_sbe,
  c.type AS committee_type,
  0::integer AS is_state_committee_obsolete,
  c.state_committee AS state_committee_id_obsolete,
  0::integer AS is_local_committee_obsolete,
  c.local_committee AS local_committee_id_obsolete,
  c.refer_name AS reference_name,
  c.name AS committee_name,
  c.address1 AS address_line_1,
  c.address2 AS address_line_2,
  c.address3 AS address_line_3,
  c.city, c.state,
  c.zipcode AS postal_code,
  CASE WHEN c.active THEN 'A' ELSE 'F' END AS committee_status_code,
  c.status_date::text AS status_date,
  c.creation_date::text AS creation_date,
  c.creation_amount AS creation_funds_available,
  NULL::text AS residual_funds_return_to_contributors,
  NULL::text AS residual_funds_to_political_committee,
  NULL::text AS residual_funds_to_charity,
  NULL::text AS residual_funds_per_ilcs_9_5,
  NULL::text AS residual_funds_description,
  NULL::text AS candidate_support_or_oppose,
  NULL::text AS policy_support_or_oppose,
  c.party AS party_affiliation,
  c.purpose AS committee_purpose,
  NULL::text AS source_file,
  NULL::bigint AS source_row_number
FROM isbe_committees c;

DROP VIEW IF EXISTS isbe_bulk_candidates_clean_compat CASCADE;
CREATE VIEW isbe_bulk_candidates_clean_compat AS
SELECT
  c.id AS candidate_id,
  c.last_name,
  c.first_name,
  TRIM(COALESCE(c.first_name, '') || ' ' || COALESCE(c.last_name, '')) AS candidate_full_name,
  c.address1 AS address_line_1,
  c.address2 AS address_line_2,
  c.city, c.state,
  c.zipcode AS postal_code,
  c.office AS office_sought,
  c.district_type,
  c.district,
  c.residence_county,
  c.party AS party_affiliation,
  CASE WHEN c.redaction_requested THEN 1 ELSE 0 END AS redaction_requested,
  NULL::text AS source_file,
  NULL::bigint AS source_row_number
FROM isbe_candidates c;

-- Additional compat views for link/aggregation tables

DROP VIEW IF EXISTS isbe_bulk_d2_totals_clean_compat CASCADE;
CREATE VIEW isbe_bulk_d2_totals_clean_compat AS
SELECT
  d.id AS d2_totals_record_id,
  d.committee_id AS committee_id_sbe,
  d.filed_doc_id,
  d.beginning_funds_avail AS beginning_funds_available,
  d.individual_itemized AS individual_contributions_itemized,
  d.individual_non_itemized AS individual_contributions_non_itemized,
  NULL::double precision AS transfers_in_itemized,
  NULL::double precision AS transfers_in_non_itemized,
  NULL::double precision AS loans_received_itemized,
  NULL::double precision AS loans_received_non_itemized,
  NULL::double precision AS other_receipts_itemized,
  NULL::double precision AS other_receipts_non_itemized,
  d.total_receipts,
  d.inkind_itemized AS in_kind_contributions_itemized,
  d.inkind_non_itemized AS in_kind_contributions_non_itemized,
  d.total_inkind AS total_in_kind_contributions,
  NULL::double precision AS transfers_out_itemized,
  NULL::double precision AS transfers_out_non_itemized,
  NULL::double precision AS loans_made_itemized,
  NULL::double precision AS loans_made_non_itemized,
  d.expenditures_itemized,
  d.expenditures_non_itemized,
  d.independent_expenditures_itemized,
  d.independent_expenditures_non_itemized,
  d.total_expenditures,
  d.debts_itemized AS debts_obligations_itemized,
  d.debts_non_itemized AS debts_obligations_non_itemized,
  d.total_debts AS total_debts_obligations,
  d.total_investments,
  d.end_funds_available AS ending_funds_available,
  CASE WHEN d.archived THEN 1 ELSE 0 END AS is_archived,
  NULL::text AS source_file,
  NULL::bigint AS source_row_number
FROM isbe_d2_reports d;

DROP VIEW IF EXISTS isbe_bulk_cmte_candidate_links_clean_compat CASCADE;
CREATE VIEW isbe_bulk_cmte_candidate_links_clean_compat AS
SELECT
  cc.id AS link_record_id,
  cc.committee_id AS committee_id_sbe,
  cc.candidate_id,
  NULL::text AS source_file,
  NULL::bigint AS source_row_number
FROM isbe_candidate_committees cc;

DROP VIEW IF EXISTS isbe_bulk_committee_candidate_links_compat CASCADE;
CREATE VIEW isbe_bulk_committee_candidate_links_compat AS
SELECT
  cc.id AS link_record_id,
  cc.committee_id AS committee_id_sbe,
  cc.candidate_id,
  cm.name AS committee_name,
  cm.refer_name AS reference_name,
  cm.type AS committee_type,
  cm.party AS committee_party_affiliation,
  CASE WHEN cm.active THEN 'A' ELSE 'F' END AS committee_status_code,
  cm.city AS committee_city,
  cm.state AS committee_state,
  cm.zipcode AS committee_postal_code,
  cm.purpose AS committee_purpose,
  ca.last_name,
  ca.first_name,
  TRIM(COALESCE(ca.first_name, '') || ' ' || COALESCE(ca.last_name, '')) AS candidate_full_name,
  ca.office AS office_sought,
  ca.district_type,
  ca.district,
  ca.residence_county,
  ca.party AS candidate_party_affiliation,
  ca.city AS candidate_city,
  ca.state AS candidate_state,
  ca.zipcode AS candidate_postal_code,
  NULL::text AS link_source_file,
  NULL::text AS candidate_source_file,
  NULL::text AS committee_source_file
FROM isbe_candidate_committees cc
LEFT JOIN isbe_committees cm ON cm.id = cc.committee_id
LEFT JOIN isbe_candidates ca ON ca.id = cc.candidate_id;
"""


def create_compat_views(conn):
    """Create backward-compatible views mapping isbe_* → bulk_*_clean shape."""
    print("Creating backward-compatible views...")
    with conn.cursor() as cur:
        cur.execute(COMPAT_VIEWS_DDL)
    conn.commit()
    print("  Compat views created.")


def print_summary(conn):
    """Print row counts for all isbe_* tables and views."""
    tables = [
        "isbe_committees", "isbe_candidates", "isbe_filed_docs",
        "isbe_receipts", "isbe_expenditures", "isbe_d2_reports",
        "isbe_candidate_committees", "isbe_candidacies",
        "isbe_officers", "isbe_officer_committees", "isbe_investments",
    ]
    views = [
        "isbe_most_recent_filings", "isbe_condensed_receipts",
        "isbe_condensed_expenditures", "isbe_committee_money",
        "isbe_candidate_money",
    ]

    print("\n" + "=" * 60)
    print("ISBE Sunshine ETL — Summary")
    print("=" * 60)

    with conn.cursor() as cur:
        for t in tables:
            try:
                cur.execute(f"SELECT COUNT(*) FROM {t}")
                count = cur.fetchone()[0]
                print(f"  {t:45s} {count:>12,}")
            except Exception:
                conn.rollback()
                print(f"  {t:45s} {'(missing)':>12}")

        print("  " + "-" * 57)
        for v in views:
            try:
                cur.execute(f"SELECT COUNT(*) FROM {v}")
                count = cur.fetchone()[0]
                print(f"  {v:45s} {count:>12,}")
            except Exception:
                conn.rollback()
                print(f"  {v:45s} {'(missing)':>12}")

    # Dedup stats
    print("\n  Deduplication stats:")
    with conn.cursor() as cur:
        try:
            cur.execute("SELECT COUNT(*) FROM isbe_receipts")
            raw_r = cur.fetchone()[0]
            cur.execute("SELECT COUNT(*) FROM isbe_condensed_receipts")
            cond_r = cur.fetchone()[0]
            pct_r = (1 - cond_r / raw_r) * 100 if raw_r > 0 else 0
            print(f"    Receipts:     {raw_r:>12,} raw → {cond_r:>12,} condensed ({pct_r:.1f}% removed)")
        except Exception:
            conn.rollback()

        try:
            cur.execute("SELECT COUNT(*) FROM isbe_expenditures")
            raw_e = cur.fetchone()[0]
            cur.execute("SELECT COUNT(*) FROM isbe_condensed_expenditures")
            cond_e = cur.fetchone()[0]
            pct_e = (1 - cond_e / raw_e) * 100 if raw_e > 0 else 0
            print(f"    Expenditures: {raw_e:>12,} raw → {cond_e:>12,} condensed ({pct_e:.1f}% removed)")
        except Exception:
            conn.rollback()


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser(description="ISBE Sunshine ETL for PostgreSQL")
    parser.add_argument("--bulk-dir", default=str(DEFAULT_BULK_DIR),
                        help="Directory containing ISBE bulk .txt files")
    parser.add_argument("--download", action="store_true",
                        help="Download missing files from elections.il.gov")
    parser.add_argument("--tables", default="",
                        help="Comma-separated list of tables to load (default: all)")
    parser.add_argument("--skip-views", action="store_true",
                        help="Skip materialized view creation")
    parser.add_argument("--db-url", default=DB_URL,
                        help=f"PostgreSQL connection URL (default: {DB_URL})")
    args = parser.parse_args()

    bulk_dir = Path(args.bulk_dir)
    if not bulk_dir.exists():
        print(f"Error: bulk directory not found: {bulk_dir}")
        sys.exit(1)

    # Determine which files to load
    if args.tables:
        requested = set(args.tables.lower().split(","))
        files_to_load = [f for f in LOAD_ORDER
                         if ISBE_FILES[f].replace("isbe_", "") in requested
                         or ISBE_FILES[f] in requested]
    else:
        files_to_load = LOAD_ORDER

    # Check/download files
    for filename in files_to_load:
        fpath = find_file(bulk_dir, filename)
        if fpath is None:
            if args.download:
                download_file(filename, bulk_dir / filename)
            else:
                print(f"Warning: {filename} not found in {bulk_dir}. "
                      f"Use --download to fetch from ISBE.")

    # Connect
    conn = psycopg.connect(args.db_url)
    conn.autocommit = False

    total_t0 = time.time()

    try:
        # Create schema
        conn.autocommit = True
        create_schema(conn)
        conn.autocommit = False

        # Load each file
        total_rows = 0
        for filename in files_to_load:
            fpath = find_file(bulk_dir, filename)
            if fpath is None:
                print(f"  Skipping {filename} (not found)")
                continue
            rows = load_file(conn, filename, fpath)
            total_rows += rows

        # Post-load steps
        if "PrevOfficers.txt" in files_to_load:
            merge_prev_officers(conn)
        if "CmteOfficerLinks.txt" in files_to_load:
            update_officer_committees(conn)
        if "CmteCandidateLinks.txt" in files_to_load:
            infer_missing_candidate_links(conn)

        # Materialized views
        if not args.skip_views:
            conn.autocommit = True
            create_materialized_views(conn)
            create_compat_views(conn)
            conn.autocommit = False

        # Summary
        print_summary(conn)

        total_elapsed = time.time() - total_t0
        print(f"\nTotal: {total_rows:,} rows loaded in {total_elapsed:.1f}s")

    except Exception as e:
        conn.rollback()
        print(f"Error: {e}")
        import traceback
        traceback.print_exc()
        sys.exit(1)
    finally:
        conn.close()


if __name__ == "__main__":
    main()
