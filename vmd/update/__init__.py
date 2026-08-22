"""Updating this copy of VMD from a USB stick.

Everything in this package is stdlib only, and that is a rule rather than a
habit: `vmd/update/apply.py` is run by the bundled interpreter with no virtual
environment at all, at the moment when the environment is being replaced. An
import of pydantic here would be an updater that cannot run during an update.
"""
