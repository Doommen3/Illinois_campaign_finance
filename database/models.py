"""Data models for Illinois Campaign Finance tracker."""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
import json
from typing import Optional, List, ClassVar
import sqlite3

from .identifiers import make_source_identifier, normalize_source_url
from werkzeug.security import check_password_hash, generate_password_hash


@dataclass
class Committee:
    """Represents a committee (candidate/organization filing reports)."""

    id: Optional[int] = None
    name: str = ""
    detail_url: Optional[str] = None
    source_identifier: Optional[str] = None
    created_at: Optional[datetime] = None
    updated_at: Optional[datetime] = None

    # Optional aggregated field
    total_contributions: Optional[float] = None

    @classmethod
    def _from_row(cls, row) -> "Committee":
        committee = cls(
            id=row["id"],
            name=row["name"],
            detail_url=row["detail_url"] if "detail_url" in row.keys() else None,
            source_identifier=row["source_identifier"] if "source_identifier" in row.keys() else None,
            created_at=row["created_at"],
            updated_at=row["updated_at"],
        )
        if "total_contributions" in row.keys():
            committee.total_contributions = row["total_contributions"] or 0
        return committee

    @classmethod
    def get_or_create(
        cls,
        conn: sqlite3.Connection,
        name: str,
        detail_url: str | None = None,
        source_identifier: str | None = None,
    ) -> "Committee":
        """Get committee by source identifier or name, or create it."""
        normalized_url = normalize_source_url(detail_url)
        source_identifier = source_identifier or make_source_identifier(normalized_url, name)

        row = None
        if source_identifier:
            cursor = conn.execute(
                """
                SELECT id, name, detail_url, source_identifier, created_at, updated_at
                FROM committees
                WHERE source_identifier = ?
                """,
                (source_identifier,),
            )
            row = cursor.fetchone()

        if not row:
            cursor = conn.execute(
                """
                SELECT id, name, detail_url, source_identifier, created_at, updated_at
                FROM committees
                WHERE name = ?
                """,
                (name,),
            )
            row = cursor.fetchone()

        if row:
            committee = cls._from_row(row)
            updated_detail_url = normalized_url or committee.detail_url
            updated_source_id = committee.source_identifier or source_identifier
            if updated_detail_url != committee.detail_url or updated_source_id != committee.source_identifier:
                conn.execute(
                    """
                    UPDATE committees
                    SET detail_url = ?, source_identifier = ?, updated_at = CURRENT_TIMESTAMP
                    WHERE id = ?
                    """,
                    (updated_detail_url, updated_source_id, committee.id),
                )
                conn.commit()
                committee.detail_url = updated_detail_url
                committee.source_identifier = updated_source_id
            return committee

        cursor = conn.execute(
            """
            INSERT INTO committees (name, detail_url, source_identifier)
            VALUES (?, ?, ?)
            """,
            (name, normalized_url, source_identifier),
        )
        conn.commit()
        return cls(
            id=cursor.lastrowid,
            name=name,
            detail_url=normalized_url,
            source_identifier=source_identifier,
        )

    @classmethod
    def get_by_id(cls, conn: sqlite3.Connection, committee_id: int) -> Optional["Committee"]:
        """Get a committee by ID."""
        cursor = conn.execute(
            """
            SELECT id, name, detail_url, source_identifier, created_at, updated_at
            FROM committees
            WHERE id = ?
            """,
            (committee_id,),
        )
        row = cursor.fetchone()
        if row:
            return cls._from_row(row)
        return None

    @classmethod
    def get_all(
        cls,
        conn: sqlite3.Connection,
        limit: int = 100,
        offset: int = 0,
        sort_by: str = "name",
        sort_dir: str = "asc",
    ) -> List["Committee"]:
        """Get all committees with pagination and sorting."""
        sort_map = {
            "name": "c.name",
            "created_at": "c.created_at",
            "total_contributions": "total_contributions",
        }
        sort_field = sort_map.get(sort_by, "c.name")
        direction = "ASC" if str(sort_dir).lower() == "asc" else "DESC"

        cursor = conn.execute(
            f"""
            SELECT c.id, c.name, c.detail_url, c.source_identifier, c.created_at, c.updated_at,
                   COALESCE(SUM(ct.amount), 0) AS total_contributions
            FROM committees c
            LEFT JOIN reports r ON r.committee_id = c.id
            LEFT JOIN contributions ct ON ct.report_id = r.id
            GROUP BY c.id
            ORDER BY {sort_field} {direction}, c.id ASC
            LIMIT ? OFFSET ?
            """,
            (limit, offset),
        )
        return [cls._from_row(row) for row in cursor.fetchall()]

    @classmethod
    def count(cls, conn: sqlite3.Connection) -> int:
        """Get total count of committees."""
        cursor = conn.execute("SELECT COUNT(*) as count FROM committees")
        return cursor.fetchone()["count"]

    @classmethod
    def search(cls, conn: sqlite3.Connection, query: str, limit: int = 100) -> List["Committee"]:
        """Search committees by name."""
        cursor = conn.execute(
            """
            SELECT id, name, detail_url, source_identifier, created_at, updated_at
            FROM committees
            WHERE name LIKE ?
            ORDER BY name
            LIMIT ?
            """,
            (f"%{query}%", limit),
        )
        return [cls._from_row(row) for row in cursor.fetchall()]


