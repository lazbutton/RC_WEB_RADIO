import secrets
import time
from subprocess import Popen, run
from tempfile import TemporaryDirectory

import requests


def test_prod_installer():
    """
    The interface/ tests contain a fixture that creates a development installation.
    This tests that installer still works in production mode.
    """
    admin_username = "testadmin"
    admin_password = secrets.token_hex(16)

    with TemporaryDirectory() as tmpdir:
        print(f"Using temporary directory {tmpdir}, admin password is {admin_password}")  # noqa:T201
        config_path = tmpdir + "/radiotomate.yaml"
        run(["radiotomate", "install", "-d", tmpdir], check=True)
        run(
            [
                "radiotomate",
                "--config-path",
                config_path,
                "users",
                "add",
                admin_username,
                "--password",
                admin_password,
                "--admin",
            ],
            check=True,
        )
        server = Popen(["radiotomate", "-c", config_path, "interface", "--demo"])

        # wait a bit until the server is available
        for _ in range(1000):
            try:
                requests.get("http://127.0.0.1:6811")
                break
            except requests.exceptions.ConnectionError:
                time.sleep(0.01)
        else:
            raise RuntimeError("Interface server is not responding. Aborting.")

        server.kill()
