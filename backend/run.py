"""Local API backend entry point."""

import socket

import uvicorn

from school_face_id.config.settings import APP_AUTO_PORT, APP_HOST, APP_PORT
from school_face_id.main import app


def find_free_port(start_port: int, attempts: int = 20) -> int:
    for port in range(start_port, start_port + attempts):
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
            try:
                sock.bind((APP_HOST, port))
                return port
            except OSError:
                continue
    raise RuntimeError("No free backend port found")


if __name__ == "__main__":
    port = find_free_port(APP_PORT) if APP_AUTO_PORT else APP_PORT
    uvicorn.run(app, host=APP_HOST, port=port)
