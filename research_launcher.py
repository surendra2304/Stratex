import os
import sys
import subprocess

def main():
    """
    Research Launcher
    Ensures that any research script is run in an explicit RESEARCH_MODE.
    This guarantees mathematically that the execution engine will refuse
    to place any exchange orders, avoiding accidental live trading.
    """
    if len(sys.argv) < 2:
        print("Usage: python research_launcher.py <research_script.py> [args...]")
        sys.exit(1)
        
    script = sys.argv[1]
    args = sys.argv[2:]
    
    # Explicit Safety Boundary
    env = os.environ.copy()
    env["RESEARCH_MODE"] = "1"
    
    print(f"🚀 Launching Research Script: {script}")
    print("🛡️  RESEARCH_MODE=1 explicitly activated.")
    
    cmd = [sys.executable, script] + args
    result = subprocess.run(cmd, env=env)
    
    sys.exit(result.returncode)

if __name__ == "__main__":
    main()
