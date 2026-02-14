"""Tests for IRS 527 data loader."""
from pathlib import Path

from database.connection import get_db, init_db
from database.irs527_loader import (
    _parse_header,
    _parse_org,
    _parse_report,
    _parse_director,
    _parse_related_org,
    _parse_expenditure,
    _parse_election_authority,
    load_irs527_full_file,
    reload_irs527_reports,
)


def test_parse_header():
    fields = ["H", "20260208", "0641", "F", ""]
    result = _parse_header(fields)
    assert result["record_type"] == "H"
    assert result["date"] == "20260208"
    assert result["time"] == "0641"


def test_parse_org_valid():
    # Minimal org record with enough fields
    fields = ["1", "8871", "8", "0", "0", "0", "912121950",
              "Test Organization", "123 Main St", "", "Springfield", "IL",
              "62701", "", "test@email.com", "20010101",
              "John Doe", "456 Oak", "", "Chicago", "IL", "60601", "",
              "Jane Smith", "789 Pine", "", "Chicago", "IL", "60601", "",
              "123 Business", "", "Springfield", "IL", "62701", "",
              "Campaign purpose", "", "2001-05-13 21:20:54", "0", "1"]
    result = _parse_org(fields)
    assert result is not None
    assert result[0] == "912121950"  # ein
    assert result[3] == "Test Organization"  # org_name
    assert result[7] == "IL"  # state


def test_parse_org_malformed():
    fields = ["1", "8871"]  # Too few fields
    result = _parse_org(fields)
    assert result is None


def test_parse_director():
    fields = ["D", "8", "26174", "Test Org", "912121950",
              "John Smith", "President", "123 Main", "", "Chicago", "IL", "60601", ""]
    result = _parse_director(fields)
    assert result is not None
    assert result[0] == 8  # form_id
    assert result[1] == "912121950"  # ein
    assert result[3] == "John Smith"  # person_name
    assert result[4] == "President"  # title


def test_parse_related_org():
    fields = ["R", "10", "5", "Test Org", "061596525",
              "Related Org Name", "Connected", "123 Main", "", "City", "IL", "60601", ""]
    result = _parse_related_org(fields)
    assert result is not None
    assert result[0] == 10  # form_id
    assert result[3] == "Related Org Name"
    assert result[4] == "Connected"  # relationship_type


def test_parse_expenditure():
    fields = ["B", "9555268", "38295", "Test Org", "521073928",
              "Recipient Name", "123 Main", "", "Chicago", "IL", "60601", "",
              "Employer", "500", "Occupation", "20030430", "Contribution"]
    result = _parse_expenditure(fields)
    assert result is not None
    assert result[0] == 9555268  # form_id
    assert result[3] == "Recipient Name"
    assert result[11] == 500.0  # amount
    assert result[14] == "Contribution"  # purpose


def test_parse_election_authority():
    fields = ["E", "9555171", "1365", "FL"]
    result = _parse_election_authority(fields)
    assert result is not None
    assert result[0] == 9555171  # form_id
    assert result[1] == "1365"  # election_authority_id
    assert result[2] == "FL"  # state


def test_parse_election_authority_short():
    fields = ["E", "100"]
    result = _parse_election_authority(fields)
    assert result is None  # Too few fields


def test_parse_report_current_8872_layout():
    fields = [
        "2", "8872", "9555268", "20030101", "20030630", "1", "0", "0", "0",
        "HAWAII STATE TEACHERS ASSOCIATION POLITICAL ACTION COMMITTEE", "521073928",
        "1200 ALA KAPUNA STREET", "", "HONOLULU", "HI", "96819", "",
        "lhasegawa@nea.org", "19701209", "LYNNELLE HASEGAWA", "1200 ALA KAPUNA STREET",
        "", "HONOLULU", "HI", "96819", "", "LYNNELLE HASEGAWA", "1200 ALA KAPUNA STREET",
        "", "HONOLULU", "HI", "96819", "", "1200 ALA KAPUNA STREET", "", "HONOLULU",
        "HI", "96819", "", "5", "", "", "", "", "0", "97298", "0", "500",
        "2003-07-08 21:30:24", "",
    ]

    parsed = _parse_report(fields)
    assert parsed is not None
    assert parsed[0] == 9555268
    assert parsed[1] == "521073928"
    assert parsed[2] == "20030101"
    assert parsed[3] == "20030630"
    assert parsed[34] == 5
    assert parsed[38] == 97298.0
    assert parsed[39] == 500.0
    assert parsed[40] == "2003-07-08 21:30:24"


