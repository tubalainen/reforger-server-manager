"""The Docker gate: each stack's only way to the Docker daemon (#204)."""

# How long the gate and the observer keep an idle connection open. uvicorn's
# default is 5 s, the same as the GUI's poll, so the server would close a
# pooled connection at the very moment the client sent its next request on it:
# docker-py then got "Remote end closed connection without response" and the
# page a 500. The real daemon has no idle timeout at all; this only has to be
# well past every poll interval.
KEEP_ALIVE_SECONDS = 600
