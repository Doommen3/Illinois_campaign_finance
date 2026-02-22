"""Data models for Illinois Campaign Finance tracker."""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
import json
from typing import Optional, List, ClassVar
import sqlite3

from .identifiers import make_source_identifier, normalize_source_url
from werkzeug.security import check_password_hash, generate_password_hash


def _normalized_date_sql(column: str) -> str:
    """Normalize mixed TEXT date values (ISO, timestamps, MM/DD/YYYY) to DATE."""
    text_expr = f"TRIM(COALESCE(CAST({column} AS TEXT), ''))"
    first_slash_expr = f"INSTR({text_expr}, '/')"
    remainder_expr = f"SUBSTR({text_expr}, {first_slash_expr} + 1)"
    second_slash_expr = f"INSTR({remainder_expr}, '/')"
    month_expr = f"SUBSTR({text_expr}, 1, {first_slash_expr} - 1)"
    day_expr = f"SUBSTR({remainder_expr}, 1, {second_slash_expr} - 1)"
    year_expr = f"SUBSTR({remainder_expr}, {second_slash_expr} + 1, 4)"
    year_padded = (
        f"SUBSTR('0000' || CAST({year_expr} AS TEXT), "
        f"LENGTH('0000' || CAST({year_expr} AS TEXT)) - 3, 4)"
    )
    month_padded = (
        f"SUBSTR('00' || CAST({month_expr} AS TEXT), "
        f"LENGTH('00' || CAST({month_expr} AS TEXT)) - 1, 2)"
    )
    day_padded = (
        f"SUBSTR('00' || CAST({day_expr} AS TEXT), "
        f"LENGTH('00' || CAST({day_expr} AS TEXT)) - 1, 2)"
    )
    return f"""(
        CASE
            WHEN {text_expr} = '' THEN NULL
            WHEN {first_slash_expr} > 0 AND {second_slash_expr} > 0 THEN
                DATE(
                    {year_padded} || '-' || {month_padded} || '-' || {day_padded}
                )
            ELSE DATE(SUBSTR({text_expr}, 1, 10))
        END
    )"""


def _append_date_range_filters(
    clauses: List[str],
    params: List[object],
    date_expr: str,
    date_from: Optional[str] = None,
    date_to: Optional[str] = None,
) -> None:
    start = (date_from or "").strip()
    if start:
        clauses.append(f"{date_expr} >= DATE(?)")
        params.append(start[:10])

    end = (date_to or "").strip()
    if end:
        clauses.append(f"{date_expr} <= DATE(?)")
        params.append(end[:10])


