# Deliberately unsafe STATIC fixtures: never execute or import this file.
import httpx
import requests
import subprocess
import yaml
from httpx import AsyncClient
from yaml import load

# ruleid: python-insecure-tls
httpx.Client(verify=False)
# ruleid: python-insecure-tls
AsyncClient(verify=False)
# ruleid: python-insecure-tls
requests.get("https://example.com", verify=False)
# ruleid: python-insecure-tls
session.verify = False
# ok: python-insecure-tls
httpx.AsyncClient(verify=True)
# ok: python-insecure-tls
requests.get("https://example.com")

# ruleid: python-dynamic-execution
eval(source)
# ruleid: python-dynamic-execution
exec(source)
# ok: python-dynamic-execution
int("42")

# ruleid: python-unsafe-yaml
yaml.load(source)
# ruleid: python-unsafe-yaml
load(source, Loader=yaml.Loader)
# ruleid: python-unsafe-yaml
yaml.unsafe_load(source)
# ruleid: python-unsafe-yaml
yaml.full_load(source)
# ok: python-unsafe-yaml
yaml.safe_load(source)
# ok: python-unsafe-yaml
yaml.load(source, Loader=yaml.SafeLoader)
# ok: python-unsafe-yaml
yaml.load(source, Loader=yaml.CSafeLoader)

# ruleid: python-shell-subprocess
subprocess.run(command, shell=True)
# ok: python-shell-subprocess
subprocess.run(["echo", "hello"], check=True)
