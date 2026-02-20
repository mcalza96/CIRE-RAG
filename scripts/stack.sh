#!/bin/bash

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
RAG_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"
RUN_DIR="$SCRIPT_DIR/.run"
LOG_DIR="$SCRIPT_DIR/.logs"

mkdir -p "$RUN_DIR" "$LOG_DIR"

RAG_API_PID_FILE="$RUN_DIR/rag-api.pid"
RAG_WORKER_PID_FILE="$RUN_DIR/rag-worker.pid"
COMMUNITY_WORKER_PID_FILE="$RUN_DIR/community-worker.pid"

RAG_API_LOG="$LOG_DIR/rag-api.log"
RAG_WORKER_LOG="$LOG_DIR/rag-worker.log"
COMMUNITY_WORKER_LOG="$LOG_DIR/community-worker.log"

load_root_env() {
  local env_local="$RAG_DIR/.env.local"
  local env_file="$RAG_DIR/.env"

  set -a
  if [ -f "$env_file" ]; then
    # shellcheck disable=SC1090
    source "$env_file"
  fi
  if [ -f "$env_local" ]; then
    # shellcheck disable=SC1090
    source "$env_local"
  fi
  set +a
}

kill_port_if_busy() {
  local port="$1"
  local pids
  pids=$(lsof -tiTCP:"$port" -sTCP:LISTEN 2>/dev/null || true)
  if [ -n "$pids" ]; then
    echo "⚠️  Puerto $port ocupado. Cerrando PID(s): $pids"
    kill -9 $pids 2>/dev/null || true
    sleep 1
  fi
}

is_pid_alive() {
  local pid="$1"
  kill -0 "$pid" 2>/dev/null
}

start_process() {
  local name="$1"
  local cmd="$2"
  local workdir="$3"
  local pid_file="$4"
  local log_file="$5"

  if [ -f "$pid_file" ]; then
    local old_pid
    old_pid=$(cat "$pid_file" 2>/dev/null || true)
    if [ -n "$old_pid" ] && is_pid_alive "$old_pid"; then
      echo "ℹ️  $name ya está corriendo (PID $old_pid)"
      return
    fi
  fi

  echo "▶️  Iniciando $name..."
  (
    cd "$workdir"
    # Use non-login shell to preserve the working directory for Python package imports.
    nohup bash -c "$cmd" >> "$log_file" 2>&1 &
    echo $! > "$pid_file"
  )
  sleep 1

  local new_pid
  new_pid=$(cat "$pid_file" 2>/dev/null || true)
  if [ -n "$new_pid" ] && is_pid_alive "$new_pid"; then
    echo "✅ $name iniciado (PID $new_pid)"
  else
    echo "❌ No se pudo iniciar $name"
    echo "   Revisa logs: $log_file"
  fi
}

stop_process() {
  local name="$1"
  local pid_file="$2"

  if [ ! -f "$pid_file" ]; then
    echo "ℹ️  $name no tiene PID registrado"
    return
  fi

  local pid
  pid=$(cat "$pid_file" 2>/dev/null || true)
  if [ -z "$pid" ]; then
    rm -f "$pid_file"
    echo "ℹ️  PID inválido para $name"
    return
  fi

  if is_pid_alive "$pid"; then
    echo "⏹️  Deteniendo $name (PID $pid)..."
    kill "$pid" 2>/dev/null || true
    sleep 1
    if is_pid_alive "$pid"; then
      kill -9 "$pid" 2>/dev/null || true
    fi
  else
    echo "ℹ️  $name ya estaba detenido"
  fi

  rm -f "$pid_file"
  echo "✅ $name detenido"
}

status_process() {
  local name="$1"
  local pid_file="$2"

  if [ ! -f "$pid_file" ]; then
    echo "- $name: detenido"
    return
  fi

  local pid
  pid=$(cat "$pid_file" 2>/dev/null || true)
  if [ -n "$pid" ] && is_pid_alive "$pid"; then
    echo "- $name: activo (PID $pid)"
  else
    echo "- $name: detenido (PID stale)"
  fi
}

start_all() {
  load_root_env

  kill_port_if_busy 8000

  : > "$RAG_API_LOG"
  : > "$RAG_WORKER_LOG"
  : > "$COMMUNITY_WORKER_LOG"
  start_process "RAG API" "./scripts/start_api.sh" "$RAG_DIR" "$RAG_API_PID_FILE" "$RAG_API_LOG"
  start_process "RAG Worker" "./scripts/start_worker.sh" "$RAG_DIR" "$RAG_WORKER_PID_FILE" "$RAG_WORKER_LOG"
  start_process "Community Worker" "venv/bin/python -m app.workers.community_worker" "$RAG_DIR" "$COMMUNITY_WORKER_PID_FILE" "$COMMUNITY_WORKER_LOG"
}

stop_all() {
  stop_process "Community Worker" "$COMMUNITY_WORKER_PID_FILE"
  stop_process "RAG Worker" "$RAG_WORKER_PID_FILE"
  stop_process "RAG API" "$RAG_API_PID_FILE"
}

show_status() {
  echo "📊 Estado de servicios"
  status_process "RAG API" "$RAG_API_PID_FILE"
  status_process "RAG Worker" "$RAG_WORKER_PID_FILE"
  status_process "Community Worker" "$COMMUNITY_WORKER_PID_FILE"
}

show_logs() {
  local target="${1:-all}"
  case "$target" in
    rag-api) tail -n 80 "$RAG_API_LOG" ;;
    rag-worker) tail -n 80 "$RAG_WORKER_LOG" ;;
    community-worker) tail -n 80 "$COMMUNITY_WORKER_LOG" ;;
    all)
      echo "--- rag-api ---" && tail -n 40 "$RAG_API_LOG"
      echo "--- rag-worker ---" && tail -n 40 "$RAG_WORKER_LOG"
      echo "--- community-worker ---" && tail -n 40 "$COMMUNITY_WORKER_LOG"
      ;;
    *)
      echo "Uso logs: ./stack.sh logs [rag-api|rag-worker|community-worker|all]"
      exit 1
      ;;
  esac
}

case "${1:-}" in
  up|start)
    start_all
    show_status
    ;;
  down|stop)
    stop_all
    show_status
    ;;
  restart)
    stop_all
    start_all
    show_status
    ;;
  status)
    show_status
    ;;
  logs)
    show_logs "${2:-all}"
    ;;
  *)
    echo "Uso:"
    echo "  ./stack.sh up|down|restart|status"
    echo "  ./stack.sh logs [rag-api|rag-worker|community-worker|all]"
    exit 1
    ;;
esac