@dataclass
class Report:
    """Represents a filed report."""

    id: Optional[int] = None
    committee_id: Optional[int] = None
    report_type: Optional[str] = None
    reporting_period: Optional[str] = None
    filed_date: Optional[str] = None
    pages: Optional[int] = None
    clarification: Optional[str] = None
    detail_url: Optional[str] = None
    source_identifier: Optional[str] = None
    is_paper_filed: bool = False
    scrape_status: str = "pending"
    scrape_error: Optional[str] = None
    source_page: Optional[int] = None
    created_at: Optional[datetime] = None
    updated_at: Optional[datetime] = None

    # Optional joined field
    committee_name: Optional[str] = None

    @classmethod
    def _build_source_identifier(cls, report: "Report") -> str:
        return make_source_identifier(
            report.detail_url,
            report.committee_id,
            report.report_type,
            report.reporting_period,
            report.filed_date,
            report.pages,
        )

    @classmethod
    def _find_existing_id(cls, conn: sqlite3.Connection, report: "Report") -> Optional[int]:
        """Locate an existing report row for upsert behavior."""
        if report.source_identifier:
            row = conn.execute(
                "SELECT id FROM reports WHERE source_identifier = ?",
                (report.source_identifier,),
            ).fetchone()
            if row:
                return row["id"]

        if report.detail_url:
            row = conn.execute(
                "SELECT id FROM reports WHERE detail_url = ?",
                (report.detail_url,),
            ).fetchone()
            if row:
                return row["id"]

        row = conn.execute(
            """
            SELECT id
            FROM reports
            WHERE committee_id = ?
              AND COALESCE(report_type, '') = COALESCE(?, '')
              AND COALESCE(reporting_period, '') = COALESCE(?, '')
              AND COALESCE(filed_date, '') = COALESCE(?, '')
              AND COALESCE(pages, -1) = COALESCE(?, -1)
              AND COALESCE(clarification, '') = COALESCE(?, '')
            """,
            (
                report.committee_id,
                report.report_type,
                report.reporting_period,
                report.filed_date,
                report.pages,
                report.clarification,
            ),
        ).fetchone()
        if row:
            return row["id"]
        return None

    def save(self, conn: sqlite3.Connection) -> "Report":
        """Save the report to the database with upsert-like dedupe."""
        self.detail_url = normalize_source_url(self.detail_url)
        self.source_identifier = self.source_identifier or self._build_source_identifier(self)

        if not self.id:
            self.id = self._find_existing_id(conn, self)

        if self.id:
            conn.execute(
                """
                UPDATE reports SET
                    committee_id = ?, report_type = ?, reporting_period = ?,
                    filed_date = ?, pages = ?, clarification = ?, detail_url = ?,
                    source_identifier = ?, is_paper_filed = ?, scrape_status = ?, scrape_error = ?,
                    source_page = ?, updated_at = CURRENT_TIMESTAMP
                WHERE id = ?
                """,
                (
                    self.committee_id,
                    self.report_type,
                    self.reporting_period,
                    self.filed_date,
                    self.pages,
                    self.clarification,
                    self.detail_url,
                    self.source_identifier,
                    self.is_paper_filed,
                    self.scrape_status,
                    self.scrape_error,
                    self.source_page,
                    self.id,
                ),
            )
        else:
            cursor = conn.execute(
                """
                INSERT INTO reports (
                    committee_id, report_type, reporting_period, filed_date,
                    pages, clarification, detail_url, source_identifier,
                    is_paper_filed, scrape_status, scrape_error, source_page
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    self.committee_id,
                    self.report_type,
                    self.reporting_period,
                    self.filed_date,
                    self.pages,
                    self.clarification,
                    self.detail_url,
                    self.source_identifier,
                    self.is_paper_filed,
                    self.scrape_status,
                    self.scrape_error,
                    self.source_page,
                ),
            )
            self.id = cursor.lastrowid
        conn.commit()
        return self

    @classmethod
    def _from_row(cls, row) -> "Report":
        """Create a Report from a database row."""
        report = cls(
            id=row["id"],
            committee_id=row["committee_id"],
            report_type=row["report_type"],
            reporting_period=row["reporting_period"],
            filed_date=row["filed_date"],
            pages=row["pages"],
            clarification=row["clarification"],
            detail_url=row["detail_url"],
            source_identifier=row["source_identifier"] if "source_identifier" in row.keys() else None,
            is_paper_filed=bool(row["is_paper_filed"]),
            scrape_status=row["scrape_status"],
            scrape_error=row["scrape_error"],
            source_page=row["source_page"],
            created_at=row["created_at"],
            updated_at=row["updated_at"],
        )
        if "committee_name" in row.keys():
            report.committee_name = row["committee_name"]
        return report

    @classmethod
    def get_by_id(cls, conn: sqlite3.Connection, report_id: int) -> Optional["Report"]:
        """Get a report by ID."""
        cursor = conn.execute(
            """
            SELECT r.*, c.name as committee_name
            FROM reports r
            JOIN committees c ON r.committee_id = c.id
            WHERE r.id = ?
            """,
            (report_id,),
        )
        row = cursor.fetchone()
        if row:
            return cls._from_row(row)
        return None

    @classmethod
    def get_pending(cls, conn: sqlite3.Connection, limit: int = 20) -> List["Report"]:
        """Get reports pending detail scraping."""
        cursor = conn.execute(
            """
            SELECT r.*, c.name as committee_name
            FROM reports r
            JOIN committees c ON r.committee_id = c.id
            WHERE r.scrape_status = 'pending' AND r.is_paper_filed = 0
            ORDER BY r.id
            LIMIT ?
            """,
            (limit,),
        )
        return [cls._from_row(row) for row in cursor.fetchall()]

    @classmethod
    def get_all(
        cls,
        conn: sqlite3.Connection,
        limit: int = 100,
        offset: int = 0,
        paper_filed: Optional[bool] = None,
        sort_by: str = "filed_date",
        sort_dir: str = "desc",
    ) -> List["Report"]:
        """Get all reports with pagination, filtering, and sorting."""
        sort_map = {
            "committee": "c.name",
            "report_type": "r.report_type",
            "reporting_period": "r.reporting_period",
            "filed_date": "r.filed_date",
            "pages": "r.pages",
            "status": "r.scrape_status",
            "source_page": "r.source_page",
            "id": "r.id",
        }
        order_by = sort_map.get(sort_by, "r.filed_date")
        direction = "ASC" if str(sort_dir).lower() == "asc" else "DESC"

        query = """
            SELECT r.*, c.name as committee_name
            FROM reports r
            JOIN committees c ON r.committee_id = c.id
        """
        params: List[object] = []

        if paper_filed is not None:
            query += " WHERE r.is_paper_filed = ?"
            params.append(int(bool(paper_filed)))

        query += f" ORDER BY {order_by} {direction}, r.id DESC LIMIT ? OFFSET ?"
        params.extend([limit, offset])

        cursor = conn.execute(query, params)
        return [cls._from_row(row) for row in cursor.fetchall()]

    @classmethod
    def get_by_committee(
        cls,
        conn: sqlite3.Connection,
        committee_id: int,
        limit: int = 100,
        offset: int = 0,
        sort_by: str = "filed_date",
        sort_dir: str = "desc",
    ) -> List["Report"]:
        """Get reports for a specific committee."""
        sort_map = {
            "report_type": "r.report_type",
            "reporting_period": "r.reporting_period",
            "filed_date": "r.filed_date",
            "pages": "r.pages",
            "status": "r.scrape_status",
            "source_page": "r.source_page",
            "id": "r.id",
        }
        order_by = sort_map.get(sort_by, "r.filed_date")
        direction = "ASC" if str(sort_dir).lower() == "asc" else "DESC"

        cursor = conn.execute(
            f"""
            SELECT r.*, c.name as committee_name
            FROM reports r
            JOIN committees c ON r.committee_id = c.id
            WHERE r.committee_id = ?
            ORDER BY {order_by} {direction}, r.id DESC
            LIMIT ? OFFSET ?
            """,
            (committee_id, limit, offset),
        )
        return [cls._from_row(row) for row in cursor.fetchall()]

    @classmethod
    def count(cls, conn: sqlite3.Connection, paper_filed: Optional[bool] = None) -> int:
        """Get total count of reports."""
        if paper_filed is not None:
            cursor = conn.execute(
                "SELECT COUNT(*) as count FROM reports WHERE is_paper_filed = ?",
                (int(bool(paper_filed)),),
            )
        else:
            cursor = conn.execute("SELECT COUNT(*) as count FROM reports")
        return cursor.fetchone()["count"]

    @classmethod
    def count_by_committee(cls, conn: sqlite3.Connection, committee_id: int) -> int:
        """Count reports for a committee."""
        cursor = conn.execute(
            "SELECT COUNT(*) as count FROM reports WHERE committee_id = ?",
            (committee_id,),
        )
        return cursor.fetchone()["count"]

    @classmethod
    def count_by_status(cls, conn: sqlite3.Connection) -> dict:
        """Get count of reports by scrape status."""
        cursor = conn.execute(
            """
            SELECT scrape_status, COUNT(*) as count
            FROM reports
            GROUP BY scrape_status
            """
        )
        return {row["scrape_status"]: row["count"] for row in cursor.fetchall()}


@dataclass
class Donor:
    """Represents a unique donor."""

    id: Optional[int] = None
    name: str = ""
    address: Optional[str] = None
    occupation: Optional[str] = None
    employer: Optional[str] = None
    normalized_name: str = ""
    normalized_address: Optional[str] = None
    created_at: Optional[datetime] = None

    # Optional aggregated field
    total_amount: Optional[float] = None
    contribution_count: Optional[int] = None
    committee_count: Optional[int] = None
    donor_key: Optional[str] = None
    donor_city: Optional[str] = None
    donor_state: Optional[str] = None
    source: Optional[str] = None
    BULK_RECEIPTS_MATERIALIZATION_VERSION: ClassVar[int] = 2

    @classmethod
    def _table_exists(cls, conn: sqlite3.Connection, table_name: str) -> bool:
        row = conn.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name = ?",
            (table_name,),
        ).fetchone()
        return row is not None

    @classmethod
    def _column_exists(cls, conn: sqlite3.Connection, table_name: str, column_name: str) -> bool:
        if not cls._table_exists(conn, table_name):
            return False
        rows = conn.execute(f"PRAGMA table_info({table_name})").fetchall()
        return any(row["name"] == column_name for row in rows)

    @classmethod
    def _bulk_materialization_is_current(cls, conn: sqlite3.Connection) -> bool:
        if not cls._table_exists(conn, "analytics_materialized_meta"):
            return False
        if not cls._column_exists(conn, "analytics_materialized_meta", "materialization_version"):
            return False
        row = conn.execute(
            """
            SELECT materialization_version
            FROM analytics_materialized_meta
            WHERE source = 'bulk_receipts'
            LIMIT 1
            """
        ).fetchone()
        if not row:
            return False
        try:
            return int(row["materialization_version"] or 0) >= cls.BULK_RECEIPTS_MATERIALIZATION_VERSION
        except (TypeError, ValueError):
            return False

    @classmethod
    def _refresh_bulk_materialization_if_stale(cls, conn: sqlite3.Connection) -> None:
        if not cls._table_exists(conn, "analytics_donor_summary"):
            return
        has_bulk_rows = conn.execute(
            """
            SELECT 1
            FROM analytics_donor_summary
            WHERE source = 'bulk_receipts'
            LIMIT 1
            """
        ).fetchone()
        if not has_bulk_rows or cls._bulk_materialization_is_current(conn):
            return
        try:
            from database.analytics import refresh_analytics_materialized

            refresh_analytics_materialized(conn)
        except Exception:
            # Keep donor pages readable even if a rebuild cannot happen right now.
            return

    @classmethod
    def get_directory_source(cls, conn: sqlite3.Connection) -> Optional[str]:
        """Return donor directory source when materialized summary is available."""
        if not cls._table_exists(conn, "analytics_donor_summary"):
            return None
        cls._refresh_bulk_materialization_if_stale(conn)
        bulk_row = conn.execute(
            """
            SELECT 1
            FROM analytics_donor_summary
            WHERE source = 'bulk_receipts'
            LIMIT 1
            """
        ).fetchone()
        if bulk_row and cls._bulk_materialization_is_current(conn):
            return "bulk_receipts"

        contrib_row = conn.execute(
            """
            SELECT 1
            FROM analytics_donor_summary
            WHERE source = 'contributions'
            LIMIT 1
            """
        ).fetchone()
        if contrib_row:
            return "contributions"
        return None

    @classmethod
    def get_or_create(
        cls,
        conn: sqlite3.Connection,
        name: str,
        address: str,
        normalized_name: str,
        normalized_address: str,
        occupation: Optional[str] = None,
        employer: Optional[str] = None,
    ) -> "Donor":
        """Get an existing donor by normalized name/address or create a new one."""
        cursor = conn.execute(
            """
            SELECT id, name, address, occupation, employer, normalized_name, normalized_address, created_at
            FROM donors
            WHERE normalized_name = ? AND normalized_address = ?
            """,
            (normalized_name, normalized_address),
        )
        row = cursor.fetchone()
        if row:
            donor = cls(
                id=row["id"],
                name=row["name"],
                address=row["address"],
                occupation=row["occupation"] if "occupation" in row.keys() else None,
                employer=row["employer"] if "employer" in row.keys() else None,
                normalized_name=row["normalized_name"],
                normalized_address=row["normalized_address"],
                created_at=row["created_at"],
            )
            updated_occupation = donor.occupation or occupation
            updated_employer = donor.employer or employer
            if updated_occupation != donor.occupation or updated_employer != donor.employer:
                conn.execute(
                    """
                    UPDATE donors
                    SET occupation = ?, employer = ?
                    WHERE id = ?
                    """,
                    (updated_occupation, updated_employer, donor.id),
                )
                conn.commit()
                donor.occupation = updated_occupation
                donor.employer = updated_employer
            return donor

        cursor = conn.execute(
            """
            INSERT INTO donors (name, address, occupation, employer, normalized_name, normalized_address)
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            (name, address, occupation, employer, normalized_name, normalized_address),
        )
        conn.commit()
        return cls(
            id=cursor.lastrowid,
            name=name,
            address=address,
            occupation=occupation,
            employer=employer,
            normalized_name=normalized_name,
            normalized_address=normalized_address,
        )

    @classmethod
    def get_by_id(cls, conn: sqlite3.Connection, donor_id: int) -> Optional["Donor"]:
        """Get a donor by ID."""
        cursor = conn.execute(
            """
            SELECT id, name, address, occupation, employer, normalized_name, normalized_address, created_at
            FROM donors WHERE id = ?
            """,
            (donor_id,),
        )
        row = cursor.fetchone()
        if row:
            return cls(
                id=row["id"],
                name=row["name"],
                address=row["address"],
                occupation=row["occupation"] if "occupation" in row.keys() else None,
                employer=row["employer"] if "employer" in row.keys() else None,
                normalized_name=row["normalized_name"],
                normalized_address=row["normalized_address"],
                created_at=row["created_at"],
            )
        return None

    @classmethod
    def get_all_with_totals(
        cls,
        conn: sqlite3.Connection,
        limit: int = 100,
        offset: int = 0,
        sort_by: str = "total_amount",
        sort_dir: str = "desc",
    ) -> List["Donor"]:
        """Get all donors with aggregated contribution totals."""
        source = cls.get_directory_source(conn)
        if source:
            sort_map = {
                "source": "source",
                "name": "donor_name",
                "address": "donor_address",
                "occupation": "occupation",
                "employer": "employer",
                "total_amount": "total_amount",
                "contribution_count": "contribution_count",
                "committee_count": "committee_count",
                "city": "donor_city",
                "state": "donor_state",
                "created_at": "updated_at",
            }
            order_by = sort_map.get(sort_by, "total_amount")
            direction = "ASC" if str(sort_dir).lower() == "asc" else "DESC"
            rows = conn.execute(
                f"""
                SELECT
                    source,
                    donor_key,
                    local_donor_id,
                    donor_name,
                    donor_address,
                    donor_city,
                    donor_state,
                    occupation,
                    employer,
                    total_amount,
                    contribution_count,
                    committee_count
                FROM analytics_donor_summary
                WHERE source = ?
                ORDER BY {order_by} {direction}, donor_name ASC
                LIMIT ? OFFSET ?
                """,
                (source, limit, offset),
            ).fetchall()
            donors = []
            for row in rows:
                donor = cls(
                    id=row["local_donor_id"],
                    name=row["donor_name"] or "Unknown Donor",
                    address=row["donor_address"],
                    occupation=row["occupation"],
                    employer=row["employer"],
                    normalized_name="",
                    normalized_address=None,
                    created_at=None,
                )
                donor.total_amount = float(row["total_amount"] or 0.0)
                donor.contribution_count = int(row["contribution_count"] or 0)
                donor.committee_count = int(row["committee_count"] or 0)
                donor.donor_key = row["donor_key"]
                donor.donor_city = row["donor_city"]
                donor.donor_state = row["donor_state"]
                donor.source = row["source"]
                donors.append(donor)
            return donors

        sort_map = {
            "name": "d.name",
            "address": "d.address",
            "occupation": "d.occupation",
            "employer": "d.employer",
            "total_amount": "total_amount",
            "contribution_count": "contribution_count",
            "created_at": "d.created_at",
        }
        order_by = sort_map.get(sort_by, "total_amount")
        direction = "ASC" if str(sort_dir).lower() == "asc" else "DESC"

        cursor = conn.execute(
            f"""
            SELECT d.*, COALESCE(SUM(c.amount), 0) as total_amount, COUNT(c.id) as contribution_count
            FROM donors d
            LEFT JOIN contributions c ON d.id = c.donor_id
            GROUP BY d.id
            ORDER BY {order_by} {direction}, d.id ASC
            LIMIT ? OFFSET ?
            """,
            (limit, offset),
        )
        donors = []
        for row in cursor.fetchall():
            donor = cls(
                id=row["id"],
                name=row["name"],
                address=row["address"],
                occupation=row["occupation"] if "occupation" in row.keys() else None,
                employer=row["employer"] if "employer" in row.keys() else None,
                normalized_name=row["normalized_name"],
                normalized_address=row["normalized_address"],
                created_at=row["created_at"],
            )
            donor.total_amount = row["total_amount"] or 0
            donor.contribution_count = row["contribution_count"] or 0
            donors.append(donor)
        return donors

    @classmethod
    def count(cls, conn: sqlite3.Connection) -> int:
        """Get total count of donors."""
        source = cls.get_directory_source(conn)
        if source:
            cursor = conn.execute(
                """
                SELECT COUNT(*) as count
                FROM analytics_donor_summary
                WHERE source = ?
                """,
                (source,),
            )
            return cursor.fetchone()["count"]

        cursor = conn.execute("SELECT COUNT(*) as count FROM donors")
        return cursor.fetchone()["count"]

    @classmethod
    def search(cls, conn: sqlite3.Connection, query: str, limit: int = 100) -> List["Donor"]:
        """Search donors by name."""
        cursor = conn.execute(
            """
            SELECT d.*, SUM(c.amount) as total_amount, COUNT(c.id) as contribution_count
            FROM donors d
            LEFT JOIN contributions c ON d.id = c.donor_id
            WHERE d.name LIKE ? OR d.normalized_name LIKE ?
            GROUP BY d.id
            ORDER BY total_amount DESC
            LIMIT ?
            """,
            (f"%{query}%", f"%{query}%", limit),
        )
        donors = []
        for row in cursor.fetchall():
            donor = cls(
                id=row["id"],
                name=row["name"],
                address=row["address"],
                occupation=row["occupation"] if "occupation" in row.keys() else None,
                employer=row["employer"] if "employer" in row.keys() else None,
                normalized_name=row["normalized_name"],
                normalized_address=row["normalized_address"],
                created_at=row["created_at"],
            )
            donor.total_amount = row["total_amount"] or 0
            donor.contribution_count = row["contribution_count"] or 0
            donors.append(donor)
        return donors

    @classmethod
    def get_summary_by_key(
        cls,
        conn: sqlite3.Connection,
        donor_key: str,
        source: Optional[str] = None,
    ) -> Optional["Donor"]:
        """Get donor summary row by stable donor key."""
        if not donor_key or not cls._table_exists(conn, "analytics_donor_summary"):
            return None

        row = None
        if source:
            row = conn.execute(
                """
                SELECT
                    source,
                    donor_key,
                    local_donor_id,
                    donor_name,
                    donor_address,
                    donor_city,
                    donor_state,
                    occupation,
                    employer,
                    total_amount,
                    contribution_count,
                    committee_count
                FROM analytics_donor_summary
                WHERE source = ? AND donor_key = ?
                LIMIT 1
                """,
                (source, donor_key),
            ).fetchone()

        if not row:
            preferred_source = source or cls.get_directory_source(conn) or ""
            row = conn.execute(
                """
                SELECT
                    source,
                    donor_key,
                    local_donor_id,
                    donor_name,
                    donor_address,
                    donor_city,
                    donor_state,
                    occupation,
                    employer,
                    total_amount,
                    contribution_count,
                    committee_count
                FROM analytics_donor_summary
                WHERE donor_key = ?
                ORDER BY
                    CASE WHEN source = ? THEN 0 ELSE 1 END,
                    total_amount DESC
                LIMIT 1
                """,
                (donor_key, preferred_source),
            ).fetchone()

        if not row:
            return None

        donor = cls(
            id=row["local_donor_id"],
            name=row["donor_name"] or "Unknown Donor",
            address=row["donor_address"],
            occupation=row["occupation"],
            employer=row["employer"],
            normalized_name="",
            normalized_address=None,
            created_at=None,
        )
        donor.total_amount = float(row["total_amount"] or 0.0)
        donor.contribution_count = int(row["contribution_count"] or 0)
        donor.committee_count = int(row["committee_count"] or 0)
        donor.donor_key = row["donor_key"]
        donor.donor_city = row["donor_city"]
        donor.donor_state = row["donor_state"]
        donor.source = row["source"]
        return donor

    @classmethod
    def get_committee_breakdown_by_key(
        cls,
        conn: sqlite3.Connection,
        donor_key: str,
        source: str,
        limit: int = 100,
        offset: int = 0,
        sort_by: str = "amount",
        sort_dir: str = "desc",
    ) -> List[dict]:
        """Get committee-level totals for a donor key."""
        if (
            not donor_key
            or not source
            or not cls._table_exists(conn, "analytics_donor_committee_agg")
        ):
            return []

        sort_map = {
            "committee": "committee_name",
            "amount": "total_amount",
            "contribution_count": "contribution_count",
        }
        order_by = sort_map.get(sort_by, "total_amount")
        direction = "ASC" if str(sort_dir).lower() == "asc" else "DESC"

        rows = conn.execute(
            f"""
            SELECT
                committee_id,
                committee_name,
                total_amount,
                contribution_count
            FROM analytics_donor_committee_agg
            WHERE source = ? AND donor_key = ?
            ORDER BY {order_by} {direction}, committee_name ASC
            LIMIT ? OFFSET ?
            """,
            (source, donor_key, limit, offset),
        ).fetchall()
        return [
            {
                "committee_id": row["committee_id"],
                "committee_name": row["committee_name"],
                "total_amount": float(row["total_amount"] or 0.0),
                "contribution_count": int(row["contribution_count"] or 0),
            }
            for row in rows
        ]

    @classmethod
    def count_committee_breakdown_by_key(
        cls,
        conn: sqlite3.Connection,
        donor_key: str,
        source: str,
    ) -> int:
        """Count committee rows available for a donor key."""
        if (
            not donor_key
            or not source
            or not cls._table_exists(conn, "analytics_donor_committee_agg")
        ):
            return 0

        row = conn.execute(
            """
            SELECT COUNT(*) AS count
            FROM analytics_donor_committee_agg
            WHERE source = ? AND donor_key = ?
            """,
            (source, donor_key),
        ).fetchone()
        return int(row["count"] or 0) if row else 0


