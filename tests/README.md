# Tests

The options-flow regression tests use an installed Home Assistant runtime on
Linux, including its real config-entry and options-flow managers. They create
temporary configuration directories and synthetic entries; no real credentials
or existing Home Assistant installation are required.

For the supported minimum, use Python 3.12 in an isolated environment:

```sh
python -m venv .venv
. .venv/bin/activate
pip install --constraint https://raw.githubusercontent.com/home-assistant/core/2024.12.0/homeassistant/package_constraints.txt --constraint tests/constraints-runtime.txt 'homeassistant==2024.12.0' pycryptodome pytest==8.3.4 pytest-asyncio==0.24.0
pytest -q tests/test_options_flow_runtime.py
```

Repeat in a separate Python 3.13 environment with `2025.12.0` in both the
constraint URL and package version to test the release that removed the
`OptionsFlow.config_entry` setter. Both exact versions are included in CI.
The extra constraints file keeps the `pycares` API expected by their `aiodns`;
it applies to this older-release matrix, rather than current HA releases.

The runtime tests exercise opening and serializing the options form, saved and
default values, scan-interval validation, saving through the options manager,
reopening, cancellation, and the real entry reload updating the coordinator's
polling interval. They also preserve the existing setup precedence: options,
then entry data, then the 60-second default. The form's existing default remains
the saved option or 60 seconds, independently of the entry-data fallback.

The cloud client, cloud polling, heartbeat scheduling, and entity-platform
forwarding are isolated. These tests verify the form payload used by the
frontend; they do not drive a browser or contact a battery account. They can run
in containers with networking disabled after dependencies have been installed.

Run the complete existing suite with `pytest -q` in either environment.
