"""
location-history Flask API
Main application entry point
"""

from flask import Flask

from location_history.web.database import init_db
from location_history.web.routes import visits, locations, trips, movements, stats, frontend


def create_app():
    """Create and configure Flask application"""
    app = Flask(__name__)

    init_db(app)

    # Register API blueprints
    app.register_blueprint(visits.bp, url_prefix='/api')
    app.register_blueprint(locations.bp, url_prefix='/api')
    app.register_blueprint(trips.bp, url_prefix='/api')
    app.register_blueprint(movements.bp, url_prefix='/api')
    app.register_blueprint(stats.bp, url_prefix='/api')

    # Register frontend blueprint
    app.register_blueprint(frontend.bp)

    @app.route('/health')
    def health():
        """Health check endpoint"""
        return {'status': 'ok'}

    return app