@dataclass
class Contribution:
    """Represents a contribution."""

    id: Optional[int] = None
    report_id: Optional[int] = None
    donor_id: Optional[int] = None
    amount: Optional[float] = None
    transaction_date: Optional[str] = None
    received_by: Optional[str] = None
    description: Optional[str] = None
    vendor_name: Optional[str] = None
    vendor_address: Optional[str] = None
    raw_contributed_by: Optional[str] = None
    raw_address: Optional[str] = None
    raw_occupation: Optional[str] = None
    raw_employer: Optional[str] = None
    created_at: Optional[datetime] = None

    # Optional joined fields
    donor_name: Optional[str] = None
    committee_name: Optional[str] = None
    filed_date: Optional[str] = None

    def save(self, conn: sqlite3.Connection) -> "Contribution":
        """Save the contribution to the database."""
        if self.id:
            conn.execute(
                """
                UPDATE contributions SET
                    report_id = ?, donor_id = ?, amount = ?, transaction_date = ?, received_by = ?,
                    description = ?, vendor_name = ?, vendor_address = ?,
                    raw_contributed_by = ?, raw_address = ?, raw_occupation = ?, raw_employer = ?
                WHERE id = ?
                """,
                (
                    self.report_id,
                    self.donor_id,
                    self.amount,
                    self.transaction_date,
                    self.received_by,
                    self.description,
                    self.vendor_name,
                    self.vendor_address,
                    self.raw_contributed_by,
                    self.raw_address,
                    self.raw_occupation,
                    self.raw_employer,
                    self.id,
                ),
            )
        else:
            cursor = conn.execute(
                """
                INSERT INTO contributions (
                    report_id, donor_id, amount, transaction_date, received_by, description,
                    vendor_name, vendor_address, raw_contributed_by, raw_address,
                    raw_occupation, raw_employer
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    self.report_id,
                    self.donor_id,
                    self.amount,
                    self.transaction_date,
                    self.received_by,
                    self.description,
                    self.vendor_name,
                    self.vendor_address,
                    self.raw_contributed_by,
                    self.raw_address,
                    self.raw_occupation,
                    self.raw_employer,
                ),
            )
            self.id = cursor.lastrowid
        conn.commit()
        return self

    @classmethod
    def _from_row(cls, row) -> "Contribution":
        """Create a Contribution from a database row."""
        contrib = cls(
            id=row["id"],
            report_id=row["report_id"],
            donor_id=row["donor_id"],
            amount=row["amount"],
            transaction_date=row["transaction_date"] if "transaction_date" in row.keys() else None,
            received_by=row["received_by"],
            description=row["description"],
            vendor_name=row["vendor_name"],
            vendor_address=row["vendor_address"],
            raw_contributed_by=row["raw_contributed_by"],
            raw_address=row["raw_address"],
            raw_occupation=row["raw_occupation"] if "raw_occupation" in row.keys() else None,
            raw_employer=row["raw_employer"] if "raw_employer" in row.keys() else None,
            created_at=row["created_at"],
        )
        keys = row.keys()
        if "donor_name" in keys:
            contrib.donor_name = row["donor_name"]
        if "committee_name" in keys:
            contrib.committee_name = row["committee_name"]
        if "filed_date" in keys:
            contrib.filed_date = row["filed_date"]
        return contrib

    @classmethod
    def get_by_report(
        cls,
        conn: sqlite3.Connection,
        report_id: int,
        sort_by: str = "amount",
        sort_dir: str = "desc",
    ) -> List["Contribution"]:
        """Get contributions for a specific report."""
        sort_map = {
            "donor": "d.name",
            "amount": "c.amount",
            "transaction_date": "c.transaction_date",
            "received_by": "c.received_by",
            "description": "c.description",
            "vendor_name": "c.vendor_name",
            "vendor_address": "c.vendor_address",
        }
        order_by = sort_map.get(sort_by, "c.amount")
        direction = "ASC" if str(sort_dir).lower() == "asc" else "DESC"

        cursor = conn.execute(
            f"""
            SELECT c.*, d.name as donor_name
            FROM contributions c
            JOIN donors d ON c.donor_id = d.id
            WHERE c.report_id = ?
            ORDER BY {order_by} {direction}, c.id DESC
            """,
            (report_id,),
        )
        return [cls._from_row(row) for row in cursor.fetchall()]

    @classmethod
    def get_by_donor(
        cls,
        conn: sqlite3.Connection,
        donor_id: int,
        limit: int = 100,
        offset: int = 0,
        sort_by: str = "filed_date",
        sort_dir: str = "desc",
    ) -> List["Contribution"]:
        """Get contributions from a specific donor."""
        sort_map = {
            "committee": "cm.name",
            "amount": "c.amount",
            "filed_date": "r.filed_date",
            "description": "c.description",
        }
        order_by = sort_map.get(sort_by, "r.filed_date")
        direction = "ASC" if str(sort_dir).lower() == "asc" else "DESC"

        cursor = conn.execute(
            f"""
            SELECT c.*, d.name as donor_name, cm.name as committee_name, r.filed_date
            FROM contributions c
            JOIN donors d ON c.donor_id = d.id
            JOIN reports r ON c.report_id = r.id
            JOIN committees cm ON r.committee_id = cm.id
            WHERE c.donor_id = ?
            ORDER BY {order_by} {direction}, c.id DESC
            LIMIT ? OFFSET ?
            """,
            (donor_id, limit, offset),
        )
        return [cls._from_row(row) for row in cursor.fetchall()]

    @classmethod
    def get_by_committee(
        cls,
        conn: sqlite3.Connection,
        committee_id: int,
        limit: int = 100,
        offset: int = 0,
        sort_by: str = "filed_date",
        sort_dir: str = "desc",
    ) -> List["Contribution"]:
        """Get contributions for a specific committee."""
        sort_map = {
            "donor": "d.name",
            "amount": "c.amount",
            "filed_date": "r.filed_date",
            "description": "c.description",
        }
        order_by = sort_map.get(sort_by, "r.filed_date")
        direction = "ASC" if str(sort_dir).lower() == "asc" else "DESC"

        cursor = conn.execute(
            f"""
            SELECT c.*, d.name as donor_name, r.filed_date
            FROM contributions c
            JOIN donors d ON c.donor_id = d.id
            JOIN reports r ON c.report_id = r.id
            WHERE r.committee_id = ?
            ORDER BY {order_by} {direction}, c.id DESC
            LIMIT ? OFFSET ?
            """,
            (committee_id, limit, offset),
        )
        return [cls._from_row(row) for row in cursor.fetchall()]

    @classmethod
    def count(cls, conn: sqlite3.Connection) -> int:
        """Get total count of contributions."""
        cursor = conn.execute("SELECT COUNT(*) as count FROM contributions")
        return cursor.fetchone()["count"]

    @classmethod
    def total_amount(cls, conn: sqlite3.Connection) -> float:
        """Get total amount of all contributions."""
        cursor = conn.execute("SELECT COALESCE(SUM(amount), 0) as total FROM contributions")
        return cursor.fetchone()["total"]

    @classmethod
    def total_by_committee(cls, conn: sqlite3.Connection, committee_id: int) -> float:
        """Get total amount for a committee."""
        cursor = conn.execute(
            """
            SELECT COALESCE(SUM(c.amount), 0) as total
            FROM contributions c
            JOIN reports r ON c.report_id = r.id
            WHERE r.committee_id = ?
            """,
            (committee_id,),
        )
        return cursor.fetchone()["total"]

    @classmethod
    def total_by_donor(cls, conn: sqlite3.Connection, donor_id: int) -> float:
        """Get total amount from a donor."""
        cursor = conn.execute(
            "SELECT COALESCE(SUM(amount), 0) as total FROM contributions WHERE donor_id = ?",
            (donor_id,),
        )
        return cursor.fetchone()["total"]


@dataclass
class CandidateCommitteeFinanceAgg:
    """Candidate-committee financial aggregation row from bulk download data."""

    candidate_id: Optional[int] = None
    candidate_full_name: Optional[str] = None
    office_sought: Optional[str] = None
    district_type: Optional[str] = None
    district: Optional[str] = None
    candidate_party_affiliation: Optional[str] = None
    committee_id_sbe: Optional[int] = None
    committee_name: Optional[str] = None
    committee_type: Optional[str] = None
    committee_party_affiliation: Optional[str] = None
    period_year: Optional[int] = None
    election_cycle: Optional[int] = None
    filing_count: int = 0
    sum_total_receipts: float = 0.0
    sum_total_expenditures: float = 0.0
    max_ending_funds_available: float = 0.0
    archived_filing_count: int = 0
    period_start_date: Optional[str] = None
    period_end_date: Optional[str] = None

    TABLE_NAME = "bulk_candidate_committee_finance_agg"

    @classmethod
    def _table_exists(cls, conn: sqlite3.Connection) -> bool:
        row = conn.execute(
            "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = ?",
            (cls.TABLE_NAME,),
        ).fetchone()
        return row is not None

    @classmethod
    def is_available(cls, conn: sqlite3.Connection) -> bool:
        """Return True when the bulk aggregation table is present."""
        return cls._table_exists(conn)

    @classmethod
    def _column_names(cls, conn: sqlite3.Connection) -> set[str]:
        if not cls._table_exists(conn):
            return set()
        rows = conn.execute(f"PRAGMA table_info({cls.TABLE_NAME})").fetchall()
        return {row["name"] for row in rows if row and row["name"]}

    @classmethod
    def _build_filter_sql(
        cls,
        available_columns: set[str],
        has_bulk_receipts_table: bool = False,
        search: Optional[str] = None,
        office: Optional[str] = None,
        candidate_party: Optional[str] = None,
        committee_party: Optional[str] = None,
        start_date: Optional[str] = None,
        year: Optional[int] = None,
        cycle: Optional[int] = None,
        min_receipts: Optional[float] = None,
        min_expenditures: Optional[float] = None,
    ) -> tuple[str, List[object]]:
        clauses: List[str] = []
        params: List[object] = []

        search_term = (search or "").strip()
        if search_term:
            clauses.append(
                """
                (
                    COALESCE(candidate_full_name, '') LIKE ?
                    OR COALESCE(committee_name, '') LIKE ?
                    OR COALESCE(office_sought, '') LIKE ?
                )
                """
            )
            like_term = f"%{search_term}%"
            params.extend([like_term, like_term, like_term])

        office_term = (office or "").strip()
        if office_term:
            clauses.append("COALESCE(office_sought, '') LIKE ?")
            params.append(f"%{office_term}%")

        candidate_party_term = (candidate_party or "").strip()
        if candidate_party_term:
            clauses.append("COALESCE(candidate_party_affiliation, '') LIKE ?")
            params.append(f"%{candidate_party_term}%")

        committee_party_term = (committee_party or "").strip()
        if committee_party_term:
            clauses.append("COALESCE(committee_party_affiliation, '') LIKE ?")
            params.append(f"%{committee_party_term}%")

        start_date_term = (start_date or "").strip()
        if start_date_term:
            if "period_end_date" in available_columns:
                clauses.append("DATE(period_end_date) >= DATE(?)")
                params.append(start_date_term)
            elif "period_year" in available_columns and len(start_date_term) >= 4:
                try:
                    clauses.append("period_year >= ?")
                    params.append(int(start_date_term[:4]))
                except ValueError:
                    pass
            elif has_bulk_receipts_table:
                clauses.append(
                    """
                    committee_id_sbe IN (
                        SELECT DISTINCT committee_id_sbe
                        FROM bulk_receipts_clean
                        WHERE DATE(received_date) >= DATE(?)
                    )
                    """
                )
                params.append(start_date_term)

        if year is not None and "period_year" in available_columns:
            clauses.append("period_year = ?")
            params.append(int(year))

        if cycle is not None and "election_cycle" in available_columns:
            clauses.append("election_cycle = ?")
            params.append(int(cycle))

        if min_receipts is not None:
            clauses.append("COALESCE(sum_total_receipts, 0) >= ?")
            params.append(float(min_receipts))

        if min_expenditures is not None:
            clauses.append("COALESCE(sum_total_expenditures, 0) >= ?")
            params.append(float(min_expenditures))

        if not clauses:
            return "", params
        return f" WHERE {' AND '.join(clauses)}", params

    @classmethod
    def count(
        cls,
        conn: sqlite3.Connection,
        search: Optional[str] = None,
        office: Optional[str] = None,
        candidate_party: Optional[str] = None,
        committee_party: Optional[str] = None,
        start_date: Optional[str] = None,
        year: Optional[int] = None,
        cycle: Optional[int] = None,
        min_receipts: Optional[float] = None,
        min_expenditures: Optional[float] = None,
    ) -> int:
        """Count rows in the candidate-committee finance aggregate table."""
        if not cls._table_exists(conn):
            return 0

        available_columns = cls._column_names(conn)
        has_bulk_receipts_table = conn.execute(
            "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = 'bulk_receipts_clean'"
        ).fetchone() is not None
        where_sql, params = cls._build_filter_sql(
            available_columns=available_columns,
            has_bulk_receipts_table=has_bulk_receipts_table,
            search=search,
            office=office,
            candidate_party=candidate_party,
            committee_party=committee_party,
            start_date=start_date,
            year=year,
            cycle=cycle,
            min_receipts=min_receipts,
            min_expenditures=min_expenditures,
        )
        query = f"SELECT COUNT(*) AS count FROM {cls.TABLE_NAME}{where_sql}"

        row = conn.execute(query, params).fetchone()
        return row["count"] if row else 0

    @classmethod
    def get_all(
        cls,
        conn: sqlite3.Connection,
        limit: int = 100,
        offset: int = 0,
        sort_by: str = "sum_total_receipts",
        sort_dir: str = "desc",
        search: Optional[str] = None,
        office: Optional[str] = None,
        candidate_party: Optional[str] = None,
        committee_party: Optional[str] = None,
        start_date: Optional[str] = None,
        year: Optional[int] = None,
        cycle: Optional[int] = None,
        min_receipts: Optional[float] = None,
        min_expenditures: Optional[float] = None,
    ) -> List["CandidateCommitteeFinanceAgg"]:
        """Fetch paginated candidate-committee aggregate rows with sorting."""
        if not cls._table_exists(conn):
            return []

        available_columns = cls._column_names(conn)
        has_bulk_receipts_table = conn.execute(
            "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = 'bulk_receipts_clean'"
        ).fetchone() is not None
        sort_map = {
            "candidate_id": "candidate_id",
            "candidate_full_name": "candidate_full_name",
            "office_sought": "office_sought",
            "district_type": "district_type",
            "district": "district",
            "candidate_party_affiliation": "candidate_party_affiliation",
            "committee_id_sbe": "committee_id_sbe",
            "committee_name": "committee_name",
            "committee_type": "committee_type",
            "committee_party_affiliation": "committee_party_affiliation",
            "period_year": "period_year" if "period_year" in available_columns else "candidate_id",
            "election_cycle": "election_cycle" if "election_cycle" in available_columns else "candidate_id",
            "filing_count": "filing_count",
            "sum_total_receipts": "sum_total_receipts",
            "sum_total_expenditures": "sum_total_expenditures",
            "max_ending_funds_available": "max_ending_funds_available",
            "archived_filing_count": "archived_filing_count",
        }
        order_by = sort_map.get(sort_by, "sum_total_receipts")
        direction = "ASC" if str(sort_dir).lower() == "asc" else "DESC"

        query = f"""
            SELECT
                candidate_id,
                candidate_full_name,
                office_sought,
                district_type,
                district,
                candidate_party_affiliation,
                committee_id_sbe,
                committee_name,
                committee_type,
                committee_party_affiliation,
                {"period_year" if "period_year" in available_columns else "NULL AS period_year"},
                {"election_cycle" if "election_cycle" in available_columns else "NULL AS election_cycle"},
                filing_count,
                sum_total_receipts,
                sum_total_expenditures,
                max_ending_funds_available,
                archived_filing_count,
                {"period_start_date" if "period_start_date" in available_columns else "NULL AS period_start_date"},
                {"period_end_date" if "period_end_date" in available_columns else "NULL AS period_end_date"}
            FROM {cls.TABLE_NAME}
        """
        where_sql, params = cls._build_filter_sql(
            available_columns=available_columns,
            has_bulk_receipts_table=has_bulk_receipts_table,
            search=search,
            office=office,
            candidate_party=candidate_party,
            committee_party=committee_party,
            start_date=start_date,
            year=year,
            cycle=cycle,
            min_receipts=min_receipts,
            min_expenditures=min_expenditures,
        )
        query += where_sql

        query += f" ORDER BY {order_by} {direction}, candidate_id ASC, committee_id_sbe ASC LIMIT ? OFFSET ?"
        params.extend([limit, offset])

        rows = conn.execute(query, params).fetchall()
        results = []
        for row in rows:
            results.append(
                cls(
                    candidate_id=row["candidate_id"],
                    candidate_full_name=row["candidate_full_name"],
                    office_sought=row["office_sought"],
                    district_type=row["district_type"],
                    district=row["district"],
                    candidate_party_affiliation=row["candidate_party_affiliation"],
                    committee_id_sbe=row["committee_id_sbe"],
                    committee_name=row["committee_name"],
                    committee_type=row["committee_type"],
                    committee_party_affiliation=row["committee_party_affiliation"],
                    period_year=row["period_year"],
                    election_cycle=row["election_cycle"],
                    filing_count=row["filing_count"] or 0,
                    sum_total_receipts=row["sum_total_receipts"] or 0.0,
                    sum_total_expenditures=row["sum_total_expenditures"] or 0.0,
                    max_ending_funds_available=row["max_ending_funds_available"] or 0.0,
                    archived_filing_count=row["archived_filing_count"] or 0,
                    period_start_date=row["period_start_date"],
                    period_end_date=row["period_end_date"],
                )
            )
        return results

    @classmethod
    def list_period_values(cls, conn: sqlite3.Connection) -> dict[str, List[int]]:
        """Return available period years and election cycles for filters."""
        if not cls._table_exists(conn):
            return {"years": [], "cycles": []}

        columns = cls._column_names(conn)
        years: List[int] = []
        cycles: List[int] = []

        if "period_year" in columns:
            year_rows = conn.execute(
                f"""
                SELECT DISTINCT period_year
                FROM {cls.TABLE_NAME}
                WHERE period_year IS NOT NULL
                ORDER BY period_year DESC
                """
            ).fetchall()
            years = [int(row["period_year"]) for row in year_rows if row["period_year"] is not None]

        if "election_cycle" in columns:
            cycle_rows = conn.execute(
                f"""
                SELECT DISTINCT election_cycle
                FROM {cls.TABLE_NAME}
                WHERE election_cycle IS NOT NULL
                ORDER BY election_cycle DESC
                """
            ).fetchall()
            cycles = [int(row["election_cycle"]) for row in cycle_rows if row["election_cycle"] is not None]

        return {"years": years, "cycles": cycles}


@dataclass
class CandidateCommitteeItemizedReceipt:
    """Itemized receipt row for a candidate/committee pair."""

    receipt_record_id: Optional[int] = None
    committee_id_sbe: Optional[int] = None
    filed_doc_id: Optional[int] = None
    received_date: Optional[str] = None
    d2_part_code: Optional[str] = None
    donor_name: Optional[str] = None
    occupation: Optional[str] = None
    employer: Optional[str] = None
    donor_address: Optional[str] = None
    amount: float = 0.0
    aggregate_amount: float = 0.0
    loan_amount: float = 0.0
    description: Optional[str] = None
    vendor_name: Optional[str] = None
    vendor_address: Optional[str] = None
    is_archived: Optional[int] = None
    country: Optional[str] = None
    redaction_requested: Optional[int] = None

    RECEIPTS_TABLE = "bulk_receipts_clean"
    LINKS_TABLE = "bulk_committee_candidate_links"

    @classmethod
    def _table_exists(cls, conn: sqlite3.Connection, table_name: str) -> bool:
        row = conn.execute(
            "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = ?",
            (table_name,),
        ).fetchone()
        return row is not None

    @classmethod
    def is_available(cls, conn: sqlite3.Connection) -> bool:
        required_tables = [
            cls.RECEIPTS_TABLE,
            cls.LINKS_TABLE,
            "bulk_candidates_clean",
            "bulk_committees_clean",
        ]
        return all(cls._table_exists(conn, table_name) for table_name in required_tables)

    @classmethod
    def get_context(
        cls,
        conn: sqlite3.Connection,
        candidate_id: int,
        committee_id: int,
    ) -> Optional[dict]:
        if not cls.is_available(conn):
            return None

        row = conn.execute(
            """
            SELECT
                l.candidate_id,
                l.committee_id_sbe,
                cand.candidate_full_name,
                c.committee_name
            FROM bulk_committee_candidate_links l
            LEFT JOIN bulk_candidates_clean cand
              ON cand.candidate_id = l.candidate_id
            LEFT JOIN bulk_committees_clean c
              ON c.committee_id_sbe = l.committee_id_sbe
            WHERE l.candidate_id = ?
              AND l.committee_id_sbe = ?
            LIMIT 1
            """,
            (candidate_id, committee_id),
        ).fetchone()
        if not row:
            return None
        return {
            "candidate_id": row["candidate_id"],
            "committee_id_sbe": row["committee_id_sbe"],
            "candidate_full_name": row["candidate_full_name"] or f"Candidate {candidate_id}",
            "committee_name": row["committee_name"] or f"Committee {committee_id}",
        }

    @classmethod
    def _build_filter_sql(
        cls,
        search: Optional[str] = None,
        d2_part: Optional[str] = None,
        min_amount: Optional[float] = None,
        max_amount: Optional[float] = None,
        archived: str = "no",
    ) -> tuple[str, List[object]]:
        clauses: List[str] = []
        params: List[object] = []

        search_term = (search or "").strip()
        if search_term:
            like_term = f"%{search_term}%"
            clauses.append(
                """
                (
                    COALESCE(r.last_or_business_name, '') LIKE ?
                    OR COALESCE(r.first_name, '') LIKE ?
                    OR COALESCE(r.description, '') LIKE ?
                    OR COALESCE(r.vendor_last_or_business_name, '') LIKE ?
                    OR COALESCE(r.vendor_first_name, '') LIKE ?
                    OR CAST(COALESCE(r.filed_doc_id, '') AS TEXT) LIKE ?
                )
                """
            )
            params.extend([like_term, like_term, like_term, like_term, like_term, like_term])

        d2_part_term = (d2_part or "").strip()
        if d2_part_term:
            clauses.append("COALESCE(r.d2_part_code, '') LIKE ?")
            params.append(f"{d2_part_term}%")

        if min_amount is not None:
            clauses.append("COALESCE(r.amount, 0) >= ?")
            params.append(float(min_amount))

        if max_amount is not None:
            clauses.append("COALESCE(r.amount, 0) <= ?")
            params.append(float(max_amount))

        archived_term = (archived or "no").strip().lower()
        if archived_term == "yes":
            clauses.append("COALESCE(r.is_archived, 0) = 1")
        elif archived_term == "no":
            clauses.append("COALESCE(r.is_archived, 0) = 0")

        if not clauses:
            return "", params
        return f" AND {' AND '.join(clauses)}", params

    @classmethod
    def count(
        cls,
        conn: sqlite3.Connection,
        candidate_id: int,
        committee_id: int,
        search: Optional[str] = None,
        d2_part: Optional[str] = None,
        min_amount: Optional[float] = None,
        max_amount: Optional[float] = None,
        archived: str = "no",
    ) -> int:
        if not cls.is_available(conn):
            return 0

        filter_sql, filter_params = cls._build_filter_sql(
            search=search,
            d2_part=d2_part,
            min_amount=min_amount,
            max_amount=max_amount,
            archived=archived,
        )
        row = conn.execute(
            f"""
            SELECT COUNT(*) AS count
            FROM {cls.RECEIPTS_TABLE} r
            WHERE r.committee_id_sbe = ?
              AND EXISTS (
                  SELECT 1
                  FROM {cls.LINKS_TABLE} l
                  WHERE l.candidate_id = ?
                    AND l.committee_id_sbe = r.committee_id_sbe
              )
              {filter_sql}
            """,
            [committee_id, candidate_id, *filter_params],
        ).fetchone()
        return row["count"] if row else 0

    @classmethod
    def get_all(
        cls,
        conn: sqlite3.Connection,
        candidate_id: int,
        committee_id: int,
        limit: int = 100,
        offset: int = 0,
        sort_by: str = "received_date",
        sort_dir: str = "desc",
        search: Optional[str] = None,
        d2_part: Optional[str] = None,
        min_amount: Optional[float] = None,
        max_amount: Optional[float] = None,
        archived: str = "no",
    ) -> List["CandidateCommitteeItemizedReceipt"]:
        if not cls.is_available(conn):
            return []

        sort_map = {
            "receipt_record_id": "r.receipt_record_id",
            "filed_doc_id": "r.filed_doc_id",
            "received_date": "r.received_date",
            "d2_part_code": "r.d2_part_code",
            "donor_name": "COALESCE(r.last_or_business_name, '') || ' ' || COALESCE(r.first_name, '')",
            "amount": "r.amount",
            "aggregate_amount": "r.aggregate_amount",
            "loan_amount": "r.loan_amount",
            "occupation": "r.occupation",
            "employer": "r.employer",
            "vendor_name": "COALESCE(r.vendor_last_or_business_name, '') || ' ' || COALESCE(r.vendor_first_name, '')",
            "is_archived": "r.is_archived",
        }
        order_by = sort_map.get(sort_by, "r.received_date")
        direction = "ASC" if str(sort_dir).lower() == "asc" else "DESC"

        filter_sql, filter_params = cls._build_filter_sql(
            search=search,
            d2_part=d2_part,
            min_amount=min_amount,
            max_amount=max_amount,
            archived=archived,
        )

        rows = conn.execute(
            f"""
            SELECT
                r.receipt_record_id,
                r.committee_id_sbe,
                r.filed_doc_id,
                r.received_date,
                r.d2_part_code,
                r.last_or_business_name,
                r.first_name,
                r.occupation,
                r.employer,
                r.address_line_1,
                r.address_line_2,
                r.city,
                r.state,
                r.postal_code,
                r.amount,
                r.aggregate_amount,
                r.loan_amount,
                r.description,
                r.vendor_last_or_business_name,
                r.vendor_first_name,
                r.vendor_address_line_1,
                r.vendor_address_line_2,
                r.vendor_city,
                r.vendor_state,
                r.vendor_postal_code,
                r.is_archived,
                r.country,
                r.redaction_requested
            FROM {cls.RECEIPTS_TABLE} r
            WHERE r.committee_id_sbe = ?
              AND EXISTS (
                  SELECT 1
                  FROM {cls.LINKS_TABLE} l
                  WHERE l.candidate_id = ?
                    AND l.committee_id_sbe = r.committee_id_sbe
              )
              {filter_sql}
            ORDER BY {order_by} {direction}, r.receipt_record_id DESC
            LIMIT ? OFFSET ?
            """,
            [committee_id, candidate_id, *filter_params, limit, offset],
        ).fetchall()

        results: List[CandidateCommitteeItemizedReceipt] = []
        for row in rows:
            donor_name = " ".join(
                part for part in [row["first_name"], row["last_or_business_name"]] if part
            ).strip() or None
            donor_address = ", ".join(
                part
                for part in [
                    row["address_line_1"],
                    row["address_line_2"],
                    row["city"],
                    row["state"],
                    row["postal_code"],
                ]
                if part
            ).strip() or None
            vendor_name = " ".join(
                part for part in [row["vendor_first_name"], row["vendor_last_or_business_name"]] if part
            ).strip() or None
            vendor_address = ", ".join(
                part
                for part in [
                    row["vendor_address_line_1"],
                    row["vendor_address_line_2"],
                    row["vendor_city"],
                    row["vendor_state"],
                    row["vendor_postal_code"],
                ]
                if part
            ).strip() or None

            results.append(
                cls(
                    receipt_record_id=row["receipt_record_id"],
                    committee_id_sbe=row["committee_id_sbe"],
                    filed_doc_id=row["filed_doc_id"],
                    received_date=row["received_date"],
                    d2_part_code=row["d2_part_code"],
                    donor_name=donor_name,
                    occupation=row["occupation"],
                    employer=row["employer"],
                    donor_address=donor_address,
                    amount=row["amount"] or 0.0,
                    aggregate_amount=row["aggregate_amount"] or 0.0,
                    loan_amount=row["loan_amount"] or 0.0,
                    description=row["description"],
                    vendor_name=vendor_name,
                    vendor_address=vendor_address,
                    is_archived=row["is_archived"],
                    country=row["country"],
                    redaction_requested=row["redaction_requested"],
                )
            )
        return results


@dataclass
class D2ReceiptsRecon:
    """D2 filing totals reconciled against itemized receipts sums."""

    d2_totals_record_id: Optional[int] = None
    committee_id_sbe: Optional[int] = None
    committee_name: Optional[str] = None
    filed_doc_id: Optional[int] = None
    d2_total_receipts: float = 0.0
    d2_total_expenditures: float = 0.0
    ending_funds_available: float = 0.0
    is_archived: Optional[int] = None
    receipt_row_count: int = 0
    receipts_amount_sum: float = 0.0
    first_receipt_date: Optional[str] = None
    last_receipt_date: Optional[str] = None
    receipts_minus_d2_total: float = 0.0

    TABLE_NAME = "bulk_d2_receipts_recon"

    @classmethod
    def _table_exists(cls, conn: sqlite3.Connection) -> bool:
        row = conn.execute(
            "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = ?",
            (cls.TABLE_NAME,),
        ).fetchone()
        return row is not None

    @classmethod
    def is_available(cls, conn: sqlite3.Connection) -> bool:
        return cls._table_exists(conn)

    @classmethod
    def _build_filter_sql(
        cls,
        search: Optional[str] = None,
        min_abs_diff: Optional[float] = None,
        min_receipt_rows: Optional[int] = None,
    ) -> tuple[str, List[object]]:
        clauses: List[str] = []
        params: List[object] = []

        search_term = (search or "").strip()
        if search_term:
            clauses.append(
                """
                (
                    COALESCE(committee_name, '') LIKE ?
                    OR CAST(COALESCE(committee_id_sbe, '') AS TEXT) LIKE ?
                    OR CAST(COALESCE(filed_doc_id, '') AS TEXT) LIKE ?
                )
                """
            )
            like_term = f"%{search_term}%"
            params.extend([like_term, like_term, like_term])

        if min_abs_diff is not None:
            clauses.append("ABS(COALESCE(receipts_minus_d2_total, 0)) >= ?")
            params.append(float(min_abs_diff))

        if min_receipt_rows is not None:
            clauses.append("COALESCE(receipt_row_count, 0) >= ?")
            params.append(int(min_receipt_rows))

        if not clauses:
            return "", params
        return f" WHERE {' AND '.join(clauses)}", params

    @classmethod
    def count(
        cls,
        conn: sqlite3.Connection,
        search: Optional[str] = None,
        min_abs_diff: Optional[float] = None,
        min_receipt_rows: Optional[int] = None,
    ) -> int:
        if not cls._table_exists(conn):
            return 0

        where_sql, params = cls._build_filter_sql(
            search=search,
            min_abs_diff=min_abs_diff,
            min_receipt_rows=min_receipt_rows,
        )
        row = conn.execute(
            f"SELECT COUNT(*) AS count FROM {cls.TABLE_NAME}{where_sql}",
            params,
        ).fetchone()
        return row["count"] if row else 0

    @classmethod
    def get_all(
        cls,
        conn: sqlite3.Connection,
        limit: int = 100,
        offset: int = 0,
        sort_by: str = "abs_diff",
        sort_dir: str = "desc",
        search: Optional[str] = None,
        min_abs_diff: Optional[float] = None,
        min_receipt_rows: Optional[int] = None,
    ) -> List["D2ReceiptsRecon"]:
        if not cls._table_exists(conn):
            return []

        sort_map = {
            "d2_totals_record_id": "d2_totals_record_id",
            "committee_id_sbe": "committee_id_sbe",
            "committee_name": "committee_name",
            "filed_doc_id": "filed_doc_id",
            "d2_total_receipts": "d2_total_receipts",
            "d2_total_expenditures": "d2_total_expenditures",
            "ending_funds_available": "ending_funds_available",
            "is_archived": "is_archived",
            "receipt_row_count": "receipt_row_count",
            "receipts_amount_sum": "receipts_amount_sum",
            "first_receipt_date": "first_receipt_date",
            "last_receipt_date": "last_receipt_date",
            "receipts_minus_d2_total": "receipts_minus_d2_total",
            "abs_diff": "ABS(COALESCE(receipts_minus_d2_total, 0))",
        }
        order_by = sort_map.get(sort_by, "ABS(COALESCE(receipts_minus_d2_total, 0))")
        direction = "ASC" if str(sort_dir).lower() == "asc" else "DESC"

        query = f"""
            SELECT
                d2_totals_record_id,
                committee_id_sbe,
                committee_name,
                filed_doc_id,
                d2_total_receipts,
                d2_total_expenditures,
                ending_funds_available,
                is_archived,
                receipt_row_count,
                receipts_amount_sum,
                first_receipt_date,
                last_receipt_date,
                receipts_minus_d2_total
            FROM {cls.TABLE_NAME}
        """
        where_sql, params = cls._build_filter_sql(
            search=search,
            min_abs_diff=min_abs_diff,
            min_receipt_rows=min_receipt_rows,
        )
        query += where_sql
        query += f" ORDER BY {order_by} {direction}, committee_id_sbe ASC, filed_doc_id ASC LIMIT ? OFFSET ?"
        params.extend([limit, offset])

        rows = conn.execute(query, params).fetchall()
        return [
            cls(
                d2_totals_record_id=row["d2_totals_record_id"],
                committee_id_sbe=row["committee_id_sbe"],
                committee_name=row["committee_name"],
                filed_doc_id=row["filed_doc_id"],
                d2_total_receipts=row["d2_total_receipts"] or 0.0,
                d2_total_expenditures=row["d2_total_expenditures"] or 0.0,
                ending_funds_available=row["ending_funds_available"] or 0.0,
                is_archived=row["is_archived"],
                receipt_row_count=row["receipt_row_count"] or 0,
                receipts_amount_sum=row["receipts_amount_sum"] or 0.0,
                first_receipt_date=row["first_receipt_date"],
                last_receipt_date=row["last_receipt_date"],
                receipts_minus_d2_total=row["receipts_minus_d2_total"] or 0.0,
            )
            for row in rows
        ]


@dataclass
class D2Report:
    """Represents D-2 quarterly report metadata and scrape status."""

    id: Optional[int] = None
    committee_id: Optional[int] = None
    report_type: Optional[str] = None
    reporting_period: Optional[str] = None
    filed_date: Optional[str] = None
    pages: Optional[int] = None
    clarification: Optional[str] = None
    detail_url: Optional[str] = None
    source_identifier: Optional[str] = None
    summary_json: Optional[str] = None
    detail_scrape_status: str = "pending"
    detail_scrape_error: Optional[str] = None
    itemized_scrape_status: str = "pending"
    itemized_scrape_error: Optional[str] = None
    source_page: Optional[int] = None
    created_at: Optional[datetime] = None
    updated_at: Optional[datetime] = None

    # Optional joined field
    committee_name: Optional[str] = None

    @property
    def summary(self) -> dict:
        if not self.summary_json:
            return {}
        try:
            return json.loads(self.summary_json)
        except json.JSONDecodeError:
            return {}

    @summary.setter
    def summary(self, value: dict) -> None:
        self.summary_json = json.dumps(value or {}, sort_keys=True)

    @classmethod
    def _from_row(cls, row) -> "D2Report":
        report = cls(
            id=row["id"],
            committee_id=row["committee_id"],
            report_type=row["report_type"],
            reporting_period=row["reporting_period"],
            filed_date=row["filed_date"],
            pages=row["pages"],
            clarification=row["clarification"],
            detail_url=row["detail_url"],
            source_identifier=row["source_identifier"],
            summary_json=row["summary_json"],
            detail_scrape_status=row["detail_scrape_status"],
            detail_scrape_error=row["detail_scrape_error"],
            itemized_scrape_status=row["itemized_scrape_status"],
            itemized_scrape_error=row["itemized_scrape_error"],
            source_page=row["source_page"],
            created_at=row["created_at"],
            updated_at=row["updated_at"],
        )
        if "committee_name" in row.keys():
            report.committee_name = row["committee_name"]
        return report

    @classmethod
    def _build_source_identifier(cls, report: "D2Report") -> str:
        return make_source_identifier(
            report.detail_url,
            report.committee_id,
            report.report_type,
            report.reporting_period,
            report.filed_date,
            report.pages,
        )

    def save(self, conn: sqlite3.Connection) -> "D2Report":
        """Save D-2 report, deduping by source identifier or detail URL."""
        self.detail_url = normalize_source_url(self.detail_url)
        self.source_identifier = self.source_identifier or self._build_source_identifier(self)

        if not self.id:
            row = conn.execute(
                "SELECT id FROM d2_reports WHERE source_identifier = ?",
                (self.source_identifier,),
            ).fetchone()
            if not row and self.detail_url:
                row = conn.execute(
                    "SELECT id FROM d2_reports WHERE detail_url = ?",
                    (self.detail_url,),
                ).fetchone()
            if not row:
                row = conn.execute(
                    """
                    SELECT id FROM d2_reports
                    WHERE committee_id = ?
                      AND COALESCE(report_type, '') = COALESCE(?, '')
                      AND COALESCE(reporting_period, '') = COALESCE(?, '')
                      AND COALESCE(filed_date, '') = COALESCE(?, '')
                    """,
                    (self.committee_id, self.report_type, self.reporting_period, self.filed_date),
                ).fetchone()
            if row:
                self.id = row["id"]

        if self.id:
            conn.execute(
                """
                UPDATE d2_reports
                SET committee_id = ?, report_type = ?, reporting_period = ?, filed_date = ?,
                    pages = ?, clarification = ?, detail_url = ?, source_identifier = ?,
                    summary_json = ?, detail_scrape_status = ?, detail_scrape_error = ?,
                    itemized_scrape_status = ?, itemized_scrape_error = ?, source_page = ?,
                    updated_at = CURRENT_TIMESTAMP
                WHERE id = ?
                """,
                (
                    self.committee_id,
                    self.report_type,
                    self.reporting_period,
                    self.filed_date,
                    self.pages,
                    self.clarification,
                    self.detail_url,
                    self.source_identifier,
                    self.summary_json,
                    self.detail_scrape_status,
                    self.detail_scrape_error,
                    self.itemized_scrape_status,
                    self.itemized_scrape_error,
                    self.source_page,
                    self.id,
                ),
            )
        else:
            cursor = conn.execute(
                """
                INSERT INTO d2_reports (
                    committee_id, report_type, reporting_period, filed_date,
                    pages, clarification, detail_url, source_identifier,
                    summary_json, detail_scrape_status, detail_scrape_error,
                    itemized_scrape_status, itemized_scrape_error, source_page
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    self.committee_id,
                    self.report_type,
                    self.reporting_period,
                    self.filed_date,
                    self.pages,
                    self.clarification,
                    self.detail_url,
                    self.source_identifier,
                    self.summary_json,
                    self.detail_scrape_status,
                    self.detail_scrape_error,
                    self.itemized_scrape_status,
                    self.itemized_scrape_error,
                    self.source_page,
                ),
            )
            self.id = cursor.lastrowid

        conn.commit()
        return self

    @classmethod
    def get_by_id(cls, conn: sqlite3.Connection, report_id: int) -> Optional["D2Report"]:
        cursor = conn.execute(
            """
            SELECT d2.*, c.name as committee_name
            FROM d2_reports d2
            JOIN committees c ON c.id = d2.committee_id
            WHERE d2.id = ?
            """,
            (report_id,),
        )
        row = cursor.fetchone()
        return cls._from_row(row) if row else None

    @classmethod
    def get_pending_details(
        cls,
        conn: sqlite3.Connection,
        limit: int = 20,
        committee_ids: Optional[List[int]] = None,
    ) -> List["D2Report"]:
        query = """
            SELECT d2.*, c.name as committee_name
            FROM d2_reports d2
            JOIN committees c ON c.id = d2.committee_id
            WHERE d2.detail_scrape_status = 'pending'
        """
        params: List[object] = []

        if committee_ids:
            placeholders = ",".join(["?"] * len(committee_ids))
            query += f" AND d2.committee_id IN ({placeholders})"
            params.extend(committee_ids)

        query += " ORDER BY d2.id ASC LIMIT ?"
        params.append(limit)

        cursor = conn.execute(query, params)
        return [cls._from_row(row) for row in cursor.fetchall()]

    @classmethod
    def get_pending_itemized(
        cls,
        conn: sqlite3.Connection,
        limit: int = 20,
        committee_ids: Optional[List[int]] = None,
    ) -> List["D2Report"]:
        query = """
            SELECT d2.*, c.name as committee_name
            FROM d2_reports d2
            JOIN committees c ON c.id = d2.committee_id
            WHERE d2.itemized_scrape_status = 'pending'
        """
        params: List[object] = []

        if committee_ids:
            placeholders = ",".join(["?"] * len(committee_ids))
            query += f" AND d2.committee_id IN ({placeholders})"
            params.extend(committee_ids)

        query += " ORDER BY d2.id ASC LIMIT ?"
        params.append(limit)

        cursor = conn.execute(query, params)
        return [cls._from_row(row) for row in cursor.fetchall()]


