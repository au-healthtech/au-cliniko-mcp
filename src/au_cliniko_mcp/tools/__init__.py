"""Tool registration modules. One module per Cliniko resource group.

Each module exposes a `register(mcp, client)` function that wires its tools onto
the FastMCP instance. The server.py boot path calls each register() in turn.

Naming convention:
    - Modules are plural Cliniko resource names: patients.py, appointments.py, ...
    - Tool functions are verb-noun: list_patients, get_patient, draft_treatment_note.
    - Every tool docstring follows the LLM-optimised template (see CONTRIBUTING.md).
"""
