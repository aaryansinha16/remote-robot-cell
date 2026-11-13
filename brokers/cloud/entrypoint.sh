#!/bin/sh
set -eu
umask 077

: "${CLOUD_MQTT_PASSWORD:?missing}"
: "${SITE_ID:?missing}"
: "${SITE_MQTT_PASSWORD:?missing}"

auth=/mosquitto/auth
mkdir -p "$auth"
rm -f "$auth/passwd"
mosquitto_passwd -c -b "$auth/passwd" cloud-api "$CLOUD_MQTT_PASSWORD"
mosquitto_passwd -b "$auth/passwd" "$SITE_ID" "$SITE_MQTT_PASSWORD"
cp /broker/acl "$auth/acl"
chown -R mosquitto:mosquitto "$auth"
chmod 0700 "$auth"
chmod 0600 "$auth/passwd" "$auth/acl"

exec /docker-entrypoint.sh "$@"