def test_load_irs527_basic(tmp_path: Path):
    """Test loading a small pipe-delimited file."""
    content = (
        "H|20260208|0641|F|\n"
        "1|8871|8|0|0|0|364367949|CITIZENS FOR STEFFEN|17 DOUGLAS AVE||ELGIN|IL|60120||DON@HOPPCPA.COM||"
        "DONALD HOPP, CPA|10 DOUGLAS AVE||ELGIN|IL|60120||ROBERT J. STEFFEN|234 JAMESTOWNE CT.||SLEEPY HOLLOW|IL|60118||"
        "17 DOUGLAS AVE||ELGIN|IL|60120|||||CAMPAIGN ORGANIZATION||2001-05-15 17:44:04|1|1\n"
        "D|8|277|CITIZENS FOR STEFFEN|364367949|ROBERT J. STEFFEN|CANDIDATE|234 JAMESTOWNE CT.||SLEEPY HOLLOW|IL|60118||\n"
        "B|9555268|38295|Test Org|364367949|Recipient|123 Main||Chicago|IL|60601||Emp|500||20030430|Contribution\n"
        "E|9555268|1365|IL\n"
    )
    data_path = tmp_path / "test_data.txt"
    data_path.write_text(content, encoding="utf-8")

    db_path = str(tmp_path / "test.db")
    init_db(db_path)
    conn = get_db(db_path)

    stats = load_irs527_full_file(conn, data_path, illinois_only=False)

    assert stats["headers"] == 1
    assert stats["orgs"] == 1
    assert stats["directors"] == 1
    assert stats["expenditures"] == 1
    assert stats["election_authorities"] == 1
    assert stats["malformed_rows"] == 0

    # Verify org was inserted
    org = conn.execute(
        "SELECT org_name, state FROM irs527_organizations WHERE ein = '364367949'"
    ).fetchone()
    assert org["org_name"] == "CITIZENS FOR STEFFEN"
    assert org["state"] == "IL"

    conn.close()


def test_load_irs527_illinois_only(tmp_path: Path):
    """Test that illinois_only filters out non-IL orgs."""
    content = (
        "H|20260208|0641|F|\n"
        "1|8871|8|0|0|0|111111111|IL ORG|123 Main||Chicago|IL|60601||email@test.com||"
        "John|123||Chicago|IL|60601||Jane|123||Chicago|IL|60601||"
        "123||Chicago|IL|60601|||||Purpose||2001-01-01|0|1\n"
        "1|8871|9|0|0|0|222222222|CA ORG|456 Oak||LA|CA|90001||email@test.com||"
        "John|456||LA|CA|90001||Jane|456||LA|CA|90001||"
        "456||LA|CA|90001|||||Purpose||2001-01-01|0|1\n"
        "D|8|1|IL ORG|111111111|Director A|President|123||Chicago|IL|60601||\n"
        "D|9|2|CA ORG|222222222|Director B|VP|456||LA|CA|90001||\n"
    )
    data_path = tmp_path / "test_data.txt"
    data_path.write_text(content, encoding="utf-8")

    db_path = str(tmp_path / "test.db")
    init_db(db_path)
    conn = get_db(db_path)

    stats = load_irs527_full_file(conn, data_path, illinois_only=True)

    assert stats["orgs"] == 1
    assert stats["orgs_skipped"] == 1
    assert stats["directors"] == 1  # Only IL org's director

    # IL org should exist
    il_org = conn.execute(
        "SELECT org_name FROM irs527_organizations WHERE ein = '111111111'"
    ).fetchone()
    assert il_org is not None

    # CA org should not exist
    ca_org = conn.execute(
        "SELECT org_name FROM irs527_organizations WHERE ein = '222222222'"
    ).fetchone()
    assert ca_org is None

    conn.close()


