"""A floating desktop pet for PokeTokenBar Web.

Runs on the host (the backend lives in a container and has no display), and is a
read-only consumer of the existing HTTP API: it holds no game state and writes
nothing back. See docs/floating-pet-linux.md for the design.

Requires system PyGObject — run under /usr/bin/python3, not a venv.
"""
