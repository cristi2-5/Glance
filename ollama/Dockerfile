# Ollama with the embedding model baked in, CPU-only. A fresh container (cloud
# platforms restart them freely) serves embeddings immediately instead of
# downloading ~275 MB first.

# ---------- fetch: pull the model ----------
FROM ollama/ollama:0.40.12 AS fetch
ENV OLLAMA_MODELS=/models
# set -e so a failed pull fails the build instead of shipping an empty image.
# The model is pinned to v1.5 (stored vectors must keep matching the weights
# that made them) and aliased to `latest`, so the app's unchanged setting
# `nomic-embed-text` resolves to exactly those weights.
RUN set -e; \
    ollama serve & pid=$!; \
    for i in $(seq 1 60); do ollama list >/dev/null 2>&1 && break; sleep 1; done; \
    ollama pull nomic-embed-text:v1.5; \
    ollama cp nomic-embed-text:v1.5 nomic-embed-text:latest; \
    kill $pid; wait $pid || true; \
    test -n "$(ls -A /models/blobs)"; \
    cp -a /usr/lib/ollama /out-lib; \
    rm -rf /out-lib/cuda_* /out-lib/mlx_* /out-lib/rocm* /out-lib/vulkan /out-lib/include

# ---------- runtime ----------
FROM ubuntu:24.04
# ca-certificates keeps `ollama pull moondream` working for the
# AI_PROVIDER=ollama fallback.
RUN apt-get update \
    && apt-get install -y --no-install-recommends ca-certificates \
    && rm -rf /var/lib/apt/lists/* \
    && useradd --create-home --uid 10001 ollama

# GPU libraries (~4.6 GB) are never loaded on a CPU-only host, so they are
# stripped. Only /models is copied: /root/.ollama holds an id_ed25519 key
# generated at build time, which must not ship in the image.
COPY --from=fetch /usr/bin/ollama /usr/bin/ollama
COPY --from=fetch /out-lib /usr/lib/ollama
COPY --from=fetch --chown=ollama:ollama /models /home/ollama/.ollama/models

# Hardware rules from CLAUDE.md: one model loaded, no parallel requests.
ENV OLLAMA_HOST=0.0.0.0:11434 \
    OLLAMA_MODELS=/home/ollama/.ollama/models \
    OLLAMA_MAX_LOADED_MODELS=1 \
    OLLAMA_NUM_PARALLEL=1
USER ollama
EXPOSE 11434
HEALTHCHECK CMD ["ollama", "list"]
ENTRYPOINT ["/usr/bin/ollama"]
CMD ["serve"]
