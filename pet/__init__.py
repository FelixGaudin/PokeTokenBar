"""A floating desktop pet for PokeTokenBar Web.

Runs on the host (the backend lives in a container and has no display), and is a
client of the existing HTTP API: it holds no game state, and writes back only prayers
over the egg and the back-view setting. See docs/floating-pet-linux.md for the design.

Requires system PyGObject — run under /usr/bin/python3, not a venv.
"""
