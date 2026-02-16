"""OpenBook vendor contracts and contributions routes."""
from flask import Blueprint, render_template, request, current_app, abort

openbook_bp = Blueprint("openbook", __name__)


def _table_exists(conn, table_name: str) -> bool:
    row = conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name = ?",
        (table_name,),
    ).fetchone()
    return row is not None


def _scalar(conn, sql, params=(), default=0):
    try:
        row = conn.execute(sql, params).fetchone()
    except Exception:
        return default
    if not row:
        return default
    if hasattr(row, "keys"):
        first_key = next(iter(row.keys()), None)
        if first_key is None:
            return default
        value = row[first_key]
    else:
        value = row[0] if len(row) else default
    return default if value is None else value


@openbook_bp.route("/")
def list_vendors():
    conn = current_app.get_database()
    if not _table_exists(conn, "openbook_vendor_match"):
        return render_template(
            "openbook/list.html",
            vendors=[],
            total=0,
            page=1,
            total_pages=1,
            query="",
        )

    query = request.args.get("q", "").strip()
    page = request.args.get("page", 1, type=int)
    per_page = 50
    offset = max(0, page - 1) * per_page

    where_sql = "WHERE m.match_method != 'no_match' AND m.openbook_vendor_key != '' AND m.confidence >= 0.8"
    params = []
    if query:
        where_sql += " AND (m.openbook_vendor_key LIKE ? OR m.openbook_vendor_label LIKE ?)"
        params.extend([f"%{query}%", f"%{query}%"])

    total = int(
        _scalar(
            conn,
            f"""
            SELECT COUNT(DISTINCT m.openbook_vendor_key)
            FROM openbook_vendor_match m
            {where_sql}
            """,
            params,
            default=0,
        )
    )
    total_pages = max(1, (total + per_page - 1) // per_page)

    vendors = conn.execute(
        f"""
        WITH vendor_base AS (
            SELECT
                m.openbook_vendor_key,
                MAX(m.openbook_vendor_label) AS openbook_vendor_label,
                COUNT(DISTINCT m.seed_id) AS seed_count,
                MAX(m.confidence) AS best_confidence
            FROM openbook_vendor_match m
            {where_sql}
            GROUP BY m.openbook_vendor_key
        ),
        contracts AS (
            SELECT
                openbook_vendor_key,
                COUNT(*) AS contract_count,
                COALESCE(SUM(award_amount), 0) AS total_award_amount
            FROM openbook_contracts_raw
            GROUP BY openbook_vendor_key
        ),
        contributions AS (
            SELECT
                openbook_vendor_key,
                COUNT(*) AS contribution_count
            FROM openbook_contributions_raw
            GROUP BY openbook_vendor_key
        )
        SELECT
            b.openbook_vendor_key,
            b.openbook_vendor_label,
            b.seed_count,
            b.best_confidence,
            COALESCE(c.contract_count, 0) AS contract_count,
            COALESCE(c.total_award_amount, 0) AS total_award_amount,
            COALESCE(k.contribution_count, 0) AS contribution_count
        FROM vendor_base b
        LEFT JOIN contracts c ON c.openbook_vendor_key = b.openbook_vendor_key
        LEFT JOIN contributions k ON k.openbook_vendor_key = b.openbook_vendor_key
        ORDER BY c.total_award_amount DESC, c.contract_count DESC, b.openbook_vendor_label ASC
        LIMIT ? OFFSET ?
        """,
        params + [per_page, offset],
    ).fetchall()

    summary = {
        "vendors_matched": total,
        "contracts_total": int(
            _scalar(conn, "SELECT COUNT(*) FROM openbook_contracts_raw", default=0)
            if _table_exists(conn, "openbook_contracts_raw")
            else 0
        ),
        "award_total": float(
            _scalar(
                conn,
                "SELECT COALESCE(SUM(award_amount), 0) FROM openbook_contracts_raw",
                default=0,
            )
            if _table_exists(conn, "openbook_contracts_raw")
            else 0
        ),
        "contributions_total": int(
            _scalar(conn, "SELECT COUNT(*) FROM openbook_contributions_raw", default=0)
            if _table_exists(conn, "openbook_contributions_raw")
            else 0
        ),
    }

    return render_template(
        "openbook/list.html",
        vendors=vendors,
        total=total,
        page=page,
        total_pages=total_pages,
        query=query,
        summary=summary,
    )


@openbook_bp.route("/<path:vendor_key>")
def vendor_detail(vendor_key: str):
    conn = current_app.get_database()
    if not _table_exists(conn, "openbook_vendor_match"):
        abort(404)

    vendor = conn.execute(
        """
        SELECT
            m.openbook_vendor_key,
            MAX(m.openbook_vendor_label) AS openbook_vendor_label,
            MAX(m.confidence) AS best_confidence,
            COUNT(DISTINCT m.seed_id) AS seed_count
        FROM openbook_vendor_match m
        WHERE m.match_method != 'no_match' AND m.openbook_vendor_key = ? AND m.confidence >= 0.8
        GROUP BY m.openbook_vendor_key
        """,
        (vendor_key,),
    ).fetchone()
    if not vendor:
        abort(404)

    contracts = conn.execute(
        """
        SELECT
            fiscal_year,
            agency_name,
            contract_number,
            award_amount,
            detail_url
        FROM openbook_contracts_raw
        WHERE openbook_vendor_key = ?
        ORDER BY fiscal_year DESC, award_amount DESC
        LIMIT 250
        """,
        (vendor_key,),
    ).fetchall() if _table_exists(conn, "openbook_contracts_raw") else []

    warrants = conn.execute(
        """
        SELECT
            contract_number,
            fiscal_year,
            issue_date,
            payment_amount
        FROM openbook_contract_warrants
        WHERE openbook_vendor_key = ?
        ORDER BY issue_date DESC
        LIMIT 250
        """,
        (vendor_key,),
    ).fetchall() if _table_exists(conn, "openbook_contract_warrants") else []

    contributions = conn.execute(
        """
        SELECT
            contribution_date,
            contributor_name,
            recipient_name,
            amount
        FROM openbook_contributions_raw
        WHERE openbook_vendor_key = ?
        ORDER BY contribution_date DESC, amount DESC
        LIMIT 250
        """,
        (vendor_key,),
    ).fetchall() if _table_exists(conn, "openbook_contributions_raw") else []

    seed_links = conn.execute(
        """
        SELECT
            s.seed_text,
            s.seed_source,
            MAX(m.confidence) AS best_confidence,
            MAX(COALESCE(m.search_term_used, '')) AS search_term_used,
            MAX(le.entity_id) AS lobbying_entity_id
        FROM openbook_vendor_match m
        JOIN openbook_vendor_seed s ON s.seed_id = m.seed_id
        LEFT JOIN lobbying_entities le ON UPPER(TRIM(le.entity_name)) = UPPER(TRIM(s.seed_text))
        WHERE m.openbook_vendor_key = ? AND m.match_method != 'no_match' AND m.confidence >= 0.8
        GROUP BY s.seed_text, s.seed_source
        ORDER BY best_confidence DESC, s.seed_text ASC
        LIMIT 250
        """,
        (vendor_key,),
    ).fetchall() if _table_exists(conn, "openbook_vendor_seed") else []

    totals = {
        "contract_count": len(contracts),
        "award_total": float(sum(float(row["award_amount"] or 0) for row in contracts)),
        "warrant_count": len(warrants),
        "warrant_total": float(sum(float(row["payment_amount"] or 0) for row in warrants)),
        "contribution_count": len(contributions),
        "contribution_total": float(sum(float(row["amount"] or 0) for row in contributions)),
    }

    return render_template(
        "openbook/detail.html",
        vendor=vendor,
        contracts=contracts,
        warrants=warrants,
        contributions=contributions,
        seed_links=seed_links,
        totals=totals,
    )
