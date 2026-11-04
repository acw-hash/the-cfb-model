from pathlib import Path
import difflib, subprocess
main = subprocess.check_output(["git","show","origin/main:scripts/publish_week8.py"], text=True)
cur = Path("scripts/publish_week9.py").read_text(encoding="utf-8")
for line in difflib.unified_diff(main.splitlines(), cur.splitlines(), fromfile="scripts/publish_week8.py", tofile="scripts/publish_week9.py", lineterm=""):
    print(line)
print("--- checks ---")
print("week=8 stray", "week=8" in cur)
print("daily", "daily" in cur.lower())
print("old date", "2026-10-" in cur)
