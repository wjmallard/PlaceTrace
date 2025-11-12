"""
Configuration management
Load settings from config.yaml
"""

import yaml
from pathlib import Path


def load_config(config_path=None):
    """
    Load configuration from YAML file
    
    Args:
        config_path: Path to config.yaml (optional, auto-detects if None)
        
    Returns:
        dict: Configuration dictionary
    """
    if config_path is None:
        # Auto-detect: project root is two levels up from this file
        # /app/api/config.py -> ../../config.yaml
        project_root = Path(__file__).parent.parent.parent
        config_path = project_root / 'config.yaml'
    else:
        config_path = Path(config_path)
    
    if not config_path.exists():
        raise FileNotFoundError(f"Configuration file not found: {config_path}")
    
    with open(config_path, 'r') as f:
        config = yaml.safe_load(f)
    
    return config
