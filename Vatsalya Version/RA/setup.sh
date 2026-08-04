#!/bin/bash
# Bootstraps MySQL + Qdrant on a RunPod-style sandbox where dockerd cannot
# run (no overlayfs/iptables permission -- see README.md's "No-Docker
# fallback") and only /workspace survives a pod restart; everything else
# (apt packages, /opt, /var/lib/mysql) is wiped when the pod's container
# is recreated.
#
# Safe to re-run any time (after a restart, or just to check status): all
# persistent state lives under $DATA_ROOT on the /workspace mount, so a
# re-run detects existing data and skips re-seeding it -- only missing
# binaries/services get (re)installed/(re)started.
#
# First-run data sources:
#   - MySQL:  ../flipkart_metadata.sql            (flat product_metadata dump)
#   - Qdrant: backups/qdrant_data.tar.gz           (chunks/entities/relationships)
#   - LightRAG: backups/lightrag_storage/          (graph + doc status + KV stores)
# If those backup files are absent, the corresponding store just comes up
# empty -- this script does not fabricate data that isn't there.

set -uo pipefail

PROJECT_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
BACKUPS_DIR="$PROJECT_ROOT/backups"
DATA_ROOT="/workspace/ra_data"
MYSQL_DATADIR="$DATA_ROOT/mysql/data"
QDRANT_BIN_DIR="$DATA_ROOT/qdrant/bin"
QDRANT_BIN="$QDRANT_BIN_DIR/qdrant"
QDRANT_STORAGE="$DATA_ROOT/qdrant/storage"
LOG_DIR="$DATA_ROOT/logs"
MYSQL_CNF="/etc/mysql/mysql.conf.d/mysqld.cnf"
FLAT_METADATA_SQL="$(dirname "$PROJECT_ROOT")/flipkart_metadata.sql"

mkdir -p "$MYSQL_DATADIR" "$QDRANT_BIN_DIR" "$QDRANT_STORAGE" "$LOG_DIR"

# Symlink so the data is also browsable from inside the repo, without
# actually living on the ephemeral container filesystem.
[ -L "$PROJECT_ROOT/runtime" ] || ln -sfn "$DATA_ROOT" "$PROJECT_ROOT/runtime"

echo "=========================================="
echo "1. MySQL"
echo "=========================================="

if ! command -v mysqld >/dev/null 2>&1; then
  echo "mysql-server not installed -- installing (apt)..."
  export DEBIAN_FRONTEND=noninteractive
  apt-get update -qq
  apt-get install -y -qq mysql-server
fi

# Idempotent config patch: point datadir at the persistent path, listen on
# all interfaces, and use port 3307 (this repo's convention, matches
# ../README.md's Docker setup -- avoids clashing with any default 3306).
if [ -f "$MYSQL_CNF" ]; then
  sed -i -E \
    -e "s|^#?[[:space:]]*datadir[[:space:]]*=.*|datadir\t\t= ${MYSQL_DATADIR}|" \
    -e "s|^#?[[:space:]]*port[[:space:]]*=.*|port\t\t\t= 3307|" \
    -e "s|^bind-address[[:space:]]*=.*|bind-address\t\t= 0.0.0.0|" \
    "$MYSQL_CNF"
fi

FRESH_MYSQL=0
if [ -z "$(ls -A "$MYSQL_DATADIR" 2>/dev/null)" ]; then
  FRESH_MYSQL=1
  if [ -d /var/lib/mysql ] && [ -n "$(ls -A /var/lib/mysql 2>/dev/null)" ]; then
    echo "Seeding persistent datadir from apt's freshly-initialized /var/lib/mysql..."
    rsync -a /var/lib/mysql/ "$MYSQL_DATADIR/"
  else
    echo "Initializing empty MySQL datadir..."
    mysqld --initialize-insecure --datadir="$MYSQL_DATADIR" --user=mysql
  fi
fi
chown -R mysql:mysql "$MYSQL_DATADIR"

mkdir -p /var/run/mysqld && chown mysql:mysql /var/run/mysqld
service mysql start >/dev/null 2>&1 || service mysql restart >/dev/null 2>&1

echo -n "Waiting for MySQL..."
for i in $(seq 1 30); do
  mysqladmin --socket=/var/run/mysqld/mysqld.sock ping >/dev/null 2>&1 && { echo " up"; break; }
  echo -n "."
  sleep 1
done

