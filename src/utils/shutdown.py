"""Process-wide shutdown signal.

Owned here, not in main_app, so a module can wait on it without importing the
app: that import constructs the singletons, takes the runtime lock, and starts
the background threads.
"""
import threading

shutdown_event = threading.Event()