def test_reload_irs527_reports_illinois_only(tmp_path: Path):
    content = (
        "H|20260208|0641|F|\n"
        "1|8871|8|0|0|0|111111111|IL ORG|123 Main||Chicago|IL|60601||email@test.com|20010101|"
        "John Custodian|123 Main||Chicago|IL|60601||Jane Contact|123 Main||Chicago|IL|60601||"
        "123 Main||Chicago|IL|60601||Purpose||2001-01-01 00:00:00|0|1\n"
        "1|8871|9|0|0|0|222222222|CA ORG|456 Oak||Los Angeles|CA|90001||email@test.com|20010101|"
        "John Custodian|456 Oak||Los Angeles|CA|90001||Jane Contact|456 Oak||Los Angeles|CA|90001||"
        "456 Oak||Los Angeles|CA|90001||Purpose||2001-01-01 00:00:00|0|1\n"
        "2|8872|1001|20240101|20240331|1|0|0|0|IL ORG|111111111|123 Main||Chicago|IL|60601||"
        "email@test.com|20010101|John Custodian|123 Main||Chicago|IL|60601||Jane Contact|123 Main||"
        "Chicago|IL|60601||123 Main||Chicago|IL|60601||1|||||0|1000|0|250|2024-04-01 12:00:00|\n"
        "2|8872|1002|20240101|20240331|1|0|0|0|CA ORG|222222222|456 Oak||Los Angeles|CA|90001||"
        "email@test.com|20010101|John Custodian|456 Oak||Los Angeles|CA|90001||Jane Contact|456 Oak||"
        "Los Angeles|CA|90001||456 Oak||Los Angeles|CA|90001||1|||||0|500|0|100|2024-04-01 12:00:00|\n"
    )
    data_path = tmp_path / "test_527_reports_repair.txt"
    data_path.write_text(content, encoding="utf-8")

    db_path = str(tmp_path / "test.db")
    init_db(db_path)
    conn = get_db(db_path)

    # Pre-seed a bad row to validate replace_existing behavior.
    conn.execute(
        """
        INSERT INTO irs527_reports (form_id, ein, total_contributions, total_expenditures)
        VALUES (9999, '8872', 0, 0)
        """
    )
    conn.commit()

    stats = reload_irs527_reports(
        conn,
        data_path,
        illinois_only=True,
        replace_existing=True,
    )

    assert stats["existing_reports_deleted"] == 1
    assert stats["reports_loaded"] == 1
    assert stats["reports_skipped"] == 1
    assert stats["reports_malformed"] == 0

    rows = conn.execute(
        """
        SELECT ein, form_id, total_contributions, total_expenditures
        FROM irs527_reports
        ORDER BY form_id
        """
    ).fetchall()

    assert len(rows) == 1
    assert rows[0]["ein"] == "111111111"
    assert rows[0]["form_id"] == 1001
    assert rows[0]["total_contributions"] == 1000.0
    assert rows[0]["total_expenditures"] == 250.0

    conn.close()


def test_load_irs527_malformed_handling(tmp_path: Path):
    """Test graceful handling of malformed rows."""
    content = (
        "H|20260208|0641|F|\n"
        "X|bad|row\n"
        "1|8871|8|0|0|0|364367949|Good Org|123 Main||City|IL|60601||email@test.com||"
        "John|123||City|IL|60601||Jane|123||City|IL|60601||"
        "123||City|IL|60601|||||Purpose||2001-01-01|0|1\n"
    )
    data_path = tmp_path / "test_data.txt"
    data_path.write_text(content, encoding="utf-8")

    db_path = str(tmp_path / "test.db")
    init_db(db_path)
    conn = get_db(db_path)

    stats = load_irs527_full_file(conn, data_path, illinois_only=False)

    assert stats["malformed_rows"] == 1
    assert stats["orgs"] == 1
    assert stats["headers"] == 1

    conn.close()