@dataclass
class D2ItemizedLink:
    """Itemized links found on a D-2 report detail page."""

    id: Optional[int] = None
    d2_report_id: Optional[int] = None
    label: Optional[str] = None
    itemized_type: Optional[str] = None
    url: Optional[str] = None
    source_identifier: Optional[str] = None
    status: str = "pending"
    error_message: Optional[str] = None
    created_at: Optional[datetime] = None
    updated_at: Optional[datetime] = None

    @classmethod
    def _from_row(cls, row) -> "D2ItemizedLink":
        return cls(
            id=row["id"],
            d2_report_id=row["d2_report_id"],
            label=row["label"],
            itemized_type=row["itemized_type"],
            url=row["url"],
            source_identifier=row["source_identifier"],
            status=row["status"],
            error_message=row["error_message"],
            created_at=row["created_at"],
            updated_at=row["updated_at"],
        )

    def save(self, conn: sqlite3.Connection) -> "D2ItemizedLink":
        self.url = normalize_source_url(self.url)
        self.source_identifier = self.source_identifier or make_source_identifier(self.url, self.label)

        if not self.id:
            row = conn.execute(
                """
                SELECT id
                FROM d2_itemized_links
                WHERE d2_report_id = ? AND source_identifier = ?
                """,
                (self.d2_report_id, self.source_identifier),
            ).fetchone()
            if row:
                self.id = row["id"]

        if self.id:
            conn.execute(
                """
                UPDATE d2_itemized_links
                SET d2_report_id = ?, label = ?, itemized_type = ?, url = ?,
                    source_identifier = ?, status = ?, error_message = ?,
                    updated_at = CURRENT_TIMESTAMP
                WHERE id = ?
                """,
                (
                    self.d2_report_id,
                    self.label,
                    self.itemized_type,
                    self.url,
                    self.source_identifier,
                    self.status,
                    self.error_message,
                    self.id,
                ),
            )
        else:
            cursor = conn.execute(
                """
                INSERT INTO d2_itemized_links (
                    d2_report_id, label, itemized_type, url, source_identifier,
                    status, error_message
                ) VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    self.d2_report_id,
                    self.label,
                    self.itemized_type,
                    self.url,
                    self.source_identifier,
                    self.status,
                    self.error_message,
                ),
            )
            self.id = cursor.lastrowid

        conn.commit()
        return self

    @classmethod
    def get_by_report(cls, conn: sqlite3.Connection, d2_report_id: int) -> List["D2ItemizedLink"]:
        cursor = conn.execute(
            """
            SELECT *
            FROM d2_itemized_links
            WHERE d2_report_id = ?
            ORDER BY id ASC
            """,
            (d2_report_id,),
        )
        return [cls._from_row(row) for row in cursor.fetchall()]

    @classmethod
    def get_pending(
        cls,
        conn: sqlite3.Connection,
        limit: int = 100,
        committee_ids: Optional[List[int]] = None,
    ) -> List["D2ItemizedLink"]:
        query = """
            SELECT l.*
            FROM d2_itemized_links l
            JOIN d2_reports d2 ON d2.id = l.d2_report_id
            WHERE l.status = 'pending'
        """
        params: List[object] = []

        if committee_ids:
            placeholders = ",".join(["?"] * len(committee_ids))
            query += f" AND d2.committee_id IN ({placeholders})"
            params.extend(committee_ids)

        query += " ORDER BY l.id ASC LIMIT ?"
        params.append(limit)
        cursor = conn.execute(query, params)
        return [cls._from_row(row) for row in cursor.fetchall()]


@dataclass
class D2ItemizedEntry:
    """Row-level itemized data scraped from D-2 itemized pages."""

    id: Optional[int] = None
    d2_report_id: Optional[int] = None
    itemized_link_id: Optional[int] = None
    source_page: Optional[int] = None
    source_row: Optional[int] = None
    row_hash: Optional[str] = None
    entry_type: Optional[str] = None
    contributed_by: Optional[str] = None
    received_by: Optional[str] = None
    address: Optional[str] = None
    amount: Optional[float] = None
    description: Optional[str] = None
    vendor_name: Optional[str] = None
    vendor_address: Optional[str] = None
    expended_by: Optional[str] = None
    purpose_beneficiary: Optional[str] = None
    candidate_name: Optional[str] = None
    office_district: Optional[str] = None
    supporting_opposing: Optional[str] = None
    created_at: Optional[datetime] = None

    def save(self, conn: sqlite3.Connection) -> "D2ItemizedEntry":
        """Save itemized entry with dedupe by (itemized_link_id, row_hash)."""
        if not self.row_hash:
            self.row_hash = make_source_identifier(
                None,
                self.itemized_link_id,
                self.source_page,
                self.source_row,
                self.entry_type,
                self.contributed_by,
                self.received_by,
                self.address,
                self.amount,
                self.description,
                self.vendor_name,
                self.vendor_address,
                self.expended_by,
                self.purpose_beneficiary,
                self.candidate_name,
                self.office_district,
                self.supporting_opposing,
            )

        if not self.id:
            row = conn.execute(
                """
                SELECT id
                FROM d2_itemized_entries
                WHERE itemized_link_id = ? AND row_hash = ?
                """,
                (self.itemized_link_id, self.row_hash),
            ).fetchone()
            if row:
                self.id = row["id"]

        if self.id:
            conn.execute(
                """
                UPDATE d2_itemized_entries
                SET d2_report_id = ?, itemized_link_id = ?, source_page = ?, source_row = ?,
                    row_hash = ?, entry_type = ?, contributed_by = ?, received_by = ?,
                    address = ?, amount = ?, description = ?, vendor_name = ?, vendor_address = ?,
                    expended_by = ?, purpose_beneficiary = ?, candidate_name = ?,
                    office_district = ?, supporting_opposing = ?
                WHERE id = ?
                """,
                (
                    self.d2_report_id,
                    self.itemized_link_id,
                    self.source_page,
                    self.source_row,
                    self.row_hash,
                    self.entry_type,
                    self.contributed_by,
                    self.received_by,
                    self.address,
                    self.amount,
                    self.description,
                    self.vendor_name,
                    self.vendor_address,
                    self.expended_by,
                    self.purpose_beneficiary,
                    self.candidate_name,
                    self.office_district,
                    self.supporting_opposing,
                    self.id,
                ),
            )
        else:
            cursor = conn.execute(
                """
                INSERT INTO d2_itemized_entries (
                    d2_report_id, itemized_link_id, source_page, source_row, row_hash,
                    entry_type, contributed_by, received_by, address, amount, description,
                    vendor_name, vendor_address, expended_by, purpose_beneficiary,
                    candidate_name, office_district, supporting_opposing
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    self.d2_report_id,
                    self.itemized_link_id,
                    self.source_page,
                    self.source_row,
                    self.row_hash,
                    self.entry_type,
                    self.contributed_by,
                    self.received_by,
                    self.address,
                    self.amount,
                    self.description,
                    self.vendor_name,
                    self.vendor_address,
                    self.expended_by,
                    self.purpose_beneficiary,
                    self.candidate_name,
                    self.office_district,
                    self.supporting_opposing,
                ),
            )
            self.id = cursor.lastrowid

        conn.commit()
        return self