@dataclass
class Committee:
    """Represents a committee (candidate/organization filing reports)."""

    id: Optional[int] = None
    name: str = ""
    committee_id_sbe: Optional[int] = None
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
            committee_id_sbe=row["committee_id_sbe"] if "committee_id_sbe" in row.keys() else None,
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
        committee_id_sbe: int | None = None,
    ) -> "Committee":
        """Get committee by source identifier or name, or create it."""
        normalized_url = normalize_source_url(detail_url)
        source_identifier = source_identifier or make_source_identifier(normalized_url, name)

        row = None
        if committee_id_sbe is not None:
            cursor = conn.execute(
                """
                SELECT id, name, committee_id_sbe, detail_url, source_identifier, created_at, updated_at
                FROM committees
                WHERE committee_id_sbe = ?
                """,
                (committee_id_sbe,),
            )
            row = cursor.fetchone()

        if not row and source_identifier:
            cursor = conn.execute(
                """
                SELECT id, name, committee_id_sbe, detail_url, source_identifier, created_at, updated_at
                FROM committees
                WHERE source_identifier = ?
                """,
                (source_identifier,),
            )
            row = cursor.fetchone()

        if not row and normalized_url:
            cursor = conn.execute(
                """
                SELECT id, name, committee_id_sbe, detail_url, source_identifier, created_at, updated_at
                FROM committees
                WHERE detail_url = ?
                """,
                (normalized_url,),
            )
            row = cursor.fetchone()

        if not row:
            cursor = conn.execute(
                """
                SELECT id, name, committee_id_sbe, detail_url, source_identifier, created_at, updated_at
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
            updated_committee_id_sbe = committee.committee_id_sbe
            if committee_id_sbe is not None and committee.committee_id_sbe is None:
                updated_committee_id_sbe = committee_id_sbe

            if (
                updated_detail_url != committee.detail_url
                or updated_source_id != committee.source_identifier
                or updated_committee_id_sbe != committee.committee_id_sbe
            ):
                conn.execute(
                    """
                    UPDATE committees
                    SET committee_id_sbe = ?, detail_url = ?, source_identifier = ?, updated_at = CURRENT_TIMESTAMP
                    WHERE id = ?
                    """,
                    (updated_committee_id_sbe, updated_detail_url, updated_source_id, committee.id),
                )
                conn.commit()
                committee.committee_id_sbe = updated_committee_id_sbe
                committee.detail_url = updated_detail_url
                committee.source_identifier = updated_source_id
            return committee

        cursor = conn.execute(
            """
            INSERT INTO committees (name, committee_id_sbe, detail_url, source_identifier)
            VALUES (?, ?, ?, ?)
            """,
            (name, committee_id_sbe, normalized_url, source_identifier),
        )
        conn.commit()
        return cls(
            id=cursor.lastrowid,
            name=name,
            committee_id_sbe=committee_id_sbe,
            detail_url=normalized_url,
            source_identifier=source_identifier,
        )

    @classmethod
    def get_by_sbe_id(cls, conn: sqlite3.Connection, committee_id_sbe: int) -> Optional["Committee"]:
        """Get a committee by Illinois SBE committee ID."""
        cursor = conn.execute(
            """
            SELECT id, name, committee_id_sbe, detail_url, source_identifier, created_at, updated_at
            FROM committees
            WHERE committee_id_sbe = ?
            """,
            (committee_id_sbe,),
        )
        row = cursor.fetchone()
        if row:
            return cls._from_row(row)
        return None

    @classmethod
    def get_by_id(cls, conn: sqlite3.Connection, committee_id: int) -> Optional["Committee"]:
        """Get a committee by ID."""
        cursor = conn.execute(
            """
            SELECT id, name, committee_id_sbe, detail_url, source_identifier, created_at, updated_at
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
        transaction_date_from: Optional[str] = None,
        transaction_date_to: Optional[str] = None,
    ) -> List["Committee"]:
        """Get all committees with pagination and sorting."""
        sort_map = {
            "name": "c.name",
            "created_at": "c.created_at",
            "total_contributions": "total_contributions",
        }
        sort_field = sort_map.get(sort_by, "c.name")
        direction = "ASC" if str(sort_dir).lower() == "asc" else "DESC"
        where_clauses: List[str] = []
        params: List[object] = []

        tx_date_expr = _normalized_date_sql("ct.transaction_date")
        _append_date_range_filters(
            where_clauses,
            params,
            tx_date_expr,
            date_from=transaction_date_from,
            date_to=transaction_date_to,
        )

        where_sql = f" WHERE {' AND '.join(where_clauses)}" if where_clauses else ""
        params.extend([limit, offset])

        cursor = conn.execute(
            f"""
            SELECT c.id, c.name, c.committee_id_sbe, c.detail_url, c.source_identifier, c.created_at, c.updated_at,
                   COALESCE(SUM(ct.amount), 0) AS total_contributions
            FROM committees c
            LEFT JOIN reports r ON r.committee_id = c.id
            LEFT JOIN contributions ct ON ct.report_id = r.id
            {where_sql}
            GROUP BY c.id
            ORDER BY {sort_field} {direction}, c.id ASC
            LIMIT ? OFFSET ?
            """,
            params,
        )
        return [cls._from_row(row) for row in cursor.fetchall()]

    @classmethod
    def count(
        cls,
        conn: sqlite3.Connection,
        transaction_date_from: Optional[str] = None,
        transaction_date_to: Optional[str] = None,
    ) -> int:
        """Get total count of committees."""
        where_clauses: List[str] = []
        params: List[object] = []
        tx_date_expr = _normalized_date_sql("ct.transaction_date")
        _append_date_range_filters(
            where_clauses,
            params,
            tx_date_expr,
            date_from=transaction_date_from,
            date_to=transaction_date_to,
        )

        if where_clauses:
            cursor = conn.execute(
                f"""
                SELECT COUNT(DISTINCT c.id) as count
                FROM committees c
                JOIN reports r ON r.committee_id = c.id
                JOIN contributions ct ON ct.report_id = r.id
                WHERE {' AND '.join(where_clauses)}
                """,
                params,
            )
        else:
            cursor = conn.execute("SELECT COUNT(*) as count FROM committees")
        return cursor.fetchone()["count"]

    @classmethod
    def search(
        cls,
        conn: sqlite3.Connection,
        query: str,
        limit: int = 100,
        transaction_date_from: Optional[str] = None,
        transaction_date_to: Optional[str] = None,
    ) -> List["Committee"]:
        """Search committees by name."""
        where_clauses: List[str] = ["c.name LIKE ?"]
        params: List[object] = [f"%{query}%"]
        tx_filter_clauses: List[str] = []
        _append_date_range_filters(
            tx_filter_clauses,
            params,
            _normalized_date_sql("ct.transaction_date"),
            date_from=transaction_date_from,
            date_to=transaction_date_to,
        )
        if tx_filter_clauses:
            where_clauses.append(
                f"""
                EXISTS (
                    SELECT 1
                    FROM reports r
                    JOIN contributions ct ON ct.report_id = r.id
                    WHERE r.committee_id = c.id
                      AND {' AND '.join(tx_filter_clauses)}
                )
                """
            )
        params.append(limit)
        cursor = conn.execute(
            f"""
            SELECT id, name, committee_id_sbe, detail_url, source_identifier, created_at, updated_at
            FROM committees c
            WHERE {' AND '.join(where_clauses)}
            ORDER BY c.name
            LIMIT ?
            """,
            params,
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
            WHERE r.scrape_status = 'pending' AND r.is_paper_filed = FALSE
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
        filed_date_from: Optional[str] = None,
        filed_date_to: Optional[str] = None,
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
        where_clauses: List[str] = []

        if paper_filed is not None:
            where_clauses.append("r.is_paper_filed = ?")
            params.append(bool(paper_filed))
        report_date_expr = _normalized_date_sql("r.filed_date")
        _append_date_range_filters(
            where_clauses,
            params,
            report_date_expr,
            date_from=filed_date_from,
            date_to=filed_date_to,
        )

        if where_clauses:
            query += f" WHERE {' AND '.join(where_clauses)}"

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
        filed_date_from: Optional[str] = None,
        filed_date_to: Optional[str] = None,
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
        where_clauses: List[str] = ["r.committee_id = ?"]
        params: List[object] = [committee_id]
        report_date_expr = _normalized_date_sql("r.filed_date")
        _append_date_range_filters(
            where_clauses,
            params,
            report_date_expr,
            date_from=filed_date_from,
            date_to=filed_date_to,
        )

        cursor = conn.execute(
            f"""
            SELECT r.*, c.name as committee_name
            FROM reports r
            JOIN committees c ON r.committee_id = c.id
            WHERE {' AND '.join(where_clauses)}
            ORDER BY {order_by} {direction}, r.id DESC
            LIMIT ? OFFSET ?
            """,
            [*params, limit, offset],
        )
        return [cls._from_row(row) for row in cursor.fetchall()]

    @classmethod
    def count(
        cls,
        conn: sqlite3.Connection,
        paper_filed: Optional[bool] = None,
        filed_date_from: Optional[str] = None,
        filed_date_to: Optional[str] = None,
    ) -> int:
        """Get total count of reports."""
        where_clauses: List[str] = []
        params: List[object] = []
        if paper_filed is not None:
            where_clauses.append("is_paper_filed = ?")
            params.append(bool(paper_filed))
        report_date_expr = _normalized_date_sql("filed_date")
        _append_date_range_filters(
            where_clauses,
            params,
            report_date_expr,
            date_from=filed_date_from,
            date_to=filed_date_to,
        )

        if where_clauses:
            cursor = conn.execute(
                f"SELECT COUNT(*) as count FROM reports WHERE {' AND '.join(where_clauses)}",
                params,
            )
        else:
            cursor = conn.execute("SELECT COUNT(*) as count FROM reports")
        return cursor.fetchone()["count"]

    @classmethod
    def count_by_committee(
        cls,
        conn: sqlite3.Connection,
        committee_id: int,
        filed_date_from: Optional[str] = None,
        filed_date_to: Optional[str] = None,
    ) -> int:
        """Count reports for a committee."""
        where_clauses: List[str] = ["committee_id = ?"]
        params: List[object] = [committee_id]
        report_date_expr = _normalized_date_sql("filed_date")
        _append_date_range_filters(
            where_clauses,
            params,
            report_date_expr,
            date_from=filed_date_from,
            date_to=filed_date_to,
        )
        cursor = conn.execute(
            f"SELECT COUNT(*) as count FROM reports WHERE {' AND '.join(where_clauses)}",
            params,
        )
        return cursor.fetchone()["count"]

    @classmethod
    def count_by_status(
        cls,
        conn: sqlite3.Connection,
        filed_date_from: Optional[str] = None,
        filed_date_to: Optional[str] = None,
    ) -> dict:
        """Get count of reports by scrape status."""
        where_clauses: List[str] = []
        params: List[object] = []
        report_date_expr = _normalized_date_sql("filed_date")
        _append_date_range_filters(
            where_clauses,
            params,
            report_date_expr,
            date_from=filed_date_from,
            date_to=filed_date_to,
        )
        where_sql = f" WHERE {' AND '.join(where_clauses)}" if where_clauses else ""
        cursor = conn.execute(
            f"""
            SELECT scrape_status, COUNT(*) as count
            FROM reports
            {where_sql}
            GROUP BY scrape_status
            """,
            params,
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
    entity_id: Optional[str] = None
    donor_city: Optional[str] = None
    donor_state: Optional[str] = None
    source: Optional[str] = None
    merge_action: Optional[str] = None
    entity_confidence_score: Optional[float] = None
    BULK_RECEIPTS_MATERIALIZATION_VERSION: ClassVar[int] = 2

    @classmethod
    def _table_exists(cls, conn: sqlite3.Connection, table_name: str) -> bool:
        row = conn.execute(
            "SELECT 1 FROM sqlite_master WHERE type IN ('table', 'view') AND name = ?",
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
    def _bulk_donor_key_sql(cls, alias: str = "r") -> str:
        return (
            f"LOWER(TRIM("
            f"COALESCE({alias}.first_name, '') || '|' || COALESCE({alias}.last_or_business_name, '') || '|' || "
            f"COALESCE({alias}.address_line_1, '') || '|' || COALESCE({alias}.address_line_2, '') || '|' || "
            f"COALESCE({alias}.city, '') || '|' || COALESCE({alias}.state, '') || '|' || COALESCE({alias}.postal_code, '')"
            f"))"
        )

    @classmethod
    def _bulk_donor_name_sql(cls, alias: str = "r") -> str:
        return (
            "TRIM("
            f"COALESCE({alias}.first_name, '')"
            " || CASE "
            f"WHEN COALESCE({alias}.first_name, '') <> '' AND COALESCE({alias}.last_or_business_name, '') <> '' "
            "THEN ' ' ELSE '' END"
            f" || COALESCE({alias}.last_or_business_name, '')"
            ")"
        )

    @classmethod
    def _bulk_donor_address_sql(cls, alias: str = "r") -> str:
        return (
            "TRIM("
            f"COALESCE({alias}.address_line_1, '')"
            " || CASE "
            f"WHEN COALESCE({alias}.address_line_1, '') <> '' AND COALESCE({alias}.address_line_2, '') <> '' "
            "THEN ', ' ELSE '' END"
            f" || COALESCE({alias}.address_line_2, '')"
            " || CASE "
            f"WHEN (COALESCE({alias}.address_line_1, '') <> '' OR COALESCE({alias}.address_line_2, '') <> '') "
            f" AND COALESCE({alias}.city, '') <> '' THEN ', ' ELSE '' END"
            f" || COALESCE({alias}.city, '')"
            " || CASE "
            f"WHEN COALESCE({alias}.state, '') <> '' THEN ', ' ELSE '' END"
            f" || COALESCE({alias}.state, '')"
            " || CASE "
            f"WHEN COALESCE({alias}.postal_code, '') <> '' THEN ' ' ELSE '' END"
            f" || COALESCE({alias}.postal_code, '')"
            ")"
        )

    @classmethod
    def _bulk_receipts_has_donor_key_columns(cls, conn: sqlite3.Connection) -> bool:
        required_columns = (
            "first_name",
            "last_or_business_name",
            "address_line_1",
            "address_line_2",
            "city",
            "state",
            "postal_code",
        )
        return cls._table_exists(conn, "bulk_receipts_clean") and all(
            cls._column_exists(conn, "bulk_receipts_clean", column_name)
            for column_name in required_columns
        )

    @classmethod
    def _bulk_receipts_base_filter_sql(cls, conn: sqlite3.Connection, alias: str = "r") -> str:
        clauses = [f"COALESCE({alias}.amount, 0) > 0"]
        if cls._column_exists(conn, "bulk_receipts_clean", "is_archived"):
            clauses.append(f"NOT COALESCE({alias}.is_archived::boolean, FALSE)")
        if cls._column_exists(conn, "bulk_receipts_clean", "d2_part_code"):
            clauses.append(
                f"(COALESCE({alias}.d2_part_code, '') = '' OR COALESCE({alias}.d2_part_code, '') LIKE '1%')"
            )
        return " AND ".join(clauses)

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
    def _local_entity_materialization_is_ready(cls, conn: sqlite3.Connection, source: str) -> bool:
        if source != "bulk_receipts":
            return False
        if not cls._table_exists(conn, "donor_entity_local"):
            return False
        if not cls._table_exists(conn, "donor_entity_local_member"):
            return False
        row = conn.execute(
            """
            SELECT 1
            FROM donor_entity_local
            WHERE source = ?
            LIMIT 1
            """,
            (source,),
        ).fetchone()
        return row is not None

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
        transaction_date_from: Optional[str] = None,
        transaction_date_to: Optional[str] = None,
    ) -> List["Donor"]:
        """Get all donors with aggregated contribution totals."""
        source = cls.get_directory_source(conn)
        has_date_window = bool((transaction_date_from or "").strip() or (transaction_date_to or "").strip())
        direction = "ASC" if str(sort_dir).lower() == "asc" else "DESC"

        if has_date_window:
            if (
                source == "bulk_receipts"
                and cls._bulk_receipts_has_donor_key_columns(conn)
            ):
                donor_key_expr = cls._bulk_donor_key_sql("r")
                donor_name_expr = cls._bulk_donor_name_sql("r")
                donor_address_expr = cls._bulk_donor_address_sql("r")
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
                    "created_at": "total_amount",
                }
                order_by = sort_map.get(sort_by, "total_amount")
                where_clauses: List[str] = [
                    cls._bulk_receipts_base_filter_sql(conn, "r"),
                    f"{donor_key_expr} <> ''",
                ]
                params: List[object] = []
                _append_date_range_filters(
                    where_clauses,
                    params,
                    _normalized_date_sql("r.received_date"),
                    date_from=transaction_date_from,
                    date_to=transaction_date_to,
                )
                params.extend([limit, offset])

                rows = conn.execute(
                    f"""
                    SELECT
                        'bulk_receipts' AS source,
                        {donor_key_expr} AS donor_key,
                        NULL AS local_donor_id,
                        {donor_name_expr} AS donor_name,
                        {donor_address_expr} AS donor_address,
                        COALESCE(NULLIF(TRIM(r.city), ''), NULL) AS donor_city,
                        COALESCE(NULLIF(TRIM(r.state), ''), NULL) AS donor_state,
                        COALESCE(NULLIF(TRIM(r.occupation), ''), NULL) AS occupation,
                        COALESCE(NULLIF(TRIM(r.employer), ''), NULL) AS employer,
                        COALESCE(SUM(r.amount), 0) AS total_amount,
                        COUNT(*) AS contribution_count,
                        COUNT(DISTINCT r.committee_id_sbe) AS committee_count
                    FROM bulk_receipts_clean r
                    WHERE {' AND '.join(where_clauses)}
                    GROUP BY donor_key, donor_name, donor_address, donor_city, donor_state, occupation, employer
                    ORDER BY {order_by} {direction}, donor_name ASC
                    LIMIT ? OFFSET ?
                    """,
                    params,
                ).fetchall()

                donors = []
                for row in rows:
                    donor = cls(
                        id=None,
                        name=row["donor_name"] or "Unknown Donor",
                        address=row["donor_address"] or None,
                        occupation=row["occupation"] or None,
                        employer=row["employer"] or None,
                        normalized_name="",
                        normalized_address=None,
                        created_at=None,
                    )
                    donor.total_amount = float(row["total_amount"] or 0.0)
                    donor.contribution_count = int(row["contribution_count"] or 0)
                    donor.committee_count = int(row["committee_count"] or 0)
                    donor.donor_key = row["donor_key"] or None
                    donor.donor_city = row["donor_city"] or None
                    donor.donor_state = row["donor_state"] or None
                    donor.source = "bulk_receipts"
                    donors.append(donor)
                return donors

            if source != "bulk_receipts":
                sort_map = {
                    "name": "d.name",
                    "address": "d.address",
                    "occupation": "d.occupation",
                    "employer": "d.employer",
                    "total_amount": "total_amount",
                    "contribution_count": "contribution_count",
                    "committee_count": "committee_count",
                    "created_at": "d.created_at",
                }
                order_by = sort_map.get(sort_by, "total_amount")
                where_clauses: List[str] = []
                params: List[object] = []
                _append_date_range_filters(
                    where_clauses,
                    params,
                    _normalized_date_sql("c.transaction_date"),
                    date_from=transaction_date_from,
                    date_to=transaction_date_to,
                )
                where_sql = f" WHERE {' AND '.join(where_clauses)}" if where_clauses else ""
                params.extend([limit, offset])
                cursor = conn.execute(
                    f"""
                    SELECT
                        d.*,
                        COALESCE(SUM(c.amount), 0) as total_amount,
                        COUNT(c.id) as contribution_count,
                        COUNT(DISTINCT r.committee_id) AS committee_count
                    FROM donors d
                    JOIN contributions c ON d.id = c.donor_id
                    LEFT JOIN reports r ON r.id = c.report_id
                    {where_sql}
                    GROUP BY d.id
                    ORDER BY {order_by} {direction}, d.id ASC
                    LIMIT ? OFFSET ?
                    """,
                    params,
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
                    donor.committee_count = row["committee_count"] or 0
                    donor.source = source or "contributions"
                    donors.append(donor)
                return donors

        if source:
            if cls._local_entity_materialization_is_ready(conn, source):
                # Keep entity mode fast by sorting on base entity columns only.
                # Per-row enrichments (anchor + counts) are computed only for the
                # selected page instead of the full entity population.
                sort_by_contribution_count = sort_by == "contribution_count"
                sort_map = {
                    "source": "e.source",
                    "name": "e.canonical_name",
                    "total_amount": "e.total_amount",
                    "created_at": "e.canonical_name",
                }
                final_sort_map = {
                    "source": "se.source",
                    "name": "donor_name",
                    "total_amount": "se.total_amount",
                    "created_at": "donor_name",
                }
                order_by = sort_map.get(sort_by, "e.total_amount")
                final_order_by = final_sort_map.get(sort_by, "se.total_amount")
                direction = "ASC" if str(sort_dir).lower() == "asc" else "DESC"
                if sort_by_contribution_count:
                    order_by = "sort_contribution_count"
                    final_order_by = "COALESCE(se.sort_contribution_count, 0)"

                cte_prefix = ""
                selected_entities_join = ""
                selected_entities_extra = ", 0 AS sort_contribution_count"
                query_params: tuple = (source, limit, offset)
                if sort_by_contribution_count:
                    cte_prefix = """
                    entity_contribution_totals AS (
                        SELECT
                            m.source,
                            m.entity_id,
                            COALESCE(SUM(m.contribution_count), 0) AS contribution_count
                        FROM donor_entity_local_member m
                        WHERE m.source = ?
                        GROUP BY m.source, m.entity_id
                    ),
                    """
                    selected_entities_join = """
                        LEFT JOIN entity_contribution_totals ect
                          ON ect.source = e.source
                         AND ect.entity_id = e.entity_id
                    """
                    selected_entities_extra = ", COALESCE(ect.contribution_count, 0) AS sort_contribution_count"
                    query_params = (source, source, limit, offset)
                rows = conn.execute(
                    f"""
                    WITH
                    {cte_prefix}
                    selected_entities AS (
                        SELECT
                            e.entity_id,
                            e.source,
                            e.canonical_name,
                            e.display_name,
                            e.total_amount,
                            e.merge_action,
                            e.confidence_score
                            {selected_entities_extra}
                        FROM donor_entity_local e
                        {selected_entities_join}
                        WHERE e.source = ?
                        ORDER BY {order_by} {direction}, e.canonical_name ASC
                        LIMIT ? OFFSET ?
                    ),
                    selected_members AS (
                        SELECT
                            m.source,
                            m.entity_id,
                            m.donor_key,
                            m.donor_name,
                            m.donor_city,
                            m.donor_state,
                            m.total_amount,
                            m.contribution_count,
                            ROW_NUMBER() OVER (
                                PARTITION BY m.entity_id
                                ORDER BY m.total_amount DESC, m.donor_key ASC
                            ) AS rn
                        FROM donor_entity_local_member m
                        JOIN selected_entities se
                          ON se.source = m.source
                         AND se.entity_id = m.entity_id
                    ),
                    member_totals AS (
                        SELECT
                            entity_id,
                            COALESCE(SUM(contribution_count), 0) AS contribution_count
                        FROM selected_members
                        GROUP BY entity_id
                    ),
                    committee_totals AS (
                        SELECT
                            sm.entity_id,
                            COUNT(DISTINCT a.committee_id) AS committee_count
                        FROM selected_members sm
                        LEFT JOIN analytics_donor_committee_agg a
                            ON a.source = sm.source
                           AND a.donor_key = sm.donor_key
                        GROUP BY sm.entity_id
                    )
                    SELECT
                        se.source AS source,
                        se.entity_id AS entity_id,
                        COALESCE(NULLIF(se.display_name, ''), NULLIF(anchor.donor_name, ''), se.canonical_name) AS donor_name,
                        COALESCE(summary.donor_address, '') AS donor_address,
                        COALESCE(summary.occupation, '') AS occupation,
                        COALESCE(summary.employer, '') AS employer,
                        COALESCE(anchor.donor_city, '') AS donor_city,
                        COALESCE(anchor.donor_state, '') AS donor_state,
                        COALESCE(se.total_amount, 0) AS total_amount,
                        COALESCE(mt.contribution_count, 0) AS contribution_count,
                        COALESCE(ct.committee_count, 0) AS committee_count,
                        COALESCE(anchor.donor_key, '') AS anchor_donor_key,
                        se.merge_action AS merge_action,
                        se.confidence_score AS confidence_score,
                        COALESCE(se.sort_contribution_count, 0) AS sort_contribution_count
                    FROM selected_entities se
                    LEFT JOIN selected_members anchor
                        ON anchor.entity_id = se.entity_id
                       AND anchor.rn = 1
                    LEFT JOIN analytics_donor_summary summary
                        ON summary.source = se.source
                       AND summary.donor_key = anchor.donor_key
                    LEFT JOIN member_totals mt ON mt.entity_id = se.entity_id
                    LEFT JOIN committee_totals ct ON ct.entity_id = se.entity_id
                    ORDER BY {final_order_by} {direction}, donor_name ASC
                    """,
                    query_params,
                ).fetchall()
                donors = []
                for row in rows:
                    donor = cls(
                        id=None,
                        name=row["donor_name"] or "Unknown Donor",
                        address=row["donor_address"] or None,
                        occupation=row["occupation"] or None,
                        employer=row["employer"] or None,
                        normalized_name="",
                        normalized_address=None,
                        created_at=None,
                    )
                    donor.total_amount = float(row["total_amount"] or 0.0)
                    donor.contribution_count = int(row["contribution_count"] or 0)
                    donor.committee_count = int(row["committee_count"] or 0)
                    donor.donor_key = row["anchor_donor_key"] or None
                    donor.entity_id = row["entity_id"] or None
                    donor.donor_city = row["donor_city"] or None
                    donor.donor_state = row["donor_state"] or None
                    donor.source = row["source"]
                    donor.merge_action = row["merge_action"] or None
                    donor.entity_confidence_score = (
                        float(row["confidence_score"]) if row["confidence_score"] is not None else None
                    )
                    donors.append(donor)
                return donors

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
            "committee_count": "committee_count",
            "created_at": "d.created_at",
        }
        order_by = sort_map.get(sort_by, "total_amount")

        cursor = conn.execute(
            f"""
            SELECT
                d.*,
                COALESCE(SUM(c.amount), 0) as total_amount,
                COUNT(c.id) as contribution_count,
                COUNT(DISTINCT r.committee_id) AS committee_count
            FROM donors d
            LEFT JOIN contributions c ON d.id = c.donor_id
            LEFT JOIN reports r ON r.id = c.report_id
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
            donor.committee_count = row["committee_count"] or 0
            donors.append(donor)
        return donors

    @classmethod
    def count(
        cls,
        conn: sqlite3.Connection,
        transaction_date_from: Optional[str] = None,
        transaction_date_to: Optional[str] = None,
    ) -> int:
        """Get total count of donors."""
        source = cls.get_directory_source(conn)
        has_date_window = bool((transaction_date_from or "").strip() or (transaction_date_to or "").strip())

        if has_date_window:
            if (
                source == "bulk_receipts"
                and cls._bulk_receipts_has_donor_key_columns(conn)
            ):
                donor_key_expr = cls._bulk_donor_key_sql("r")
                where_clauses: List[str] = [
                    cls._bulk_receipts_base_filter_sql(conn, "r"),
                    f"{donor_key_expr} <> ''",
                ]
                params: List[object] = []
                _append_date_range_filters(
                    where_clauses,
                    params,
                    _normalized_date_sql("r.received_date"),
                    date_from=transaction_date_from,
                    date_to=transaction_date_to,
                )
                row = conn.execute(
                    f"""
                    SELECT COUNT(*) as count
                    FROM (
                        SELECT {donor_key_expr} AS donor_key
                        FROM bulk_receipts_clean r
                        WHERE {' AND '.join(where_clauses)}
                        GROUP BY donor_key
                    )
                    """,
                    params,
                ).fetchone()
                return int(row["count"] or 0) if row else 0

            if source != "bulk_receipts":
                where_clauses: List[str] = []
                params: List[object] = []
                _append_date_range_filters(
                    where_clauses,
                    params,
                    _normalized_date_sql("c.transaction_date"),
                    date_from=transaction_date_from,
                    date_to=transaction_date_to,
                )
                where_sql = f" WHERE {' AND '.join(where_clauses)}" if where_clauses else ""
                row = conn.execute(
                    f"""
                    SELECT COUNT(*) as count
                    FROM (
                        SELECT d.id
                        FROM donors d
                        JOIN contributions c ON d.id = c.donor_id
                        {where_sql}
                        GROUP BY d.id
                    )
                    """,
                    params,
                ).fetchone()
                return int(row["count"] or 0) if row else 0

        if source:
            if cls._local_entity_materialization_is_ready(conn, source):
                cursor = conn.execute(
                    """
                    SELECT COUNT(*) as count
                    FROM donor_entity_local
                    WHERE source = ?
                    """,
                    (source,),
                )
                return cursor.fetchone()["count"]

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
    def search(
        cls,
        conn: sqlite3.Connection,
        query: str,
        limit: int = 100,
        transaction_date_from: Optional[str] = None,
        transaction_date_to: Optional[str] = None,
    ) -> List["Donor"]:
        """Search donors by name."""
        where_clauses: List[str] = ["(d.name LIKE ? OR d.normalized_name LIKE ?)"]
        params: List[object] = [f"%{query}%", f"%{query}%"]
        _append_date_range_filters(
            where_clauses,
            params,
            _normalized_date_sql("c.transaction_date"),
            date_from=transaction_date_from,
            date_to=transaction_date_to,
        )
        params.append(limit)
        cursor = conn.execute(
            f"""
            SELECT d.*, SUM(c.amount) as total_amount, COUNT(c.id) as contribution_count
            FROM donors d
            LEFT JOIN contributions c ON d.id = c.donor_id
            WHERE {' AND '.join(where_clauses)}
            GROUP BY d.id
            ORDER BY total_amount DESC
            LIMIT ?
            """,
            params,
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
        date_from: Optional[str] = None,
        date_to: Optional[str] = None,
    ) -> Optional["Donor"]:
        """Get donor summary row by stable donor key."""
        if not donor_key:
            return None

        has_date_window = bool((date_from or "").strip() or (date_to or "").strip())
        resolved_source = source or cls.get_directory_source(conn) or "bulk_receipts"
        if (
            has_date_window
            and resolved_source == "bulk_receipts"
            and cls._bulk_receipts_has_donor_key_columns(conn)
        ):
            donor_key_expr = cls._bulk_donor_key_sql("r")
            donor_name_expr = cls._bulk_donor_name_sql("r")
            donor_address_expr = cls._bulk_donor_address_sql("r")
            where_clauses: List[str] = [
                cls._bulk_receipts_base_filter_sql(conn, "r"),
                f"{donor_key_expr} = ?",
            ]
            params: List[object] = [donor_key]
            _append_date_range_filters(
                where_clauses,
                params,
                _normalized_date_sql("r.received_date"),
                date_from=date_from,
                date_to=date_to,
            )
            row = conn.execute(
                f"""
                SELECT
                    'bulk_receipts' AS source,
                    {donor_key_expr} AS donor_key,
                    NULL AS local_donor_id,
                    {donor_name_expr} AS donor_name,
                    {donor_address_expr} AS donor_address,
                    COALESCE(NULLIF(TRIM(r.city), ''), NULL) AS donor_city,
                    COALESCE(NULLIF(TRIM(r.state), ''), NULL) AS donor_state,
                    COALESCE(NULLIF(TRIM(r.occupation), ''), NULL) AS occupation,
                    COALESCE(NULLIF(TRIM(r.employer), ''), NULL) AS employer,
                    COALESCE(SUM(r.amount), 0) AS total_amount,
                    COUNT(*) AS contribution_count,
                    COUNT(DISTINCT r.committee_id_sbe) AS committee_count
                FROM bulk_receipts_clean r
                WHERE {' AND '.join(where_clauses)}
                GROUP BY donor_key, donor_name, donor_address, donor_city, donor_state, occupation, employer
                ORDER BY total_amount DESC
                LIMIT 1
                """,
                params,
            ).fetchone()
            if row:
                donor = cls(
                    id=None,
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
            return None

        if not cls._table_exists(conn, "analytics_donor_summary"):
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
        date_from: Optional[str] = None,
        date_to: Optional[str] = None,
        limit: int = 100,
        offset: int = 0,
        sort_by: str = "amount",
        sort_dir: str = "desc",
    ) -> List[dict]:
        """Get committee-level totals for a donor key."""
        has_date_window = bool((date_from or "").strip() or (date_to or "").strip())
        if (
            has_date_window
            and donor_key
            and source == "bulk_receipts"
            and cls._bulk_receipts_has_donor_key_columns(conn)
        ):
            sort_map = {
                "committee": "committee_name",
                "amount": "total_amount",
                "contribution_count": "contribution_count",
            }
            order_by = sort_map.get(sort_by, "total_amount")
            direction = "ASC" if str(sort_dir).lower() == "asc" else "DESC"
            donor_key_expr = cls._bulk_donor_key_sql("r")
            committee_name_expr = (
                "COALESCE(MAX(c.committee_name), 'Committee ' || r.committee_id_sbe)"
                if cls._table_exists(conn, "bulk_committees_clean")
                else "'Committee ' || r.committee_id_sbe"
            )
            join_sql = (
                "LEFT JOIN bulk_committees_clean c ON c.committee_id_sbe = r.committee_id_sbe"
                if cls._table_exists(conn, "bulk_committees_clean")
                else ""
            )
            where_clauses: List[str] = [
                cls._bulk_receipts_base_filter_sql(conn, "r"),
                f"{donor_key_expr} = ?",
            ]
            params: List[object] = [donor_key]
            _append_date_range_filters(
                where_clauses,
                params,
                _normalized_date_sql("r.received_date"),
                date_from=date_from,
                date_to=date_to,
            )
            rows = conn.execute(
                f"""
                SELECT
                    CAST(r.committee_id_sbe AS TEXT) AS committee_id,
                    {committee_name_expr} AS committee_name,
                    COALESCE(SUM(r.amount), 0) AS total_amount,
                    COUNT(*) AS contribution_count
                FROM bulk_receipts_clean r
                {join_sql}
                WHERE {' AND '.join(where_clauses)}
                GROUP BY r.committee_id_sbe
                ORDER BY {order_by} {direction}, committee_name ASC
                LIMIT ? OFFSET ?
                """,
                [*params, limit, offset],
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
        date_from: Optional[str] = None,
        date_to: Optional[str] = None,
    ) -> int:
        """Count committee rows available for a donor key."""
        has_date_window = bool((date_from or "").strip() or (date_to or "").strip())
        if (
            has_date_window
            and donor_key
            and source == "bulk_receipts"
            and cls._bulk_receipts_has_donor_key_columns(conn)
        ):
            donor_key_expr = cls._bulk_donor_key_sql("r")
            where_clauses: List[str] = [
                cls._bulk_receipts_base_filter_sql(conn, "r"),
                f"{donor_key_expr} = ?",
            ]
            params: List[object] = [donor_key]
            _append_date_range_filters(
                where_clauses,
                params,
                _normalized_date_sql("r.received_date"),
                date_from=date_from,
                date_to=date_to,
            )
            row = conn.execute(
                f"""
                SELECT COUNT(*) AS count
                FROM (
                    SELECT r.committee_id_sbe
                    FROM bulk_receipts_clean r
                    WHERE {' AND '.join(where_clauses)}
                    GROUP BY r.committee_id_sbe
                )
                """,
                params,
            ).fetchone()
            return int(row["count"] or 0) if row else 0

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

    @classmethod
    def get_summary_by_entity(
        cls,
        conn: sqlite3.Connection,
        entity_id: str,
        source: Optional[str] = None,
        date_from: Optional[str] = None,
        date_to: Optional[str] = None,
    ) -> Optional["Donor"]:
        """Get donor summary row by local donor entity id."""
        if (
            not entity_id
            or not cls._table_exists(conn, "donor_entity_local")
            or not cls._table_exists(conn, "donor_entity_local_member")
        ):
            return None

        resolved_source = source or cls.get_directory_source(conn) or "bulk_receipts"
        row = conn.execute(
            """
            SELECT
                e.source AS source,
                e.entity_id AS entity_id,
                COALESCE(NULLIF(e.display_name, ''), NULLIF(anchor.donor_name, ''), e.canonical_name) AS donor_name,
                COALESCE(summary.donor_address, '') AS donor_address,
                COALESCE(summary.occupation, '') AS occupation,
                COALESCE(summary.employer, '') AS employer,
                COALESCE(anchor.donor_city, '') AS donor_city,
                COALESCE(anchor.donor_state, '') AS donor_state,
                COALESCE(e.total_amount, 0) AS total_amount,
                COALESCE((
                    SELECT SUM(m2.contribution_count)
                    FROM donor_entity_local_member m2
                    WHERE m2.source = e.source
                      AND m2.entity_id = e.entity_id
                ), 0) AS contribution_count,
                COALESCE((
                    SELECT COUNT(DISTINCT a.committee_id)
                    FROM donor_entity_local_member m3
                    LEFT JOIN analytics_donor_committee_agg a
                        ON a.source = m3.source
                       AND a.donor_key = m3.donor_key
                    WHERE m3.source = e.source
                      AND m3.entity_id = e.entity_id
                ), 0) AS committee_count,
                COALESCE(anchor.donor_key, '') AS anchor_donor_key,
                e.merge_action AS merge_action,
                e.confidence_score AS confidence_score
            FROM donor_entity_local e
            LEFT JOIN donor_entity_local_member anchor
                ON anchor.source = e.source
               AND anchor.entity_id = e.entity_id
               AND anchor.donor_key = (
                    SELECT m4.donor_key
                    FROM donor_entity_local_member m4
                    WHERE m4.source = e.source
                      AND m4.entity_id = e.entity_id
                    ORDER BY m4.total_amount DESC, m4.donor_key ASC
                    LIMIT 1
               )
            LEFT JOIN analytics_donor_summary summary
                ON summary.source = e.source
               AND summary.donor_key = anchor.donor_key
            WHERE e.source = ?
              AND e.entity_id = ?
            LIMIT 1
            """,
            (resolved_source, entity_id),
        ).fetchone()

        if not row:
            return None

        donor = cls(
            id=None,
            name=row["donor_name"] or "Unknown Donor",
            address=row["donor_address"] or None,
            occupation=row["occupation"] or None,
            employer=row["employer"] or None,
            normalized_name="",
            normalized_address=None,
            created_at=None,
        )
        donor.total_amount = float(row["total_amount"] or 0.0)
        donor.contribution_count = int(row["contribution_count"] or 0)
        donor.committee_count = int(row["committee_count"] or 0)
        donor.donor_key = row["anchor_donor_key"] or None
        donor.entity_id = row["entity_id"] or None
        donor.donor_city = row["donor_city"] or None
        donor.donor_state = row["donor_state"] or None
        donor.source = row["source"] or resolved_source
        donor.merge_action = row["merge_action"] or None
        donor.entity_confidence_score = (
            float(row["confidence_score"]) if row["confidence_score"] is not None else None
        )

        has_date_window = bool((date_from or "").strip() or (date_to or "").strip())
        if (
            has_date_window
            and donor.source == "bulk_receipts"
            and cls._bulk_receipts_has_donor_key_columns(conn)
        ):
            donor_key_expr = cls._bulk_donor_key_sql("r")
            where_clauses: List[str] = [
                "m.source = ?",
                "m.entity_id = ?",
                cls._bulk_receipts_base_filter_sql(conn, "r"),
            ]
            params: List[object] = [donor.source, donor.entity_id or entity_id]
            _append_date_range_filters(
                where_clauses,
                params,
                _normalized_date_sql("r.received_date"),
                date_from=date_from,
                date_to=date_to,
            )
            agg_row = conn.execute(
                f"""
                SELECT
                    COALESCE(SUM(r.amount), 0) AS total_amount,
                    COUNT(*) AS contribution_count,
                    COUNT(DISTINCT r.committee_id_sbe) AS committee_count
                FROM donor_entity_local_member m
                JOIN bulk_receipts_clean r
                  ON {donor_key_expr} = m.donor_key
                WHERE {' AND '.join(where_clauses)}
                """,
                params,
            ).fetchone()
            donor.total_amount = float(agg_row["total_amount"] or 0.0) if agg_row else 0.0
            donor.contribution_count = int(agg_row["contribution_count"] or 0) if agg_row else 0
            donor.committee_count = int(agg_row["committee_count"] or 0) if agg_row else 0

        return donor

    @classmethod
    def get_committee_breakdown_by_entity(
        cls,
        conn: sqlite3.Connection,
        entity_id: str,
        source: str,
        date_from: Optional[str] = None,
        date_to: Optional[str] = None,
        limit: int = 100,
        offset: int = 0,
        sort_by: str = "amount",
        sort_dir: str = "desc",
    ) -> List[dict]:
        """Get committee-level totals for a local donor entity."""
        has_date_window = bool((date_from or "").strip() or (date_to or "").strip())
        if (
            has_date_window
            and entity_id
            and source == "bulk_receipts"
            and cls._table_exists(conn, "donor_entity_local_member")
            and cls._bulk_receipts_has_donor_key_columns(conn)
        ):
            sort_map = {
                "committee": "committee_name",
                "amount": "total_amount",
                "contribution_count": "contribution_count",
            }
            order_by = sort_map.get(sort_by, "total_amount")
            direction = "ASC" if str(sort_dir).lower() == "asc" else "DESC"
            donor_key_expr = cls._bulk_donor_key_sql("r")
            committee_name_expr = (
                "COALESCE(MAX(c.committee_name), 'Committee ' || r.committee_id_sbe)"
                if cls._table_exists(conn, "bulk_committees_clean")
                else "'Committee ' || r.committee_id_sbe"
            )
            join_sql = (
                "LEFT JOIN bulk_committees_clean c ON c.committee_id_sbe = r.committee_id_sbe"
                if cls._table_exists(conn, "bulk_committees_clean")
                else ""
            )
            where_clauses: List[str] = [
                "m.source = ?",
                "m.entity_id = ?",
                cls._bulk_receipts_base_filter_sql(conn, "r"),
            ]
            params: List[object] = [source, entity_id]
            _append_date_range_filters(
                where_clauses,
                params,
                _normalized_date_sql("r.received_date"),
                date_from=date_from,
                date_to=date_to,
            )
            rows = conn.execute(
                f"""
                SELECT
                    CAST(r.committee_id_sbe AS TEXT) AS committee_id,
                    {committee_name_expr} AS committee_name,
                    COALESCE(SUM(r.amount), 0) AS total_amount,
                    COUNT(*) AS contribution_count
                FROM donor_entity_local_member m
                JOIN bulk_receipts_clean r
                  ON {donor_key_expr} = m.donor_key
                {join_sql}
                WHERE {' AND '.join(where_clauses)}
                GROUP BY r.committee_id_sbe
                ORDER BY {order_by} {direction}, committee_name ASC
                LIMIT ? OFFSET ?
                """,
                [*params, limit, offset],
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

        if (
            not entity_id
            or not source
            or not cls._table_exists(conn, "donor_entity_local_member")
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
                a.committee_id AS committee_id,
                a.committee_name AS committee_name,
                COALESCE(SUM(a.total_amount), 0) AS total_amount,
                COALESCE(SUM(a.contribution_count), 0) AS contribution_count
            FROM donor_entity_local_member m
            JOIN analytics_donor_committee_agg a
              ON a.source = m.source
             AND a.donor_key = m.donor_key
            WHERE m.source = ?
              AND m.entity_id = ?
            GROUP BY a.committee_id, a.committee_name
            ORDER BY {order_by} {direction}, committee_name ASC
            LIMIT ? OFFSET ?
            """,
            (source, entity_id, limit, offset),
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
    def count_committee_breakdown_by_entity(
        cls,
        conn: sqlite3.Connection,
        entity_id: str,
        source: str,
        date_from: Optional[str] = None,
        date_to: Optional[str] = None,
    ) -> int:
        """Count committee rows available for a donor entity."""
        has_date_window = bool((date_from or "").strip() or (date_to or "").strip())
        if (
            has_date_window
            and entity_id
            and source == "bulk_receipts"
            and cls._table_exists(conn, "donor_entity_local_member")
            and cls._bulk_receipts_has_donor_key_columns(conn)
        ):
            donor_key_expr = cls._bulk_donor_key_sql("r")
            where_clauses: List[str] = [
                "m.source = ?",
                "m.entity_id = ?",
                cls._bulk_receipts_base_filter_sql(conn, "r"),
            ]
            params: List[object] = [source, entity_id]
            _append_date_range_filters(
                where_clauses,
                params,
                _normalized_date_sql("r.received_date"),
                date_from=date_from,
                date_to=date_to,
            )
            row = conn.execute(
                f"""
                SELECT COUNT(*) AS count
                FROM (
                    SELECT r.committee_id_sbe
                    FROM donor_entity_local_member m
                    JOIN bulk_receipts_clean r
                      ON {donor_key_expr} = m.donor_key
                    WHERE {' AND '.join(where_clauses)}
                    GROUP BY r.committee_id_sbe
                )
                """,
                params,
            ).fetchone()
            return int(row["count"] or 0) if row else 0

        if (
            not entity_id
            or not source
            or not cls._table_exists(conn, "donor_entity_local_member")
            or not cls._table_exists(conn, "analytics_donor_committee_agg")
        ):
            return 0
        row = conn.execute(
            """
            SELECT COUNT(*) AS count
            FROM (
                SELECT a.committee_id
                FROM donor_entity_local_member m
                JOIN analytics_donor_committee_agg a
                  ON a.source = m.source
                 AND a.donor_key = m.donor_key
                WHERE m.source = ?
                  AND m.entity_id = ?
                GROUP BY a.committee_id
            )
            """,
            (source, entity_id),
        ).fetchone()
        return int(row["count"] or 0) if row else 0

    @classmethod
    def _normalize_review_status_filter(cls, status: Optional[str]) -> str:
        normalized = (status or "pending").strip().lower()
        if normalized in {"all", "pending", "approved", "rejected", "not_needed"}:
            return normalized
        return "pending"

    @classmethod
    def get_local_entity_review_status_counts(
        cls,
        conn: sqlite3.Connection,
        source: str = "bulk_receipts",
    ) -> dict:
        """Count review-queue entities by derived review status."""
        if (
            not source
            or not cls._table_exists(conn, "donor_entity_local")
            or not cls._table_exists(conn, "donor_entity_local_member")
        ):
            return {"all": 0, "pending": 0, "approved": 0, "rejected": 0, "not_needed": 0}

        row = conn.execute(
            """
            WITH entity_status AS (
                SELECT
                    e.entity_id,
                    SUM(CASE WHEN m.review_status = 'pending' THEN 1 ELSE 0 END) AS pending_count,
                    SUM(CASE WHEN m.review_status = 'approved' THEN 1 ELSE 0 END) AS approved_count,
                    SUM(CASE WHEN m.review_status = 'rejected' THEN 1 ELSE 0 END) AS rejected_count,
                    SUM(CASE WHEN m.review_status = 'not_needed' THEN 1 ELSE 0 END) AS not_needed_count
                FROM donor_entity_local e
                JOIN donor_entity_local_member m
                  ON m.source = e.source
                 AND m.entity_id = e.entity_id
                WHERE e.source = ?
                  AND e.merge_action = 'review'
                  AND m.merge_action = 'review'
                GROUP BY e.entity_id
            )
            SELECT
                COUNT(*) AS all_count,
                SUM(CASE WHEN rejected_count > 0 THEN 1 ELSE 0 END) AS rejected_count,
                SUM(
                    CASE
                        WHEN rejected_count = 0
                         AND pending_count = 0
                         AND approved_count > 0
                        THEN 1 ELSE 0
                    END
                ) AS approved_count,
                SUM(
                    CASE
                        WHEN rejected_count = 0
                         AND pending_count = 0
                         AND approved_count = 0
                         AND not_needed_count > 0
                        THEN 1 ELSE 0
                    END
                ) AS not_needed_count,
                SUM(
                    CASE
                        WHEN rejected_count = 0
                         AND (
                            pending_count > 0
                            OR (pending_count = 0 AND approved_count = 0 AND not_needed_count = 0)
                         )
                        THEN 1 ELSE 0
                    END
                ) AS pending_count
            FROM entity_status
            """,
            (source,),
        ).fetchone()
        if not row:
            return {"all": 0, "pending": 0, "approved": 0, "rejected": 0, "not_needed": 0}
        return {
            "all": int(row["all_count"] or 0),
            "pending": int(row["pending_count"] or 0),
            "approved": int(row["approved_count"] or 0),
            "rejected": int(row["rejected_count"] or 0),
            "not_needed": int(row["not_needed_count"] or 0),
        }

    @classmethod
    def get_local_entity_review_queue(
        cls,
        conn: sqlite3.Connection,
        source: str = "bulk_receipts",
        status: str = "pending",
        query: Optional[str] = None,
        limit: int = 50,
        offset: int = 0,
        sort_by: str = "total_amount",
        sort_dir: str = "desc",
    ) -> List[dict]:
        """Return paginated review-queue entities with status rollups."""
        if (
            not source
            or not cls._table_exists(conn, "donor_entity_local")
            or not cls._table_exists(conn, "donor_entity_local_member")
        ):
            return []

        sort_map = {
            "name": "canonical_name",
            "total_amount": "total_amount",
            "member_count": "member_count",
            "confidence": "confidence_score",
            "confidence_score": "confidence_score",
            "pending_count": "pending_count",
            "approved_count": "approved_count",
        }
        order_by = sort_map.get(sort_by, "total_amount")
        direction = "ASC" if str(sort_dir).lower() == "asc" else "DESC"
        status_filter = cls._normalize_review_status_filter(status)
        query_text = (query or "").strip().lower()
        query_pattern = f"%{query_text}%"

        rows = conn.execute(
            f"""
            WITH entity_status AS (
                SELECT
                    e.entity_id,
                    e.source,
                    e.canonical_name,
                    e.display_name,
                    e.member_count,
                    e.total_amount,
                    e.confidence_score,
                    e.peak_confidence_score,
                    e.confidence_tier,
                    e.merge_action,
                    e.method_version,
                    SUM(CASE WHEN m.review_status = 'pending' THEN 1 ELSE 0 END) AS pending_count,
                    SUM(CASE WHEN m.review_status = 'approved' THEN 1 ELSE 0 END) AS approved_count,
                    SUM(CASE WHEN m.review_status = 'rejected' THEN 1 ELSE 0 END) AS rejected_count,
                    SUM(CASE WHEN m.review_status = 'not_needed' THEN 1 ELSE 0 END) AS not_needed_count
                FROM donor_entity_local e
                JOIN donor_entity_local_member m
                  ON m.source = e.source
                 AND m.entity_id = e.entity_id
                WHERE e.source = ?
                  AND e.merge_action = 'review'
                  AND m.merge_action = 'review'
                GROUP BY
                    e.entity_id,
                    e.source,
                    e.canonical_name,
                    e.display_name,
                    e.member_count,
                    e.total_amount,
                    e.confidence_score,
                    e.peak_confidence_score,
                    e.confidence_tier,
                    e.merge_action,
                    e.method_version
            ),
            tagged AS (
                SELECT
                    entity_id,
                    source,
                    canonical_name,
                    display_name,
                    member_count,
                    total_amount,
                    confidence_score,
                    peak_confidence_score,
                    confidence_tier,
                    merge_action,
                    method_version,
                    pending_count,
                    approved_count,
                    rejected_count,
                    not_needed_count,
                    CASE
                        WHEN rejected_count > 0 THEN 'rejected'
                        WHEN pending_count = 0 AND approved_count > 0 THEN 'approved'
                        WHEN pending_count = 0 AND approved_count = 0 AND not_needed_count > 0 THEN 'not_needed'
                        ELSE 'pending'
                    END AS entity_review_status
                FROM entity_status
            )
            SELECT
                entity_id,
                source,
                canonical_name,
                display_name,
                member_count,
                total_amount,
                confidence_score,
                peak_confidence_score,
                confidence_tier,
                merge_action,
                method_version,
                pending_count,
                approved_count,
                rejected_count,
                not_needed_count,
                entity_review_status
            FROM tagged
            WHERE (
                    ? = ''
                    OR canonical_name LIKE ?
                    OR COALESCE(display_name, '') LIKE ?
                  )
              AND (
                    ? = 'all'
                    OR entity_review_status = ?
                  )
            ORDER BY {order_by} {direction}, total_amount DESC, canonical_name ASC
            LIMIT ? OFFSET ?
            """,
            (
                source,
                query_text,
                query_pattern,
                query_pattern,
                status_filter,
                status_filter,
                limit,
                offset,
            ),
        ).fetchall()

        queue_rows = []
        for row in rows:
            queue_rows.append(
                {
                    "entity_id": row["entity_id"],
                    "source": row["source"],
                    "canonical_name": row["canonical_name"],
                    "display_name": row["display_name"],
                    "member_count": int(row["member_count"] or 0),
                    "total_amount": float(row["total_amount"] or 0.0),
                    "confidence_score": float(row["confidence_score"] or 0.0),
                    "peak_confidence_score": float(row["peak_confidence_score"] or 0.0),
                    "confidence_tier": row["confidence_tier"],
                    "merge_action": row["merge_action"],
                    "method_version": row["method_version"],
                    "pending_count": int(row["pending_count"] or 0),
                    "approved_count": int(row["approved_count"] or 0),
                    "rejected_count": int(row["rejected_count"] or 0),
                    "not_needed_count": int(row["not_needed_count"] or 0),
                    "entity_review_status": row["entity_review_status"],
                }
            )
        return queue_rows

    @classmethod
    def count_local_entity_review_queue(
        cls,
        conn: sqlite3.Connection,
        source: str = "bulk_receipts",
        status: str = "pending",
        query: Optional[str] = None,
    ) -> int:
        """Count review-queue entities with filters."""
        if (
            not source
            or not cls._table_exists(conn, "donor_entity_local")
            or not cls._table_exists(conn, "donor_entity_local_member")
        ):
            return 0

        status_filter = cls._normalize_review_status_filter(status)
        query_text = (query or "").strip().lower()
        query_pattern = f"%{query_text}%"

        row = conn.execute(
            """
            WITH entity_status AS (
                SELECT
                    e.entity_id,
                    e.canonical_name,
                    e.display_name,
                    SUM(CASE WHEN m.review_status = 'pending' THEN 1 ELSE 0 END) AS pending_count,
                    SUM(CASE WHEN m.review_status = 'approved' THEN 1 ELSE 0 END) AS approved_count,
                    SUM(CASE WHEN m.review_status = 'rejected' THEN 1 ELSE 0 END) AS rejected_count,
                    SUM(CASE WHEN m.review_status = 'not_needed' THEN 1 ELSE 0 END) AS not_needed_count
                FROM donor_entity_local e
                JOIN donor_entity_local_member m
                  ON m.source = e.source
                 AND m.entity_id = e.entity_id
                WHERE e.source = ?
                  AND e.merge_action = 'review'
                  AND m.merge_action = 'review'
                GROUP BY e.entity_id, e.canonical_name, e.display_name
            ),
            tagged AS (
                SELECT
                    entity_id,
                    canonical_name,
                    display_name,
                    CASE
                        WHEN rejected_count > 0 THEN 'rejected'
                        WHEN pending_count = 0 AND approved_count > 0 THEN 'approved'
                        WHEN pending_count = 0 AND approved_count = 0 AND not_needed_count > 0 THEN 'not_needed'
                        ELSE 'pending'
                    END AS entity_review_status
                FROM entity_status
            )
            SELECT COUNT(*) AS count
            FROM tagged
            WHERE (
                    ? = ''
                    OR canonical_name LIKE ?
                    OR COALESCE(display_name, '') LIKE ?
                  )
              AND (
                    ? = 'all'
                    OR entity_review_status = ?
                  )
            """,
            (
                source,
                query_text,
                query_pattern,
                query_pattern,
                status_filter,
                status_filter,
            ),
        ).fetchone()
        return int(row["count"] or 0) if row else 0

    @classmethod
    def get_local_entity_review_entity(
        cls,
        conn: sqlite3.Connection,
        entity_id: str,
        source: str = "bulk_receipts",
    ) -> Optional[dict]:
        """Get one local donor entity with member review counts."""
        if (
            not entity_id
            or not source
            or not cls._table_exists(conn, "donor_entity_local")
            or not cls._table_exists(conn, "donor_entity_local_member")
        ):
            return None

        row = conn.execute(
            """
            SELECT
                e.entity_id,
                e.source,
                e.canonical_name,
                e.display_name,
                e.member_count,
                e.total_amount,
                e.confidence_score,
                e.peak_confidence_score,
                e.confidence_tier,
                e.merge_action,
                e.method_version,
                COALESCE(SUM(CASE WHEN m.review_status = 'pending' THEN 1 ELSE 0 END), 0) AS pending_count,
                COALESCE(SUM(CASE WHEN m.review_status = 'approved' THEN 1 ELSE 0 END), 0) AS approved_count,
                COALESCE(SUM(CASE WHEN m.review_status = 'rejected' THEN 1 ELSE 0 END), 0) AS rejected_count,
                COALESCE(SUM(CASE WHEN m.review_status = 'not_needed' THEN 1 ELSE 0 END), 0) AS not_needed_count
            FROM donor_entity_local e
            LEFT JOIN donor_entity_local_member m
              ON m.source = e.source
             AND m.entity_id = e.entity_id
            WHERE e.source = ?
              AND e.entity_id = ?
            GROUP BY
                e.entity_id,
                e.source,
                e.canonical_name,
                e.display_name,
                e.member_count,
                e.total_amount,
                e.confidence_score,
                e.peak_confidence_score,
                e.confidence_tier,
                e.merge_action,
                e.method_version
            LIMIT 1
            """,
            (source, entity_id),
        ).fetchone()
        if not row:
            return None

        pending_count = int(row["pending_count"] or 0)
        approved_count = int(row["approved_count"] or 0)
        rejected_count = int(row["rejected_count"] or 0)
        not_needed_count = int(row["not_needed_count"] or 0)
        entity_status = "pending"
        if rejected_count > 0:
            entity_status = "rejected"
        elif pending_count == 0 and approved_count > 0:
            entity_status = "approved"
        elif pending_count == 0 and approved_count == 0 and not_needed_count > 0:
            entity_status = "not_needed"

        return {
            "entity_id": row["entity_id"],
            "source": row["source"],
            "canonical_name": row["canonical_name"],
            "display_name": row["display_name"],
            "member_count": int(row["member_count"] or 0),
            "total_amount": float(row["total_amount"] or 0.0),
            "confidence_score": float(row["confidence_score"] or 0.0),
            "peak_confidence_score": float(row["peak_confidence_score"] or 0.0),
            "confidence_tier": row["confidence_tier"],
            "merge_action": row["merge_action"],
            "method_version": row["method_version"],
            "pending_count": pending_count,
            "approved_count": approved_count,
            "rejected_count": rejected_count,
            "not_needed_count": not_needed_count,
            "entity_review_status": entity_status,
        }

    @classmethod
    def get_local_entity_review_members(
        cls,
        conn: sqlite3.Connection,
        entity_id: str,
        source: str = "bulk_receipts",
    ) -> List[dict]:
        """Get all member rows for an entity review."""
        if (
            not entity_id
            or not source
            or not cls._table_exists(conn, "donor_entity_local_member")
        ):
            return []

        rows = conn.execute(
            """
            SELECT
                m.source,
                m.entity_id,
                m.donor_key,
                m.canonical_name,
                m.donor_name,
                m.donor_city,
                m.donor_state,
                m.donor_zip5,
                m.confidence_score,
                m.confidence_tier,
                m.merge_action,
                m.total_amount,
                m.contribution_count,
                m.committee_count,
                m.review_status,
                m.reasons_json,
                m.method_version,
                COALESCE(summary.donor_address, '') AS donor_address,
                COALESCE(summary.occupation, '') AS occupation,
                COALESCE(summary.employer, '') AS employer
            FROM donor_entity_local_member m
            LEFT JOIN analytics_donor_summary summary
              ON summary.source = m.source
             AND summary.donor_key = m.donor_key
            WHERE m.source = ?
              AND m.entity_id = ?
            ORDER BY m.total_amount DESC, m.donor_key ASC
            """,
            (source, entity_id),
        ).fetchall()

        members = []
        for row in rows:
            reasons = []
            if row["reasons_json"]:
                try:
                    parsed = json.loads(row["reasons_json"])
                    if isinstance(parsed, list):
                        reasons = [str(value) for value in parsed if value]
                    elif isinstance(parsed, dict):
                        for key, value in parsed.items():
                            if isinstance(value, bool):
                                if value:
                                    reasons.append(str(key).replace("_", " "))
                            elif isinstance(value, list):
                                if value:
                                    reasons.append(f"{key}: {', '.join(str(item) for item in value if item)}")
                            elif value not in (None, ""):
                                reasons.append(f"{key}: {value}")
                except (ValueError, TypeError):
                    reasons = []

            members.append(
                {
                    "source": row["source"],
                    "entity_id": row["entity_id"],
                    "donor_key": row["donor_key"],
                    "canonical_name": row["canonical_name"],
                    "donor_name": row["donor_name"],
                    "donor_city": row["donor_city"],
                    "donor_state": row["donor_state"],
                    "donor_zip5": row["donor_zip5"],
                    "confidence_score": float(row["confidence_score"] or 0.0),
                    "confidence_tier": row["confidence_tier"],
                    "merge_action": row["merge_action"],
                    "total_amount": float(row["total_amount"] or 0.0),
                    "contribution_count": int(row["contribution_count"] or 0),
                    "committee_count": int(row["committee_count"] or 0),
                    "review_status": row["review_status"] or "pending",
                    "reasons_json": row["reasons_json"],
                    "reasons": reasons,
                    "method_version": row["method_version"],
                    "donor_address": row["donor_address"] or None,
                    "occupation": row["occupation"] or None,
                    "employer": row["employer"] or None,
                }
            )
        return members

    @classmethod
    def update_local_entity_review_status(
        cls,
        conn: sqlite3.Connection,
        *,
        source: str,
        entity_id: str,
        review_status: str,
        donor_key: Optional[str] = None,
    ) -> int:
        """Update member review status for one entity (or a single donor_key)."""
        normalized_status = cls._normalize_review_status_filter(review_status)
        if normalized_status == "all":
            normalized_status = "pending"

        if (
            not source
            or not entity_id
            or not cls._table_exists(conn, "donor_entity_local_member")
        ):
            return 0

        if donor_key:
            cursor = conn.execute(
                """
                UPDATE donor_entity_local_member
                SET review_status = ?, updated_at = CURRENT_TIMESTAMP
                WHERE source = ?
                  AND entity_id = ?
                  AND donor_key = ?
                  AND merge_action = 'review'
                """,
                (normalized_status, source, entity_id, donor_key),
            )
        else:
            cursor = conn.execute(
                """
                UPDATE donor_entity_local_member
                SET review_status = ?, updated_at = CURRENT_TIMESTAMP
                WHERE source = ?
                  AND entity_id = ?
                  AND merge_action = 'review'
                """,
                (normalized_status, source, entity_id),
            )

        updated_count = int(cursor.rowcount or 0)
        if updated_count > 0 and cls._table_exists(conn, "donor_entity_local"):
            conn.execute(
                """
                UPDATE donor_entity_local
                SET updated_at = CURRENT_TIMESTAMP
                WHERE source = ?
                  AND entity_id = ?
                """,
                (source, entity_id),
            )
        conn.commit()
        return updated_count


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
        transaction_date_from: Optional[str] = None,
        transaction_date_to: Optional[str] = None,
        sort_by: str = "filed_date",
        sort_dir: str = "desc",
    ) -> List["Contribution"]:
        """Get contributions from a specific donor."""
        tx_date_expr = _normalized_date_sql("c.transaction_date")
        sort_map = {
            "committee": "cm.name",
            "amount": "c.amount",
            "filed_date": tx_date_expr,
            "transaction_date": tx_date_expr,
            "description": "c.description",
        }
        order_by = sort_map.get(sort_by, tx_date_expr)
        direction = "ASC" if str(sort_dir).lower() == "asc" else "DESC"
        where_clauses: List[str] = ["c.donor_id = ?"]
        params: List[object] = [donor_id]
        _append_date_range_filters(
            where_clauses,
            params,
            tx_date_expr,
            date_from=transaction_date_from,
            date_to=transaction_date_to,
        )

        cursor = conn.execute(
            f"""
            SELECT c.*, d.name as donor_name, cm.name as committee_name, r.filed_date
            FROM contributions c
            JOIN donors d ON c.donor_id = d.id
            JOIN reports r ON c.report_id = r.id
            JOIN committees cm ON r.committee_id = cm.id
            WHERE {' AND '.join(where_clauses)}
            ORDER BY {order_by} {direction}, c.id DESC
            LIMIT ? OFFSET ?
            """,
            [*params, limit, offset],
        )
        return [cls._from_row(row) for row in cursor.fetchall()]

    @classmethod
    def get_by_committee(
        cls,
        conn: sqlite3.Connection,
        committee_id: int,
        limit: int = 100,
        offset: int = 0,
        transaction_date_from: Optional[str] = None,
        transaction_date_to: Optional[str] = None,
        sort_by: str = "filed_date",
        sort_dir: str = "desc",
    ) -> List["Contribution"]:
        """Get contributions for a specific committee."""
        tx_date_expr = _normalized_date_sql("c.transaction_date")
        sort_map = {
            "donor": "d.name",
            "amount": "c.amount",
            "filed_date": tx_date_expr,
            "transaction_date": tx_date_expr,
            "description": "c.description",
        }
        order_by = sort_map.get(sort_by, tx_date_expr)
        direction = "ASC" if str(sort_dir).lower() == "asc" else "DESC"
        where_clauses: List[str] = ["r.committee_id = ?"]
        params: List[object] = [committee_id]
        _append_date_range_filters(
            where_clauses,
            params,
            tx_date_expr,
            date_from=transaction_date_from,
            date_to=transaction_date_to,
        )

        cursor = conn.execute(
            f"""
            SELECT c.*, d.name as donor_name, r.filed_date
            FROM contributions c
            JOIN donors d ON c.donor_id = d.id
            JOIN reports r ON c.report_id = r.id
            WHERE {' AND '.join(where_clauses)}
            ORDER BY {order_by} {direction}, c.id DESC
            LIMIT ? OFFSET ?
            """,
            [*params, limit, offset],
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
    def total_by_committee(
        cls,
        conn: sqlite3.Connection,
        committee_id: int,
        transaction_date_from: Optional[str] = None,
        transaction_date_to: Optional[str] = None,
    ) -> float:
        """Get total amount for a committee."""
        tx_date_expr = _normalized_date_sql("c.transaction_date")
        where_clauses: List[str] = ["r.committee_id = ?"]
        params: List[object] = [committee_id]
        _append_date_range_filters(
            where_clauses,
            params,
            tx_date_expr,
            date_from=transaction_date_from,
            date_to=transaction_date_to,
        )
        cursor = conn.execute(
            f"""
            SELECT COALESCE(SUM(c.amount), 0) as total
            FROM contributions c
            JOIN reports r ON c.report_id = r.id
            WHERE {' AND '.join(where_clauses)}
            """,
            params,
        )
        return cursor.fetchone()["total"]

    @classmethod
    def total_by_donor(
        cls,
        conn: sqlite3.Connection,
        donor_id: int,
        transaction_date_from: Optional[str] = None,
        transaction_date_to: Optional[str] = None,
    ) -> float:
        """Get total amount from a donor."""
        tx_date_expr = _normalized_date_sql("transaction_date")
        where_clauses: List[str] = ["donor_id = ?"]
        params: List[object] = [donor_id]
        _append_date_range_filters(
            where_clauses,
            params,
            tx_date_expr,
            date_from=transaction_date_from,
            date_to=transaction_date_to,
        )
        cursor = conn.execute(
            f"SELECT COALESCE(SUM(amount), 0) as total FROM contributions WHERE {' AND '.join(where_clauses)}",
            params,
        )
        return cursor.fetchone()["total"]

    @classmethod
    def count_by_donor(
        cls,
        conn: sqlite3.Connection,
        donor_id: int,
        transaction_date_from: Optional[str] = None,
        transaction_date_to: Optional[str] = None,
    ) -> int:
        """Count contributions for a donor within an optional transaction-date window."""
        tx_date_expr = _normalized_date_sql("transaction_date")
        where_clauses: List[str] = ["donor_id = ?"]
        params: List[object] = [donor_id]
        _append_date_range_filters(
            where_clauses,
            params,
            tx_date_expr,
            date_from=transaction_date_from,
            date_to=transaction_date_to,
        )
        cursor = conn.execute(
            f"SELECT COUNT(*) as count FROM contributions WHERE {' AND '.join(where_clauses)}",
            params,
        )
        return int(cursor.fetchone()["count"] or 0)


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

    # Columns available in ISBE fallback (excludes period/cycle since they're always NULL)
    _ISBE_FALLBACK_COLUMNS: ClassVar[set[str]] = {
        "candidate_id", "candidate_full_name", "office_sought", "district_type",
        "district", "candidate_party_affiliation", "committee_id_sbe",
        "committee_name", "committee_type", "committee_party_affiliation",
        "filing_count", "sum_total_receipts", "sum_total_expenditures",
        "max_ending_funds_available", "archived_filing_count",
    }

    @classmethod
    def _table_exists_raw(cls, conn: sqlite3.Connection, table_name: str) -> bool:
        row = conn.execute(
            "SELECT 1 FROM sqlite_master WHERE type IN ('table', 'view') AND name = ?",
            (table_name,),
        ).fetchone()
        return row is not None

    @classmethod
    def _table_exists(cls, conn: sqlite3.Connection) -> bool:
        return cls._table_exists_raw(conn, cls.TABLE_NAME)

    @classmethod
    def _resolve_source(cls, conn: sqlite3.Connection) -> tuple[str | None, str | None]:
        """Determine best available data source.

        Returns (source_type, table_expr):
        - ("bulk", "bulk_candidate_committee_finance_agg")
        - ("isbe_fallback", "(SELECT ...) AS _cf")
        - (None, None)
        """
        if cls._table_exists(conn):
            return ("bulk", cls.TABLE_NAME)

        needed = ["isbe_candidate_committees", "isbe_candidates", "isbe_committees"]
        if all(cls._table_exists_raw(conn, t) for t in needed):
            has_d2 = cls._table_exists_raw(conn, "isbe_d2_reports")
            return ("isbe_fallback", cls._build_isbe_fallback_subquery(has_d2))

        return (None, None)

    @classmethod
    def _build_isbe_fallback_subquery(cls, has_d2: bool) -> str:
        d2_join = "LEFT JOIN isbe_d2_reports d2 ON d2.committee_id = cc.committee_id" if has_d2 else ""
        fc = "COUNT(DISTINCT CASE WHEN COALESCE(d2.archived, FALSE) = FALSE THEN d2.filed_doc_id END)" if has_d2 else "0"
        tr = "COALESCE(SUM(CASE WHEN COALESCE(d2.archived, FALSE) = FALSE THEN COALESCE(d2.total_receipts, 0) ELSE 0 END), 0)" if has_d2 else "0"
        te = "COALESCE(SUM(CASE WHEN COALESCE(d2.archived, FALSE) = FALSE THEN COALESCE(d2.total_expenditures, 0) ELSE 0 END), 0)" if has_d2 else "0"
        ef = "COALESCE(MAX(CASE WHEN COALESCE(d2.archived, FALSE) = FALSE THEN d2.end_funds_available END), 0)" if has_d2 else "0"
        ac = "COALESCE(SUM(CASE WHEN d2.archived = TRUE THEN 1 ELSE 0 END), 0)" if has_d2 else "0"
        return f"""(
            SELECT
                cc.candidate_id,
                TRIM(COALESCE(ca.first_name, '') || ' ' || COALESCE(ca.last_name, '')) AS candidate_full_name,
                ca.office AS office_sought,
                ca.district_type,
                ca.district,
                ca.party AS candidate_party_affiliation,
                cc.committee_id AS committee_id_sbe,
                c.name AS committee_name,
                c.type AS committee_type,
                c.party AS committee_party_affiliation,
                NULL AS period_year,
                NULL AS election_cycle,
                {fc} AS filing_count,
                {tr} AS sum_total_receipts,
                {te} AS sum_total_expenditures,
                {ef} AS max_ending_funds_available,
                {ac} AS archived_filing_count,
                NULL AS period_start_date,
                NULL AS period_end_date
            FROM isbe_candidate_committees cc
            JOIN isbe_candidates ca ON ca.id = cc.candidate_id
            JOIN isbe_committees c ON c.id = cc.committee_id
            {d2_join}
            GROUP BY cc.candidate_id, ca.first_name, ca.last_name, ca.office,
                     ca.district_type, ca.district, ca.party,
                     cc.committee_id, c.name, c.type, c.party
        ) AS _cf"""

    @classmethod
    def is_available(cls, conn: sqlite3.Connection) -> bool:
        """Return True when any valid candidate finance source is present."""
        source_type, _ = cls._resolve_source(conn)
        return source_type is not None

    @classmethod
    def source_type(cls, conn: sqlite3.Connection) -> str | None:
        """Return 'bulk' or 'isbe_fallback' or None."""
        return cls._resolve_source(conn)[0]

    @classmethod
    def _column_names(cls, conn: sqlite3.Connection) -> set[str]:
        source_type, _ = cls._resolve_source(conn)
        if source_type == "isbe_fallback":
            return cls._ISBE_FALLBACK_COLUMNS
        if source_type == "bulk":
            rows = conn.execute(f"PRAGMA table_info({cls.TABLE_NAME})").fetchall()
            return {row["name"] for row in rows if row and row["name"]}
        return set()

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
        end_date: Optional[str] = None,
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

        end_date_term = (end_date or "").strip()
        if end_date_term:
            if "period_end_date" in available_columns:
                clauses.append("DATE(period_end_date) <= DATE(?)")
                params.append(end_date_term)
            elif "period_year" in available_columns and len(end_date_term) >= 4:
                try:
                    clauses.append("period_year <= ?")
                    params.append(int(end_date_term[:4]))
                except ValueError:
                    pass
            elif has_bulk_receipts_table:
                clauses.append(
                    """
                    committee_id_sbe IN (
                        SELECT DISTINCT committee_id_sbe
                        FROM bulk_receipts_clean
                        WHERE DATE(received_date) <= DATE(?)
                    )
                    """
                )
                params.append(end_date_term)

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
        end_date: Optional[str] = None,
        year: Optional[int] = None,
        cycle: Optional[int] = None,
        min_receipts: Optional[float] = None,
        min_expenditures: Optional[float] = None,
    ) -> int:
        """Count rows in the candidate-committee finance aggregate table."""
        source_type, table_expr = cls._resolve_source(conn)
        if source_type is None:
            return 0

        available_columns = cls._column_names(conn)
        has_bulk_receipts_table = cls._table_exists_raw(conn, "bulk_receipts_clean")
        where_sql, params = cls._build_filter_sql(
            available_columns=available_columns,
            has_bulk_receipts_table=has_bulk_receipts_table,
            search=search,
            office=office,
            candidate_party=candidate_party,
            committee_party=committee_party,
            start_date=start_date,
            end_date=end_date,
            year=year,
            cycle=cycle,
            min_receipts=min_receipts,
            min_expenditures=min_expenditures,
        )
        query = f"SELECT COUNT(*) AS count FROM {table_expr}{where_sql}"

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
        end_date: Optional[str] = None,
        year: Optional[int] = None,
        cycle: Optional[int] = None,
        min_receipts: Optional[float] = None,
        min_expenditures: Optional[float] = None,
    ) -> List["CandidateCommitteeFinanceAgg"]:
        """Fetch paginated candidate-committee aggregate rows with sorting."""
        source_type, table_expr = cls._resolve_source(conn)
        if source_type is None:
            return []

        available_columns = cls._column_names(conn)
        has_bulk_receipts_table = cls._table_exists_raw(conn, "bulk_receipts_clean")
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
            FROM {table_expr}
        """
        where_sql, params = cls._build_filter_sql(
            available_columns=available_columns,
            has_bulk_receipts_table=has_bulk_receipts_table,
            search=search,
            office=office,
            candidate_party=candidate_party,
            committee_party=committee_party,
            start_date=start_date,
            end_date=end_date,
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
        source_type, table_expr = cls._resolve_source(conn)
        if source_type is None:
            return {"years": [], "cycles": []}

        columns = cls._column_names(conn)
        years: List[int] = []
        cycles: List[int] = []

        if "period_year" in columns:
            year_rows = conn.execute(
                f"""
                SELECT DISTINCT period_year
                FROM {table_expr}
                WHERE period_year IS NOT NULL
                ORDER BY period_year DESC
                """
            ).fetchall()
            years = [int(row["period_year"]) for row in year_rows if row["period_year"] is not None]

        if "election_cycle" in columns:
            cycle_rows = conn.execute(
                f"""
                SELECT DISTINCT election_cycle
                FROM {table_expr}
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
            "SELECT 1 FROM sqlite_master WHERE type IN ('table', 'view') AND name = ?",
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
        date_from: Optional[str] = None,
        date_to: Optional[str] = None,
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

        date_from_term = (date_from or "").strip()
        if date_from_term:
            clauses.append("DATE(r.received_date) >= DATE(?)")
            params.append(date_from_term)

        date_to_term = (date_to or "").strip()
        if date_to_term:
            clauses.append("DATE(r.received_date) <= DATE(?)")
            params.append(date_to_term)

        archived_term = (archived or "no").strip().lower()
        if archived_term == "yes":
            clauses.append("COALESCE(r.is_archived::boolean, FALSE)")
        elif archived_term == "no":
            clauses.append("NOT COALESCE(r.is_archived::boolean, FALSE)")

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
        date_from: Optional[str] = None,
        date_to: Optional[str] = None,
    ) -> int:
        if not cls.is_available(conn):
            return 0

        filter_sql, filter_params = cls._build_filter_sql(
            search=search,
            d2_part=d2_part,
            min_amount=min_amount,
            max_amount=max_amount,
            archived=archived,
            date_from=date_from,
            date_to=date_to,
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
        date_from: Optional[str] = None,
        date_to: Optional[str] = None,
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
            date_from=date_from,
            date_to=date_to,
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
class CandidateCommitteeItemizedExpenditure:
    """Itemized expenditure row for a candidate/committee pair."""

    expenditure_record_id: Optional[int] = None
    committee_id_sbe: Optional[int] = None
    filed_doc_id: Optional[int] = None
    expended_date: Optional[str] = None
    d2_part_code: Optional[str] = None
    payee_name: Optional[str] = None
    payee_address: Optional[str] = None
    amount: float = 0.0
    aggregate_amount: float = 0.0
    purpose: Optional[str] = None
    candidate_name: Optional[str] = None
    office: Optional[str] = None
    is_supporting: Optional[int] = None
    is_opposing: Optional[int] = None
    is_archived: Optional[int] = None
    country: Optional[str] = None
    redaction_requested: Optional[int] = None
    is_amount_anomalous: Optional[int] = None
    anomaly_reason: Optional[str] = None

    EXPENDITURES_TABLE = "bulk_expenditures_clean"
    LINKS_TABLE = "bulk_committee_candidate_links"

    @classmethod
    def _table_exists(cls, conn: sqlite3.Connection, table_name: str) -> bool:
        row = conn.execute(
            "SELECT 1 FROM sqlite_master WHERE type IN ('table', 'view') AND name = ?",
            (table_name,),
        ).fetchone()
        return row is not None

    @classmethod
    def is_available(cls, conn: sqlite3.Connection) -> bool:
        required_tables = [
            cls.EXPENDITURES_TABLE,
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
        anomalies_only: str = "no",
        date_from: Optional[str] = None,
        date_to: Optional[str] = None,
    ) -> tuple[str, List[object]]:
        clauses: List[str] = []
        params: List[object] = []

        search_term = (search or "").strip()
        if search_term:
            like_term = f"%{search_term}%"
            clauses.append(
                """
                (
                    COALESCE(e.payee_last_or_business_name, '') LIKE ?
                    OR COALESCE(e.payee_first_name, '') LIKE ?
                    OR COALESCE(e.purpose, '') LIKE ?
                    OR COALESCE(e.candidate_name, '') LIKE ?
                    OR COALESCE(e.office, '') LIKE ?
                    OR CAST(COALESCE(e.filed_doc_id, '') AS TEXT) LIKE ?
                )
                """
            )
            params.extend([like_term, like_term, like_term, like_term, like_term, like_term])

        d2_part_term = (d2_part or "").strip()
        if d2_part_term:
            clauses.append("COALESCE(e.d2_part_code, '') LIKE ?")
            params.append(f"{d2_part_term}%")

        if min_amount is not None:
            clauses.append("COALESCE(e.amount, 0) >= ?")
            params.append(float(min_amount))

        if max_amount is not None:
            clauses.append("COALESCE(e.amount, 0) <= ?")
            params.append(float(max_amount))

        date_from_term = (date_from or "").strip()
        if date_from_term:
            clauses.append("DATE(e.expended_date) >= DATE(?)")
            params.append(date_from_term)

        date_to_term = (date_to or "").strip()
        if date_to_term:
            clauses.append("DATE(e.expended_date) <= DATE(?)")
            params.append(date_to_term)

        archived_term = (archived or "no").strip().lower()
        if archived_term == "yes":
            clauses.append("COALESCE(e.is_archived::boolean, FALSE)")
        elif archived_term == "no":
            clauses.append("NOT COALESCE(e.is_archived::boolean, FALSE)")

        anomalies_term = (anomalies_only or "no").strip().lower()
        if anomalies_term in {"yes", "true", "1"}:
            clauses.append("COALESCE(e.is_amount_anomalous, 0) = 1")

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
        anomalies_only: str = "no",
        date_from: Optional[str] = None,
        date_to: Optional[str] = None,
    ) -> int:
        if not cls.is_available(conn):
            return 0

        filter_sql, filter_params = cls._build_filter_sql(
            search=search,
            d2_part=d2_part,
            min_amount=min_amount,
            max_amount=max_amount,
            archived=archived,
            anomalies_only=anomalies_only,
            date_from=date_from,
            date_to=date_to,
        )
        row = conn.execute(
            f"""
            SELECT COUNT(*) AS count
            FROM {cls.EXPENDITURES_TABLE} e
            WHERE e.committee_id_sbe = ?
              AND EXISTS (
                  SELECT 1
                  FROM {cls.LINKS_TABLE} l
                  WHERE l.candidate_id = ?
                    AND l.committee_id_sbe = e.committee_id_sbe
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
        sort_by: str = "expended_date",
        sort_dir: str = "desc",
        search: Optional[str] = None,
        d2_part: Optional[str] = None,
        min_amount: Optional[float] = None,
        max_amount: Optional[float] = None,
        archived: str = "no",
        anomalies_only: str = "no",
        date_from: Optional[str] = None,
        date_to: Optional[str] = None,
    ) -> List["CandidateCommitteeItemizedExpenditure"]:
        if not cls.is_available(conn):
            return []

        sort_map = {
            "expenditure_record_id": "e.expenditure_record_id",
            "filed_doc_id": "e.filed_doc_id",
            "expended_date": "e.expended_date",
            "d2_part_code": "e.d2_part_code",
            "payee_name": "COALESCE(e.payee_last_or_business_name, '') || ' ' || COALESCE(e.payee_first_name, '')",
            "amount": "e.amount",
            "aggregate_amount": "e.aggregate_amount",
            "purpose": "e.purpose",
            "candidate_name": "e.candidate_name",
            "office": "e.office",
            "is_supporting": "e.is_supporting",
            "is_opposing": "e.is_opposing",
            "is_archived": "e.is_archived",
            "is_amount_anomalous": "e.is_amount_anomalous",
        }
        order_by = sort_map.get(sort_by, "e.expended_date")
        direction = "ASC" if str(sort_dir).lower() == "asc" else "DESC"

        filter_sql, filter_params = cls._build_filter_sql(
            search=search,
            d2_part=d2_part,
            min_amount=min_amount,
            max_amount=max_amount,
            archived=archived,
            anomalies_only=anomalies_only,
            date_from=date_from,
            date_to=date_to,
        )

        rows = conn.execute(
            f"""
            SELECT
                e.expenditure_record_id,
                e.committee_id_sbe,
                e.filed_doc_id,
                e.expended_date,
                e.d2_part_code,
                e.payee_last_or_business_name,
                e.payee_first_name,
                e.address_line_1,
                e.address_line_2,
                e.city,
                e.state,
                e.postal_code,
                e.amount,
                e.aggregate_amount,
                e.purpose,
                e.candidate_name,
                e.office,
                e.is_supporting,
                e.is_opposing,
                e.is_archived,
                e.country,
                e.redaction_requested,
                e.is_amount_anomalous,
                e.anomaly_reason
            FROM {cls.EXPENDITURES_TABLE} e
            WHERE e.committee_id_sbe = ?
              AND EXISTS (
                  SELECT 1
                  FROM {cls.LINKS_TABLE} l
                  WHERE l.candidate_id = ?
                    AND l.committee_id_sbe = e.committee_id_sbe
              )
              {filter_sql}
            ORDER BY {order_by} {direction}, e.expenditure_record_id DESC
            LIMIT ? OFFSET ?
            """,
            [committee_id, candidate_id, *filter_params, limit, offset],
        ).fetchall()

        results: List[CandidateCommitteeItemizedExpenditure] = []
        for row in rows:
            payee_name = " ".join(
                part for part in [row["payee_first_name"], row["payee_last_or_business_name"]] if part
            ).strip() or None
            payee_address = ", ".join(
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

            results.append(
                cls(
                    expenditure_record_id=row["expenditure_record_id"],
                    committee_id_sbe=row["committee_id_sbe"],
                    filed_doc_id=row["filed_doc_id"],
                    expended_date=row["expended_date"],
                    d2_part_code=row["d2_part_code"],
                    payee_name=payee_name,
                    payee_address=payee_address,
                    amount=row["amount"] or 0.0,
                    aggregate_amount=row["aggregate_amount"] or 0.0,
                    purpose=row["purpose"],
                    candidate_name=row["candidate_name"],
                    office=row["office"],
                    is_supporting=row["is_supporting"],
                    is_opposing=row["is_opposing"],
                    is_archived=row["is_archived"],
                    country=row["country"],
                    redaction_requested=row["redaction_requested"],
                    is_amount_anomalous=row["is_amount_anomalous"],
                    anomaly_reason=row["anomaly_reason"],
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
            "SELECT 1 FROM sqlite_master WHERE type IN ('table', 'view') AND name = ?",
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
        period_start: Optional[str] = None,
        period_end: Optional[str] = None,
        has_filed_docs_table: bool = False,
    ) -> tuple[str, List[object]]:
        clauses: List[str] = []
        params: List[object] = []

        search_term = (search or "").strip()
        if search_term:
            clauses.append(
                """
                (
                    COALESCE(committee_name, '') LIKE ?
                    OR COALESCE(CAST(committee_id_sbe AS TEXT), '') LIKE ?
                    OR COALESCE(CAST(filed_doc_id AS TEXT), '') LIKE ?
                )
                """
            )
            like_term = f"%{search_term}%"
            params.extend([like_term, like_term, like_term])

        if min_abs_diff is not None:
            clauses.append(
                "ABS(COALESCE(CAST(NULLIF(TRIM(CAST(receipts_minus_d2_total AS TEXT)), '') AS REAL), 0)) >= ?"
            )
            params.append(float(min_abs_diff))

        if min_receipt_rows is not None:
            clauses.append(
                "COALESCE(CAST(NULLIF(TRIM(CAST(receipt_row_count AS TEXT)), '') AS INTEGER), 0) >= ?"
            )
            params.append(int(min_receipt_rows))

        if has_filed_docs_table:
            period_start_term = (period_start or "").strip()
            if period_start_term:
                clauses.append(
                    """
                    EXISTS (
                        SELECT 1
                        FROM isbe_filed_docs fd
                        WHERE fd.id = filed_doc_id
                          AND DATE(fd.reporting_period_end) >= DATE(?)
                    )
                    """
                )
                params.append(period_start_term)

            period_end_term = (period_end or "").strip()
            if period_end_term:
                clauses.append(
                    """
                    EXISTS (
                        SELECT 1
                        FROM isbe_filed_docs fd
                        WHERE fd.id = filed_doc_id
                          AND DATE(fd.reporting_period_end) <= DATE(?)
                    )
                    """
                )
                params.append(period_end_term)

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
        period_start: Optional[str] = None,
        period_end: Optional[str] = None,
    ) -> int:
        if not cls._table_exists(conn):
            return 0

        has_filed_docs_table = conn.execute(
            "SELECT 1 FROM sqlite_master WHERE type IN ('table', 'view') AND name = 'isbe_filed_docs'"
        ).fetchone() is not None
        where_sql, params = cls._build_filter_sql(
            search=search,
            min_abs_diff=min_abs_diff,
            min_receipt_rows=min_receipt_rows,
            period_start=period_start,
            period_end=period_end,
            has_filed_docs_table=has_filed_docs_table,
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
        period_start: Optional[str] = None,
        period_end: Optional[str] = None,
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
            "abs_diff": "ABS(COALESCE(CAST(NULLIF(TRIM(CAST(receipts_minus_d2_total AS TEXT)), '') AS REAL), 0))",
        }
        order_by = sort_map.get(
            sort_by,
            "ABS(COALESCE(CAST(NULLIF(TRIM(CAST(receipts_minus_d2_total AS TEXT)), '') AS REAL), 0))",
        )
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
        has_filed_docs_table = conn.execute(
            "SELECT 1 FROM sqlite_master WHERE type IN ('table', 'view') AND name = 'isbe_filed_docs'"
        ).fetchone() is not None
        where_sql, params = cls._build_filter_sql(
            search=search,
            min_abs_diff=min_abs_diff,
            min_receipt_rows=min_receipt_rows,
            period_start=period_start,
            period_end=period_end,
            has_filed_docs_table=has_filed_docs_table,
        )
        query += where_sql
        query += f" ORDER BY {order_by} {direction}, committee_id_sbe ASC, filed_doc_id ASC LIMIT ? OFFSET ?"
        params.extend([limit, offset])

        rows = conn.execute(query, params).fetchall()

        def _to_float(value: object, default: float = 0.0) -> float:
            if value is None:
                return default
            if isinstance(value, str):
                stripped = value.strip()
                if not stripped:
                    return default
                value = stripped
            try:
                return float(value)
            except (TypeError, ValueError):
                return default

        def _to_int(value: object, default: int = 0) -> int:
            if value is None:
                return default
            if isinstance(value, str):
                stripped = value.strip()
                if not stripped:
                    return default
                value = stripped
            try:
                return int(float(value))
            except (TypeError, ValueError):
                return default

        return [
            cls(
                d2_totals_record_id=row["d2_totals_record_id"],
                committee_id_sbe=row["committee_id_sbe"],
                committee_name=row["committee_name"],
                filed_doc_id=row["filed_doc_id"],
                d2_total_receipts=_to_float(row["d2_total_receipts"]),
                d2_total_expenditures=_to_float(row["d2_total_expenditures"]),
                ending_funds_available=_to_float(row["ending_funds_available"]),
                is_archived=row["is_archived"],
                receipt_row_count=_to_int(row["receipt_row_count"]),
                receipts_amount_sum=_to_float(row["receipts_amount_sum"]),
                first_receipt_date=row["first_receipt_date"],
                last_receipt_date=row["last_receipt_date"],
                receipts_minus_d2_total=_to_float(row["receipts_minus_d2_total"]),
            )
            for row in rows
        ]


@dataclass
class D2ExpendituresRecon:
    """D2 filing totals reconciled against itemized expenditure sums."""

    d2_totals_record_id: Optional[int] = None
    committee_id_sbe: Optional[int] = None
    committee_name: Optional[str] = None
    filed_doc_id: Optional[int] = None
    d2_transfers_out_itemized: float = 0.0
    d2_loans_made_itemized: float = 0.0
    d2_expenditures_itemized: float = 0.0
    d2_independent_expenditures_itemized: float = 0.0
    d2_itemized_expenditures_total: float = 0.0
    d2_total_expenditures: float = 0.0
    ending_funds_available: float = 0.0
    is_archived: Optional[int] = None
    expenditure_row_count: int = 0
    expenditures_amount_sum: float = 0.0
    sum_part_6_transfers_out: float = 0.0
    sum_part_7_loans_made: float = 0.0
    sum_part_8_expenditures: float = 0.0
    sum_part_9_independent_expenditures: float = 0.0
    anomaly_row_count: int = 0
    first_expenditure_date: Optional[str] = None
    last_expenditure_date: Optional[str] = None
    expenditures_minus_d2_itemized_total: float = 0.0
    part_6_minus_d2_transfers_out_itemized: float = 0.0
    part_7_minus_d2_loans_made_itemized: float = 0.0
    part_8_minus_d2_expenditures_itemized: float = 0.0
    part_9_minus_d2_independent_expenditures_itemized: float = 0.0

    TABLE_NAME = "bulk_d2_expenditures_recon"

    @classmethod
    def _table_exists(cls, conn: sqlite3.Connection) -> bool:
        row = conn.execute(
            "SELECT 1 FROM sqlite_master WHERE type IN ('table', 'view') AND name = ?",
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
        min_expenditure_rows: Optional[int] = None,
        anomalies_only: str = "no",
        period_start: Optional[str] = None,
        period_end: Optional[str] = None,
        has_filed_docs_table: bool = False,
    ) -> tuple[str, List[object]]:
        clauses: List[str] = []
        params: List[object] = []

        search_term = (search or "").strip()
        if search_term:
            clauses.append(
                """
                (
                    COALESCE(committee_name, '') LIKE ?
                    OR COALESCE(CAST(committee_id_sbe AS TEXT), '') LIKE ?
                    OR COALESCE(CAST(filed_doc_id AS TEXT), '') LIKE ?
                )
                """
            )
            like_term = f"%{search_term}%"
            params.extend([like_term, like_term, like_term])

        if min_abs_diff is not None:
            clauses.append(
                "ABS(COALESCE(CAST(NULLIF(TRIM(CAST(expenditures_minus_d2_itemized_total AS TEXT)), '') AS REAL), 0)) >= ?"
            )
            params.append(float(min_abs_diff))

        if min_expenditure_rows is not None:
            clauses.append(
                "COALESCE(CAST(NULLIF(TRIM(CAST(expenditure_row_count AS TEXT)), '') AS INTEGER), 0) >= ?"
            )
            params.append(int(min_expenditure_rows))

        anomalies_term = (anomalies_only or "no").strip().lower()
        if anomalies_term in {"yes", "true", "1"}:
            clauses.append(
                "COALESCE(CAST(NULLIF(TRIM(CAST(anomaly_row_count AS TEXT)), '') AS INTEGER), 0) > 0"
            )

        if has_filed_docs_table:
            period_start_term = (period_start or "").strip()
            if period_start_term:
                clauses.append(
                    """
                    EXISTS (
                        SELECT 1
                        FROM isbe_filed_docs fd
                        WHERE fd.id = filed_doc_id
                          AND DATE(fd.reporting_period_end) >= DATE(?)
                    )
                    """
                )
                params.append(period_start_term)

            period_end_term = (period_end or "").strip()
            if period_end_term:
                clauses.append(
                    """
                    EXISTS (
                        SELECT 1
                        FROM isbe_filed_docs fd
                        WHERE fd.id = filed_doc_id
                          AND DATE(fd.reporting_period_end) <= DATE(?)
                    )
                    """
                )
                params.append(period_end_term)

        if not clauses:
            return "", params
        return f" WHERE {' AND '.join(clauses)}", params

    @classmethod
    def count(
        cls,
        conn: sqlite3.Connection,
        search: Optional[str] = None,
        min_abs_diff: Optional[float] = None,
        min_expenditure_rows: Optional[int] = None,
        anomalies_only: str = "no",
        period_start: Optional[str] = None,
        period_end: Optional[str] = None,
    ) -> int:
        if not cls._table_exists(conn):
            return 0

        has_filed_docs_table = conn.execute(
            "SELECT 1 FROM sqlite_master WHERE type IN ('table', 'view') AND name = 'isbe_filed_docs'"
        ).fetchone() is not None
        where_sql, params = cls._build_filter_sql(
            search=search,
            min_abs_diff=min_abs_diff,
            min_expenditure_rows=min_expenditure_rows,
            anomalies_only=anomalies_only,
            period_start=period_start,
            period_end=period_end,
            has_filed_docs_table=has_filed_docs_table,
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
        min_expenditure_rows: Optional[int] = None,
        anomalies_only: str = "no",
        period_start: Optional[str] = None,
        period_end: Optional[str] = None,
    ) -> List["D2ExpendituresRecon"]:
        if not cls._table_exists(conn):
            return []

        _safe_real = "COALESCE(CAST(NULLIF(TRIM(CAST({col} AS TEXT)), '') AS REAL), 0)"
        _safe_int = "COALESCE(CAST(NULLIF(TRIM(CAST({col} AS TEXT)), '') AS INTEGER), 0)"
        _abs_diff_expr = f"ABS({_safe_real.format(col='expenditures_minus_d2_itemized_total')})"
        sort_map = {
            "d2_totals_record_id": "d2_totals_record_id",
            "committee_id_sbe": "committee_id_sbe",
            "committee_name": "committee_name",
            "filed_doc_id": "filed_doc_id",
            "d2_itemized_expenditures_total": _safe_real.format(col="d2_itemized_expenditures_total"),
            "d2_total_expenditures": _safe_real.format(col="d2_total_expenditures"),
            "expenditure_row_count": _safe_int.format(col="expenditure_row_count"),
            "expenditures_amount_sum": _safe_real.format(col="expenditures_amount_sum"),
            "sum_part_6_transfers_out": _safe_real.format(col="sum_part_6_transfers_out"),
            "sum_part_7_loans_made": _safe_real.format(col="sum_part_7_loans_made"),
            "sum_part_8_expenditures": _safe_real.format(col="sum_part_8_expenditures"),
            "sum_part_9_independent_expenditures": _safe_real.format(col="sum_part_9_independent_expenditures"),
            "anomaly_row_count": _safe_int.format(col="anomaly_row_count"),
            "first_expenditure_date": "first_expenditure_date",
            "last_expenditure_date": "last_expenditure_date",
            "expenditures_minus_d2_itemized_total": _safe_real.format(col="expenditures_minus_d2_itemized_total"),
            "abs_diff": _abs_diff_expr,
        }
        order_by = sort_map.get(sort_by, _abs_diff_expr)
        direction = "ASC" if str(sort_dir).lower() == "asc" else "DESC"

        query = f"""
            SELECT
                d2_totals_record_id,
                committee_id_sbe,
                committee_name,
                filed_doc_id,
                d2_transfers_out_itemized,
                d2_loans_made_itemized,
                d2_expenditures_itemized,
                d2_independent_expenditures_itemized,
                d2_itemized_expenditures_total,
                d2_total_expenditures,
                ending_funds_available,
                is_archived,
                expenditure_row_count,
                expenditures_amount_sum,
                sum_part_6_transfers_out,
                sum_part_7_loans_made,
                sum_part_8_expenditures,
                sum_part_9_independent_expenditures,
                anomaly_row_count,
                first_expenditure_date,
                last_expenditure_date,
                expenditures_minus_d2_itemized_total,
                part_6_minus_d2_transfers_out_itemized,
                part_7_minus_d2_loans_made_itemized,
                part_8_minus_d2_expenditures_itemized,
                part_9_minus_d2_independent_expenditures_itemized
            FROM {cls.TABLE_NAME}
        """
        has_filed_docs_table = conn.execute(
            "SELECT 1 FROM sqlite_master WHERE type IN ('table', 'view') AND name = 'isbe_filed_docs'"
        ).fetchone() is not None
        where_sql, params = cls._build_filter_sql(
            search=search,
            min_abs_diff=min_abs_diff,
            min_expenditure_rows=min_expenditure_rows,
            anomalies_only=anomalies_only,
            period_start=period_start,
            period_end=period_end,
            has_filed_docs_table=has_filed_docs_table,
        )
        query += where_sql
        query += f" ORDER BY {order_by} {direction}, committee_id_sbe ASC, filed_doc_id ASC LIMIT ? OFFSET ?"
        params.extend([limit, offset])

        def _sf(v, default=0.0):
            """Safe float from potentially TEXT column."""
            if v is None:
                return default
            try:
                return float(v)
            except (ValueError, TypeError):
                return default

        def _si(v, default=0):
            """Safe int from potentially TEXT column."""
            if v is None:
                return default
            try:
                return int(float(v))
            except (ValueError, TypeError):
                return default

        rows = conn.execute(query, params).fetchall()
        return [
            cls(
                d2_totals_record_id=row["d2_totals_record_id"],
                committee_id_sbe=row["committee_id_sbe"],
                committee_name=row["committee_name"],
                filed_doc_id=row["filed_doc_id"],
                d2_transfers_out_itemized=_sf(row["d2_transfers_out_itemized"]),
                d2_loans_made_itemized=_sf(row["d2_loans_made_itemized"]),
                d2_expenditures_itemized=_sf(row["d2_expenditures_itemized"]),
                d2_independent_expenditures_itemized=_sf(row["d2_independent_expenditures_itemized"]),
                d2_itemized_expenditures_total=_sf(row["d2_itemized_expenditures_total"]),
                d2_total_expenditures=_sf(row["d2_total_expenditures"]),
                ending_funds_available=_sf(row["ending_funds_available"]),
                is_archived=row["is_archived"],
                expenditure_row_count=_si(row["expenditure_row_count"]),
                expenditures_amount_sum=_sf(row["expenditures_amount_sum"]),
                sum_part_6_transfers_out=_sf(row["sum_part_6_transfers_out"]),
                sum_part_7_loans_made=_sf(row["sum_part_7_loans_made"]),
                sum_part_8_expenditures=_sf(row["sum_part_8_expenditures"]),
                sum_part_9_independent_expenditures=_sf(row["sum_part_9_independent_expenditures"]),
                anomaly_row_count=_si(row["anomaly_row_count"]),
                first_expenditure_date=row["first_expenditure_date"],
                last_expenditure_date=row["last_expenditure_date"],
                expenditures_minus_d2_itemized_total=_sf(row["expenditures_minus_d2_itemized_total"]),
                part_6_minus_d2_transfers_out_itemized=_sf(row["part_6_minus_d2_transfers_out_itemized"]),
                part_7_minus_d2_loans_made_itemized=_sf(row["part_7_minus_d2_loans_made_itemized"]),
                part_8_minus_d2_expenditures_itemized=_sf(row["part_8_minus_d2_expenditures_itemized"]),
                part_9_minus_d2_independent_expenditures_itemized=_sf(row["part_9_minus_d2_independent_expenditures_itemized"]),
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
                (password_hash, bool(is_active), existing.id),
            )
            conn.commit()
            updated = cls.get_by_id(conn, existing.id)
            return updated

        cursor = conn.execute(
            """
            INSERT INTO app_users (username, password_hash, is_active)
            VALUES (?, ?, ?)
            """,
            (username, password_hash, bool(is_active)),
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
