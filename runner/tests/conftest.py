import sys
from pathlib import Path

# Гарантуємо наявність директорії runner у sys.path під час тестів
runner_dir = Path(__file__).resolve().parent.parent
if str(runner_dir) not in sys.path:
    sys.path.insert(0, str(runner_dir))
