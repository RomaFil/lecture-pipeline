#!/bin/bash
# Ollama у user-space (без root): слухає лише 127.0.0.1, модель вивантажується одразу
# після запиту (KEEP_ALIVE=0), бо RAM на VPS ділиться з whisper.
export OLLAMA_HOST=127.0.0.1:11434
export OLLAMA_KEEP_ALIVE=0
export OLLAMA_MAX_LOADED_MODELS=1
export OLLAMA_NUM_PARALLEL=1
export OLLAMA_MODELS="$HOME/.ollama/models"
exec "$HOME/.local/bin/ollama" serve
