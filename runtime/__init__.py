"""Container runtime for the yProv4Agents streaming system.

Wraps the canonical `yprov4agenticai` core agents as long-running NATS subscribers so
the whole producer → bus → operational-agent → meta-agent pipeline runs as containers.
See `deploy/README.md` for how to run it.
"""
