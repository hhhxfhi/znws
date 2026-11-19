from pathlib import Path
import runpy, sys
payload = Path(__file__).resolve().parent / 'payload/0.8.3-dev.5-ac40bf15ffe4'
sys.path.insert(0, str(payload))
runpy.run_path(str(payload / 'check_runtime.py'), run_name="__main__")