@dataclass
class RawExtraction:
    """Stores raw scraped payloads for re-processing and auditing."""

    id: Optional[int] = None
    source_type: str = ""
    source_identifier: str = ""
    source_url: Optional[str] = None
    parser_version: Optional[str] = None
    payload_json: str = "{}"
    created_at: Optional[datetime] = None
    updated_at: Optional[datetime] = None

    @property
    def payload(self) -> dict:
        try:
            return json.loads(self.payload_json or "{}")
        except json.JSONDecodeError:
            return {}

    @payload.setter
    def payload(self, value: dict) -> None:
        self.payload_json = json.dumps(value or {}, sort_keys=True)

    def save(self, conn: sqlite3.Connection) -> "RawExtraction":
        """Insert or update a raw extraction row by (source_type, source_identifier)."""
        existing = conn.execute(
            """
            SELECT id
            FROM raw_extractions
            WHERE source_type = ? AND source_identifier = ?
            """,
            (self.source_type, self.source_identifier),
        ).fetchone()

        if existing:
            self.id = existing["id"]
            conn.execute(
                """
                UPDATE raw_extractions
                SET source_url = ?, parser_version = ?, payload_json = ?, updated_at = CURRENT_TIMESTAMP
                WHERE id = ?
                """,
                (self.source_url, self.parser_version, self.payload_json, self.id),
            )
        else:
            cursor = conn.execute(
                """
                INSERT INTO raw_extractions (
                    source_type, source_identifier, source_url, parser_version, payload_json
                ) VALUES (?, ?, ?, ?, ?)
                """,
                (self.source_type, self.source_identifier, self.source_url, self.parser_version, self.payload_json),
            )
            self.id = cursor.lastrowid

        conn.commit()
        return self

    @classmethod
    def count_by_source_type(cls, conn: sqlite3.Connection) -> dict:
        cursor = conn.execute(
            """
            SELECT source_type, COUNT(*) AS count
            FROM raw_extractions
            GROUP BY source_type
            ORDER BY source_type ASC
            """
        )
        return {row["source_type"]: row["count"] for row in cursor.fetchall()}


