import subprocess
import sys

result = subprocess.run(
    [sys.executable, "-m", "pytest",
     "/Users/mikata/Desktop/claudecodetest/stock-chart/backend/tests/",
     "-v", "--tb=short"],
    capture_output=True,
    text=True,
    cwd="/Users/mikata/Desktop/claudecodetest/stock-chart/backend"
)

with open("/Users/mikata/Desktop/claudecodetest/stock-chart/test_output.txt", "w") as f:
    f.write("STDOUT:\n")
    f.write(result.stdout)
    f.write("\nSTDERR:\n")
    f.write(result.stderr)
    f.write(f"\nEXIT CODE: {result.returncode}\n")
