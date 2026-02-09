# Base
apt-get update
export TZ=UTC
export DEBIAN_FRONTEND=noninteractive
apt-get install -y wget nano curl git zip unzip screen net-tools build-essential iputils-ping

# Requirements
pip install -r requirements.txt