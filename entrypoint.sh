#!/bin/sh

# Fix ownership of /config
chown -R cluser:comics /config

# Run the app
exec python3 app.py