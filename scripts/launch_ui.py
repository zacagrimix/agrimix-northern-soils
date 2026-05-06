"""Launch DuckDB's web UI against db/agrimix.duckdb.

Run with: python scripts/launch_ui.py
Then open the URL it prints (default http://localhost:4213/).
Ctrl-C to stop.
"""
import signal
import sys
import time
from pathlib import Path

import duckdb

DB = Path(__file__).resolve().parent.parent / "db" / "agrimix.duckdb"

con = duckdb.connect(str(DB))
con.execute("INSTALL ui")
con.execute("LOAD ui")
url = con.execute("CALL start_ui()").fetchone()[0]
print(url)
print("Ctrl-C to stop.")

def _stop(*_):
    print("\nStopping UI.")
    sys.exit(0)

signal.signal(signal.SIGINT, _stop)
signal.signal(signal.SIGTERM, _stop)

while True:
    time.sleep(3600)
