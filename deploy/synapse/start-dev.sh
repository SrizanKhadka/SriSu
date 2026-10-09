#!/bin/sh
set -eu

export PYTHONPATH="/srisu-config${PYTHONPATH:+:$PYTHONPATH}"
python /srisu-config/render_config.py
exec /start.py
