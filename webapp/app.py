"""Flask application factory for Illinois Campaign Finance tracker."""
from flask import Flask, g

from database.connection import get_db, close_db
from webapp.auth import get_current_user


def create_app(config=None):
    """Create and configure the Flask application.

    Args:
        config: Optional configuration dictionary

    Returns:
        Configured Flask application
    """
    app = Flask(__name__)

    # Default configuration
    app.config['SECRET_KEY'] = 'dev-secret-key-change-in-production'
    app.config['DATABASE_PATH'] = None  # Use default from connection.py

    # Apply custom config if provided
    if config:
        app.config.update(config)

    # Register teardown - close connection at end of each request
    @app.teardown_appcontext
    def teardown_db(exception):
        db = g.pop('_database', None)
        if db is not None:
            close_db(db)

    # Add database helper - creates per-request connection using Flask's g object
    def get_database():
        if '_database' not in g:
            g._database = get_db(app.config['DATABASE_PATH'])
        return g._database

    app.get_database = get_database

    # Register blueprints
    from webapp.routes.main import main_bp
    from webapp.routes.auth import auth_bp
    from webapp.routes.committees import committees_bp
    from webapp.routes.donors import donors_bp
    from webapp.routes.reports import reports_bp
    from webapp.routes.candidate_finance import candidate_finance_bp
    from webapp.routes.federal_finance import federal_finance_bp
    from webapp.routes.d2_receipts_recon import d2_receipts_recon_bp
    from webapp.routes.analytics import analytics_bp
    from webapp.routes.manual_entry import manual_entry_bp
    from webapp.routes.admin import admin_bp
    from webapp.routes.api import api_bp

    app.register_blueprint(main_bp)
    app.register_blueprint(auth_bp, url_prefix='/auth')
    app.register_blueprint(committees_bp, url_prefix='/committees')
    app.register_blueprint(donors_bp, url_prefix='/donors')
    app.register_blueprint(reports_bp, url_prefix='/reports')
    app.register_blueprint(candidate_finance_bp, url_prefix='/candidate-finance')
    app.register_blueprint(federal_finance_bp, url_prefix='/federal-finance')
    app.register_blueprint(d2_receipts_recon_bp, url_prefix='/d2-reconciliation')
    app.register_blueprint(analytics_bp, url_prefix='/analytics')
    app.register_blueprint(manual_entry_bp, url_prefix='/manual-entry')
    app.register_blueprint(admin_bp, url_prefix='/admin')
    app.register_blueprint(api_bp, url_prefix='/api')

    # Context processors
    @app.context_processor
    def inject_helpers():
        def format_currency(value):
            if value is None:
                return '$0.00'
            return '${:,.2f}'.format(value)

        return dict(
            format_currency=format_currency,
            current_manual_user=get_current_user(),
        )

    return app
