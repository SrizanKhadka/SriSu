#!/bin/sh
set -eu

export PYTHONPATH="/srisu-config${PYTHONPATH:+:$PYTHONPATH}"
python /srisu-config/render_config.py

# The image drops from root to Synapse's UID/GID (991:991) in /start.py.
# render_config.py runs first so it can atomically refresh the private config,
# which means a fresh named volume and the rendered file are root-owned until
# we hand both paths to the runtime user.
if [ "$(id -u)" -eq 0 ]; then
    chown "${UID:-991}:${GID:-991}" /data "$SYNAPSE_CONFIG_PATH"
    registration="$(dirname "$SYNAPSE_CONFIG_PATH")/srisu-notifications-as.yaml"
    if [ -f "$registration" ]; then
        chown "${UID:-991}:${GID:-991}" "$registration"
    fi
fi

exec /start.py
