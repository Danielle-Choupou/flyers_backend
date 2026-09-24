from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent

DATA_DIR = BASE_DIR / "data"
TEMPLATES_DIR = BASE_DIR / "templates"
FONTS_DIR = BASE_DIR / "fonts"
OUTPUT_DIR = BASE_DIR / "output"

COMPANY_PROFILES_FILE = DATA_DIR / "company_profiles.json"
TEMPLATES_CONFIG_FILE = DATA_DIR / "templates_config.json"

for folder in (DATA_DIR, TEMPLATES_DIR, FONTS_DIR, OUTPUT_DIR):
    folder.mkdir(parents=True, exist_ok=True)