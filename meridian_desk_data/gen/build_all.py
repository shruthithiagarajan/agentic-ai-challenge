"""Rebuild the whole dataset deterministically. Run: python build_all.py"""
import subprocess, sys, os
here = os.path.dirname(os.path.abspath(__file__))
for s in ["core.py", "policies.py", "emails.py", "golden_and_workflows.py", "evals_security.py", "infra_data.py", "readme_and_check.py"]:
    print(">>", s)
    subprocess.run([sys.executable, os.path.join(here, s)], check=True)