@dataclass
class AppUser:
    """Application user for protected routes."""

    id: Optional[int] = None
    username: str = ""
    password_hash: str = ""
    is_active: bool = True
    created_at: Optional[datetime] = None
    updated_at: Optional[datetime] = None

    @classmethod
    def _from_row(cls, row) -> "AppUser":
        return cls(
            id=row["id"],
            username=row["username"],
            password_hash=row["password_hash"],
            is_active=bool(row["is_active"]),
            created_at=row["created_at"],
            updated_at=row["updated_at"],
        )

    @classmethod
    def get_by_id(cls, conn: sqlite3.Connection, user_id: int) -> Optional["AppUser"]:
        row = conn.execute(
            """
            SELECT id, username, password_hash, is_active, created_at, updated_at
            FROM app_users
            WHERE id = ?
            """,
            (user_id,),
        ).fetchone()
        return cls._from_row(row) if row else None

    @classmethod
    def get_by_username(cls, conn: sqlite3.Connection, username: str) -> Optional["AppUser"]:
        row = conn.execute(
            """
            SELECT id, username, password_hash, is_active, created_at, updated_at
            FROM app_users
            WHERE username = ?
            """,
            (username,),
        ).fetchone()
        return cls._from_row(row) if row else None

    @classmethod
    def create_or_update_password(
        cls,
        conn: sqlite3.Connection,
        username: str,
        password: str,
        is_active: bool = True,
    ) -> "AppUser":
        password_hash = generate_password_hash(password)
        existing = cls.get_by_username(conn, username)
        if existing:
            conn.execute(
                """
                UPDATE app_users
                SET password_hash = ?, is_active = ?, updated_at = CURRENT_TIMESTAMP
                WHERE id = ?
                """,
                (password_hash, int(bool(is_active)), existing.id),
            )
            conn.commit()
            updated = cls.get_by_id(conn, existing.id)
            return updated

        cursor = conn.execute(
            """
            INSERT INTO app_users (username, password_hash, is_active)
            VALUES (?, ?, ?)
            """,
            (username, password_hash, int(bool(is_active))),
        )
        conn.commit()
        created = cls.get_by_id(conn, cursor.lastrowid)
        return created

    @classmethod
    def verify_credentials(cls, conn: sqlite3.Connection, username: str, password: str) -> Optional["AppUser"]:
        user = cls.get_by_username(conn, username)
        if not user or not user.is_active:
            return None
        if not check_password_hash(user.password_hash, password):
            return None
        return user


