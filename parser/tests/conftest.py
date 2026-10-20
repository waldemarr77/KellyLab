import sys
from pathlib import Path

# Гарантуємо наявність директорії parser у sys.path
parser_dir = Path(__file__).resolve().parent.parent
if str(parser_dir) not in sys.path:
    sys.path.insert(0, str(parser_dir))
