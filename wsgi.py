import os
from pathlib import Path

# Hosting workers may start with a different working directory.
os.chdir(Path(__file__).resolve().parent)

from main import create_application  # noqa: E402

# HTTP requests do all work. No polling, background threads or scheduler here.
application = create_application()