if [ "$FRESH_MYSQL" = "1" ]; then
  echo "First-run: creating flipkart db/user and importing metadata..."
  mysql -u root -e "
    CREATE DATABASE IF NOT EXISTS flipkart CHARACTER SET utf8mb4;
    CREATE USER IF NOT EXISTS 'flipkart_user'@'%' IDENTIFIED WITH mysql_native_password BY 'flipkart_pass';
    GRANT ALL PRIVILEGES ON flipkart.* TO 'flipkart_user'@'%';
    ALTER USER 'root'@'localhost' IDENTIFIED WITH mysql_native_password BY 'rootpass';
    FLUSH PRIVILEGES;
  "
  if [ -f "$FLAT_METADATA_SQL" ]; then
    sed 's/^INSERT INTO/INSERT IGNORE INTO/' "$FLAT_METADATA_SQL" > /tmp/flipkart_metadata_ignore.sql
    mysql -u root -prootpass flipkart < /tmp/flipkart_metadata_ignore.sql
    rm -f /tmp/flipkart_metadata_ignore.sql
    echo "  Imported $FLAT_METADATA_SQL"
  else
    echo "  WARNING: $FLAT_METADATA_SQL not found -- flipkart db created empty."
  fi
else
  echo "Persistent datadir already had data -- skipped re-import."
fi

echo ""
echo "=========================================="
echo "2. Qdrant"
echo "=========================================="

if [ ! -x "$QDRANT_BIN" ]; then
  echo "Qdrant binary not present -- downloading (musl build, for old-glibc hosts)..."
  curl -sL https://github.com/qdrant/qdrant/releases/latest/download/qdrant-x86_64-unknown-linux-musl.tar.gz -o /tmp/qdrant.tar.gz
  tar xzf /tmp/qdrant.tar.gz -C "$QDRANT_BIN_DIR"
  rm -f /tmp/qdrant.tar.gz
  chmod +x "$QDRANT_BIN"
fi

if [ -z "$(ls -A "$QDRANT_STORAGE" 2>/dev/null)" ]; then
  if [ -f "$BACKUPS_DIR/qdrant_data.tar.gz" ]; then
    echo "First-run: seeding Qdrant storage from backups/qdrant_data.tar.gz..."
    tar xzf "$BACKUPS_DIR/qdrant_data.tar.gz" -C "$QDRANT_STORAGE"
  else
    echo "  No backups/qdrant_data.tar.gz found -- Qdrant will start empty."
  fi
else
  echo "Persistent Qdrant storage already has data -- skipped re-seed."
fi

if ! pgrep -f "$QDRANT_BIN" >/dev/null 2>&1; then
  ( cd "$QDRANT_BIN_DIR" && QDRANT__STORAGE__STORAGE_PATH="$QDRANT_STORAGE" nohup "$QDRANT_BIN" > "$LOG_DIR/qdrant.log" 2>&1 & disown )
fi

echo -n "Waiting for Qdrant..."
for i in $(seq 1 30); do
  curl -sf http://localhost:6333/collections >/dev/null 2>&1 && { echo " up"; break; }
  echo -n "."
  sleep 1
done

echo ""
echo "=========================================="
echo "3. LightRAG storage (graph + doc status + KV stores)"
echo "=========================================="

if [ ! -d "$PROJECT_ROOT/lightrag_storage" ]; then
  if [ -d "$BACKUPS_DIR/lightrag_storage" ]; then
    echo "Restoring lightrag_storage/ from backups/..."
    cp -r "$BACKUPS_DIR/lightrag_storage" "$PROJECT_ROOT/lightrag_storage"
  else
    echo "  No backups/lightrag_storage/ found -- nothing to restore."
  fi
else
  echo "lightrag_storage/ already present -- left untouched."
fi

echo ""
echo "=========================================="
echo "Verification"
echo "=========================================="

echo "-- MySQL (flipkart @ 127.0.0.1:3307) --"
mysql -u root -prootpass flipkart -e "SELECT COUNT(*) AS product_metadata_rows FROM product_metadata;" 2>/dev/null

echo "-- Qdrant collections (http://localhost:6333) --"
for c in $(curl -s http://localhost:6333/collections | python3 -c "import json,sys;print(' '.join(c['name'] for c in json.load(sys.stdin)['result']['collections']))" 2>/dev/null); do
  curl -s "http://localhost:6333/collections/$c" | python3 -c "import json,sys; d=json.load(sys.stdin)['result']; print(f'  {\"$c\"}: {d[\"points_count\"]} points')" 2>/dev/null
done

if [ -f "$PROJECT_ROOT/lightrag_storage/kv_store_doc_status.json" ]; then
  echo "-- LightRAG doc_status --"
  python3 -c "
import json
from collections import Counter
d = json.load(open('$PROJECT_ROOT/lightrag_storage/kv_store_doc_status.json'))
c = Counter(v.get('status') for v in d.values())
print(f'  total: {len(d)}')
for k, v in c.most_common():
    print(f'  {k}: {v}')
"
fi

echo ""
echo "Done. Re-run this script any time (e.g. after a pod restart) to bring both services back up from persistent /workspace state."