@dataclass
class ScrapeState:
    """Tracks scraping progress for resumability."""

    id: Optional[int] = None
    scrape_type: str = ""  # 'main_list' or 'details'
    last_page: Optional[int] = None
    last_report_id: Optional[int] = None
    total_pages: Optional[int] = None
    status: str = "pending"
    error_message: Optional[str] = None
    started_at: Optional[datetime] = None
    completed_at: Optional[datetime] = None
    updated_at: Optional[datetime] = None

    def save(self, conn: sqlite3.Connection) -> "ScrapeState":
        """Save the scrape state to the database."""
        if self.id:
            conn.execute(
                """
                UPDATE scrape_state SET
                    scrape_type = ?, last_page = ?, last_report_id = ?,
                    total_pages = ?, status = ?, error_message = ?,
                    started_at = ?, completed_at = ?, updated_at = CURRENT_TIMESTAMP
                WHERE id = ?
                """,
                (
                    self.scrape_type,
                    self.last_page,
                    self.last_report_id,
                    self.total_pages,
                    self.status,
                    self.error_message,
                    self.started_at,
                    self.completed_at,
                    self.id,
                ),
            )
        else:
            cursor = conn.execute(
                """
                INSERT INTO scrape_state (
                    scrape_type, last_page, last_report_id, total_pages,
                    status, error_message, started_at, completed_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    self.scrape_type,
                    self.last_page,
                    self.last_report_id,
                    self.total_pages,
                    self.status,
                    self.error_message,
                    self.started_at,
                    self.completed_at,
                ),
            )
            self.id = cursor.lastrowid
        conn.commit()
        return self

    @classmethod
    def get_latest(cls, conn: sqlite3.Connection, scrape_type: str) -> Optional["ScrapeState"]:
        """Get the latest scrape state for a given type."""
        cursor = conn.execute(
            """
            SELECT * FROM scrape_state
            WHERE scrape_type = ?
            ORDER BY id DESC
            LIMIT 1
            """,
            (scrape_type,),
        )
        row = cursor.fetchone()
        if row:
            return cls(
                id=row["id"],
                scrape_type=row["scrape_type"],
                last_page=row["last_page"],
                last_report_id=row["last_report_id"],
                total_pages=row["total_pages"],
                status=row["status"],
                error_message=row["error_message"],
                started_at=row["started_at"],
                completed_at=row["completed_at"],
                updated_at=row["updated_at"],
            )
        return None


@dataclass
class ManualEntryQueue:
    """Tracks paper-filed reports needing manual data entry."""

    id: Optional[int] = None
    report_id: Optional[int] = None
    priority: int = 0
    notes: Optional[str] = None
    status: str = "pending"
    assigned_to: Optional[str] = None
    created_at: Optional[datetime] = None
    completed_at: Optional[datetime] = None

    # Optional joined fields
    committee_name: Optional[str] = None
    report_type: Optional[str] = None
    filed_date: Optional[str] = None

    def save(self, conn: sqlite3.Connection) -> "ManualEntryQueue":
        """Save to database."""
        if self.id:
            conn.execute(
                """
                UPDATE manual_entry_queue SET
                    report_id = ?, priority = ?, notes = ?, status = ?,
                    assigned_to = ?, completed_at = ?
                WHERE id = ?
                """,
                (
                    self.report_id,
                    self.priority,
                    self.notes,
                    self.status,
                    self.assigned_to,
                    self.completed_at,
                    self.id,
                ),
            )
        else:
            cursor = conn.execute(
                """
                INSERT INTO manual_entry_queue (report_id, priority, notes, status, assigned_to)
                VALUES (?, ?, ?, ?, ?)
                """,
                (self.report_id, self.priority, self.notes, self.status, self.assigned_to),
            )
            self.id = cursor.lastrowid
        conn.commit()
        return self

    @classmethod
    def add_report(
        cls,
        conn: sqlite3.Connection,
        report_id: int,
        priority: int = 0,
        notes: str = None,
    ) -> Optional["ManualEntryQueue"]:
        """Add a report to the manual entry queue."""
        cursor = conn.execute(
            "SELECT id FROM manual_entry_queue WHERE report_id = ?",
            (report_id,),
        )
        if cursor.fetchone():
            return None

        entry = cls(report_id=report_id, priority=priority, notes=notes)
        return entry.save(conn)

    @classmethod
    def get_queue(
        cls,
        conn: sqlite3.Connection,
        status: str = "pending",
        limit: int = 100,
        offset: int = 0,
    ) -> List["ManualEntryQueue"]:
        """Get queue items with optional filtering."""
        cursor = conn.execute(
            """
            SELECT m.*, c.name as committee_name, r.report_type, r.filed_date
            FROM manual_entry_queue m
            JOIN reports r ON m.report_id = r.id
            JOIN committees c ON r.committee_id = c.id
            WHERE m.status = ?
            ORDER BY m.priority DESC, m.created_at ASC
            LIMIT ? OFFSET ?
            """,
            (status, limit, offset),
        )

        items = []
        for row in cursor.fetchall():
            item = cls(
                id=row["id"],
                report_id=row["report_id"],
                priority=row["priority"],
                notes=row["notes"],
                status=row["status"],
                assigned_to=row["assigned_to"],
                created_at=row["created_at"],
                completed_at=row["completed_at"],
            )
            item.committee_name = row["committee_name"]
            item.report_type = row["report_type"]
            item.filed_date = row["filed_date"]
            items.append(item)
        return items

    @classmethod
    def count_by_status(cls, conn: sqlite3.Connection) -> dict:
        """Get count of queue items by status."""
        cursor = conn.execute(
            """
            SELECT status, COUNT(*) as count
            FROM manual_entry_queue
            GROUP BY status
            """
        )
        return {row["status"]: row["count"] for row in cursor.fetchall()}
