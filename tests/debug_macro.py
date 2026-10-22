import subprocess, sys
from pathlib import Path
sys.path.insert(0, ".")
import app

PLAN = "/tmp/temoin_recap.xml"
if not Path(PLAN).exists():
    subprocess.run([sys.executable, "temoin_recap.py", PLAN], check=True)
for niveau in (1, 3):
    sortie = subprocess.run([sys.executable, "montecarlo.py", PLAN, "--sims", "200",
                             "--macro-level", str(niveau)],
                            capture_output=True, text=True, timeout=600).stdout
    p = app.parse_montecarlo_output(sortie)
    print(f"--- niveau {niveau} ---")
    print("macro_level :", p["macro_level"])
    print("macros      :", len(p["macros"]))
    print("message     :", repr(p["macro_message"]))
    print("recap_links :", p["macro_recap_links"], p["macro_recap_examples"])
    print("warning     :", repr(p.get("macro_warning", ""))[:120])
    for ligne in sortie.splitlines():
        if "ecapitulative" in ligne:
            print("   ligne brute :", ligne)
