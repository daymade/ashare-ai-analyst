"""Run the local workbench and its collectors from the canonical checkout."""

import os
from pathlib import Path
import signal
import socket
import subprocess
import sys
import time

from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parents[1]


def main() -> None:
    os.chdir(ROOT)
    load_dotenv(ROOT / ".env")
    env = os.environ.copy()
    env.update(
        CELERY_BROKER_URL="redis://127.0.0.1:16379/0",
        CELERY_RESULT_BACKEND="redis://127.0.0.1:16379/1",
        REDIS_PORT="127.0.0.1:16379",
        DISCORD_WEBHOOK_URL="",
        DISCORD_BOT_TOKEN="",
        PYTHONUNBUFFERED="1",
    )
    for port in (8000, 5174):
        with socket.socket() as sock:
            try:
                sock.bind(("127.0.0.1", port))
            except OSError:
                raise SystemExit(f"Port {port} is occupied; stop its owner first.")
    if not (ROOT / "frontend/node_modules/.bin/vite").exists():
        raise SystemExit("Install frontend dependencies first: cd frontend && npm ci")

    log_dir = ROOT / "logs/dev"
    log_dir.mkdir(parents=True, exist_ok=True)
    compose = ["docker", "compose", "-p", "ashare-ai-analyst-dev"]
    subprocess.run([*compose, "up", "-d", "redis"], env=env, check=True)
    children = []
    logs = []

    def stop(_signal, _frame):
        raise KeyboardInterrupt

    signal.signal(signal.SIGTERM, stop)
    try:
        commands = {
            "api": [
                sys.executable,
                "-m",
                "uvicorn",
                "src.web.app:app",
                "--host",
                "127.0.0.1",
                "--port",
                "8000",
            ],
            "frontend": [
                "node",
                "frontend/node_modules/vite/bin/vite.js",
                "frontend",
                "--host",
                "127.0.0.1",
                "--port",
                "5174",
                "--strictPort",
            ],
            "worker": [
                sys.executable,
                "-m",
                "celery",
                "-A",
                "openclaw.celery_app",
                "worker",
                "--pool=threads",
                "--concurrency=2",
                "--loglevel=info",
                "--hostname=local-workbench@%h",
            ],
            "beat": [
                sys.executable,
                "-m",
                "celery",
                "-A",
                "openclaw.celery_app",
                "beat",
                "--loglevel=info",
                "--schedule",
                str(log_dir / "celerybeat-schedule"),
                "--pidfile",
                "",
            ],
        }
        for name, command in commands.items():
            log = (log_dir / f"{name}.log").open("a")
            logs.append(log)
            children.append(
                (
                    name,
                    subprocess.Popen(
                        command, env=env, stdout=log, stderr=subprocess.STDOUT
                    ),
                )
            )
        print(f"Workbench: http://127.0.0.1:5174 · logs: {log_dir}", flush=True)
        # Populate the feed immediately; beat continues the existing 30-minute schedule.
        subprocess.run(
            [
                sys.executable,
                "-m",
                "celery",
                "-A",
                "openclaw.celery_app",
                "call",
                "openclaw.tasks.ai_news_pipeline.task_fetch_ai_news",
            ],
            env=env,
            stdout=logs[2],
            stderr=subprocess.STDOUT,
            check=True,
            timeout=30,
        )
        while True:
            for name, child in children:
                if child.poll() is not None:
                    raise RuntimeError(
                        f"{name} exited with {child.returncode}; see {log_dir / (name + '.log')}"
                    )
            time.sleep(1)
    except KeyboardInterrupt:
        pass
    finally:
        for _, child in reversed(children):
            if child.poll() is None:
                child.terminate()
        for _, child in children:
            try:
                child.wait(timeout=15)
            except subprocess.TimeoutExpired:
                child.kill()
                child.wait()
        for log in logs:
            log.close()
        # Stop only this launcher's Redis project. Keep its volume and all project data.
        subprocess.run([*compose, "stop", "redis"], env=env, check=False)


if __name__ == "__main__":
    main()
