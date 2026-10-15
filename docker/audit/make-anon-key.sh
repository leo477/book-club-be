#!/bin/sh
# Prints an HS256 anon JWT for SECRET (default: the throwaway audit secret).
set -e
SECRET="${1:-audit-local-jwt-secret-0123456789abcdef0123456789}"
b64() { openssl base64 -A | tr '+/' '-_' | tr -d '='; }
H=$(printf '{"alg":"HS256","typ":"JWT"}' | b64)
P=$(printf '{"role":"anon","iss":"supabase-local","iat":1700000000,"exp":4102444800}' | b64)
S=$(printf '%s.%s' "$H" "$P" | openssl dgst -sha256 -hmac "$SECRET" -binary | b64)
printf '%s.%s.%s\n' "$H" "$P" "$S"
