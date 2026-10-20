import sys
from pathlib import Path

# Гарантуємо наявність директорії exporter у sys.path під час тестів
exporter_dir = Path(__file__).resolve().parent.parent
if str(exporter_dir) not in sys.path:
    sys.path.insert(0, str(exporter_dir))
