"""
PlaceTrace Flask API
Main application entry point
"""

from flask import Flask
from flask_cors import CORS
from placetrace.config import config
from placetrace.web.database import init_db
from placetrace.web.routes import visits, photos, locations, trips, movements, stats, frontend


def create_app():
    """Create and configure Flask application"""
    app = Flask(__name__)

    # Load configuration
    app.config['CONFIG'] = config
    app.config['SQLALCHEMY_DATABASE_URI'] = f"postgresql+psycopg:///{config['databases']['main']}"
    app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False
    
    # Enable CORS for localhost during development
    CORS(app, origins=['http://localhost:*', 'http://127.0.0.1:*'])
    
    # Initialize database
    init_db(app)
    
    # Register API blueprints
    app.register_blueprint(visits.bp, url_prefix='/api')
    app.register_blueprint(photos.bp, url_prefix='/api')
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


if __name__ == '__main__':
    app = create_app()
    app.run(debug=True, host='0.0.0.0', port=5001)
