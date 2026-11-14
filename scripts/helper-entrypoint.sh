#!/bin/sh
set -eu

mkdir -p /run/swag-ui
chown "root:${SWAG_UI_SOCKET_GID:-10001}" /run/swag-ui
chmod 0770 /run/swag-ui
umask 0007
exec uvicorn helper.control:app --uds /run/swag-ui/control.sock --workers 1
