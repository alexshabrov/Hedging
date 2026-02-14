set -e

required_env=(
  "BINANCE_KEY"
  "BINANCE_SECRET"
  "PRIVATE_KEY"
  "FRONT_SECRET_KEY"
  "FRONT_ADMIN_PASSWORD"
  "RPC_KEY"
)

for env_name in "${required_env[@]}"; do
  if [[ -z "${!env_name:-}" ]]; then
    echo "[run.sh] missing required env: ${env_name}" >&2
    exit 1
  fi
done

while true; do
  python run.py
  sleep 1
done