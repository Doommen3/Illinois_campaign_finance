"""Lobbying data routes."""
from flask import Blueprint, render_template, request, current_app, abort

lobbying_bp = Blueprint('lobbying', __name__)


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
    keys = row.keys() if hasattr(row, "keys") else []
    if not keys:
        return default
    value = row[keys[0]]
    return default if value is None else value


@lobbying_bp.route('/')
def list_entities():
    """List lobbying entities with client counts and match counts."""
    conn = current_app.get_database()

    if not _table_exists(conn, "lobbying_entities"):
        return render_template('lobbying/list.html', entities=[], total=0,
                               page=1, total_pages=1, query='')

    page = request.args.get('page', 1, type=int)
    per_page = 50
    offset = (page - 1) * per_page
    query = request.args.get('q', '').strip()

    where_clause = ""
    params = []
    if query:
        where_clause = "WHERE e.entity_name LIKE ?"
        params.append(f"%{query}%")

    total = _scalar(
        conn,
        f"SELECT COUNT(*) FROM lobbying_entities e {where_clause}",
        params,
        default=0,
    )
    total_pages = max(1, (total + per_page - 1) // per_page)

    entities = conn.execute(
        f"""
        SELECT
            e.entity_id,
            e.entity_name,
            e.reg_year,
            COUNT(DISTINCT ec.client_id) AS client_count
        FROM lobbying_entities e
        LEFT JOIN lobbying_entity_clients ec ON ec.entity_id = e.entity_id
        {where_clause}
        GROUP BY e.entity_id, e.entity_name, e.reg_year
        ORDER BY client_count DESC, e.entity_name ASC
        LIMIT ? OFFSET ?
        """,
        params + [per_page, offset],
    ).fetchall()

    return render_template('lobbying/list.html',
                           entities=entities, total=total,
                           page=page, total_pages=total_pages, query=query)


@lobbying_bp.route('/<int:entity_id>')
def entity_detail(entity_id):
    """Entity detail: clients, matched donors, matched expenditure payees."""
    conn = current_app.get_database()

    if not _table_exists(conn, "lobbying_entities"):
        abort(404)

    entity = conn.execute(
        "SELECT entity_id, entity_name, reg_year FROM lobbying_entities WHERE entity_id = ?",
        (entity_id,),
    ).fetchone()
    if not entity:
        abort(404)

    clients = conn.execute(
        """
        SELECT c.client_id, c.client_name, ec.reg_year
        FROM lobbying_entity_clients ec
        JOIN lobbying_clients c ON c.client_id = ec.client_id
        WHERE ec.entity_id = ?
        ORDER BY c.client_name
        """,
        (entity_id,),
    ).fetchall()

    # Expenditure matches for this entity
    expenditure_matches = []
    if _table_exists(conn, "lobbying_expenditure_matches"):
        expenditure_matches = conn.execute(
            """
            SELECT source_type, source_name, payee_name, committee_id_sbe, score
            FROM lobbying_expenditure_matches
            WHERE source_type = 'entity' AND source_id = ?
            ORDER BY score DESC
            LIMIT 50
            """,
            (entity_id,),
        ).fetchall()

    return render_template('lobbying/entity_detail.html',
                           entity=entity, clients=clients,
                           expenditure_matches=expenditure_matches)


@lobbying_bp.route('/client/<int:client_id>')
def client_detail(client_id):
    """Client detail: entities, campaign donations (if matched), 527 connections."""
    conn = current_app.get_database()

    if not _table_exists(conn, "lobbying_clients"):
        abort(404)

    client = conn.execute(
        "SELECT client_id, client_name FROM lobbying_clients WHERE client_id = ?",
        (client_id,),
    ).fetchone()
    if not client:
        abort(404)

    # Entities that represent this client
    entities = conn.execute(
        """
        SELECT e.entity_id, e.entity_name, ec.reg_year
        FROM lobbying_entity_clients ec
        JOIN lobbying_entities e ON e.entity_id = ec.entity_id
        WHERE ec.client_id = ?
        ORDER BY e.entity_name
        """,
        (client_id,),
    ).fetchall()

    # Donor matches
    donor_matches = []
    if _table_exists(conn, "lobbying_donor_matches"):
        donor_matches = conn.execute(
            """
            SELECT client_name, donor_key, donor_name, score, method
            FROM lobbying_donor_matches
            WHERE client_id = ?
            ORDER BY score DESC
            LIMIT 50
            """,
            (client_id,),
        ).fetchall()

    # Expenditure matches
    expenditure_matches = []
    if _table_exists(conn, "lobbying_expenditure_matches"):
        expenditure_matches = conn.execute(
            """
            SELECT source_type, source_name, payee_name, committee_id_sbe, score
            FROM lobbying_expenditure_matches
            WHERE source_type = 'client' AND source_id = ?
            ORDER BY score DESC
            LIMIT 50
            """,
            (client_id,),
        ).fetchall()

    # 527 matches
    org_527_matches = []
    if _table_exists(conn, "lobbying_527_matches"):
        org_527_matches = conn.execute(
            """
            SELECT client_name, ein, org_name, score
            FROM lobbying_527_matches
            WHERE client_id = ?
            ORDER BY score DESC
            LIMIT 50
            """,
            (client_id,),
        ).fetchall()

    return render_template('lobbying/client_detail.html',
                           client=client, entities=entities,
                           donor_matches=donor_matches,
                           expenditure_matches=expenditure_matches,
                           org_527_matches=org_527_matches)
