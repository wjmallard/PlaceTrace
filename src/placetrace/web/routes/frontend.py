"""
PlaceTrace Frontend Blueprint
Serves the map explorer UI
"""

from flask import Blueprint, render_template

from placetrace.config import MAP

# Create blueprint with template and static folders
bp = Blueprint('frontend', __name__,
               template_folder='../templates',
               static_folder='../static',
               static_url_path='/static')


@bp.route('/')
def index():
    """Serve the main map explorer page"""
    return render_template('index.html', map_config=MAP)
