"""Export shared workout copy for the static WebApp; --check detects drift."""
import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from src.utils.workout_locales import COPY, LANGS  # noqa: E402

parser = argparse.ArgumentParser(description=__doc__)
parser.add_argument('--check', action='store_true')
args = parser.parse_args()
content = '// Generated from src/utils/workout_locales.py; keep translations in sync.\nconst WORKOUT_LOCALES = ' + json.dumps(
    {lang: {key: values[i] for key, values in COPY.items()} for i, lang in enumerate(LANGS)},
    ensure_ascii=False, indent=2) + ';\n'
path = ROOT / 'src/webapp/static/workout-locales.js'
if args.check:
    if not path.exists() or path.read_text() != content:
        raise SystemExit('Workout translations are out of date. Run scripts/export_workout_locales.py')
else:
    path.write_text(content)
