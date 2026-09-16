"""Isolierter Windows-Setup- und Starttest ohne Nutzerdaten oder API-Aufruf."""

import json
import os
import shutil
import socket
import subprocess
import sys
import tempfile
import time
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def free_port():
    with socket.socket() as listener:
        listener.bind(("127.0.0.1", 0))
        return listener.getsockname()[1]


def main():
    (ROOT / "tmp").mkdir(exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="phase6-install-", dir=ROOT / "tmp") as directory:
        target = Path(directory)
        shutil.copytree(ROOT / "src", target / "src")
        for name in ("app.py", "requirements.txt", "setup.bat", "start.bat"):
            shutil.copy2(ROOT / name, target / name)
        environment = os.environ | {"AI_LEARNING_COMPANION_NO_PAUSE": "1"}
        setup = subprocess.run(
            ["cmd.exe", "/d", "/c", "setup.bat", sys.executable],
            cwd=target,
            env=environment,
            capture_output=True,
            text=True,
            timeout=600,
        )
        if setup.returncode:
            print(setup.stdout[-3000:])
            print(setup.stderr[-3000:])
            raise SystemExit(setup.returncode)
        expected = [
            target / ".venv" / "Scripts" / "python.exe",
            target / "data" / "application.db",
            target / "user_data",
            target / "knowledge_base",
            target / "chroma_db",
        ]
        assert all(path.exists() for path in expected)

        port = free_port()
        start_environment = environment | {
            "STREAMLIT_SERVER_HEADLESS": "true",
            "STREAMLIT_SERVER_PORT": str(port),
            "STREAMLIT_BROWSER_GATHER_USAGE_STATS": "false",
        }
        process = subprocess.Popen(
            ["cmd.exe", "/d", "/c", "start.bat"],
            cwd=target,
            env=start_environment,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        started = False
        try:
            deadline = time.time() + 90
            while time.time() < deadline:
                if process.poll() is not None:
                    break
                try:
                    with urllib.request.urlopen(
                        f"http://127.0.0.1:{port}/_stcore/health", timeout=2
                    ) as response:
                        started = response.status == 200
                        if started:
                            break
                except OSError:
                    time.sleep(0.5)
            assert started, "Streamlit wurde im frischen Testordner nicht erreichbar."
        finally:
            subprocess.run(
                ["taskkill", "/PID", str(process.pid), "/T", "/F"],
                capture_output=True,
                check=False,
            )
        report = {
            "setup": "ok",
            "database": "ok",
            "directories": "ok",
            "streamlit_start": "ok",
            "api_calls": 0,
        }
        (ROOT / "tmp" / "phase6_fresh_install_report.json").write_text(
            json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        print(json.dumps(report, ensure_ascii=False))


if __name__ == "__main__":
    main()
