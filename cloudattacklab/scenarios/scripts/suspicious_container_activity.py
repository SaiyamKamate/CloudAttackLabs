"""Scenario script: start a shell in the lab's test container.

Unlike the application scripts, this one is not a separate process: a container
scenario's script is loaded by the Scenario Engine and its `run(container)` is
called with a handle to the lab's test container. The handle offers one action,
`run_marker()`, which runs the fixed command defined in
backend/app/lab_manager/container.py (a shell that prints `id` and exits)
through the Docker adapter. The script cannot choose the command, the container
or any parameter, and it gets no scenario parameters.

It returns its summary as a dict, like the JSON line the other scripts print.
A non-zero exit code of the command makes the run fail.
"""


def run(container):
    result = container.run_marker()
    return {
        "scenario": "suspicious_container_activity",
        "target": container.target,
        "container": container.name,
        "command": " ".join(result.command),
        "exit_code": result.exit_code,
        "output": result.output[:500],
    }
