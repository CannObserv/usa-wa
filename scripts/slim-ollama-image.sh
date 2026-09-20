#!/usr/bin/env bash
# Rebuild ollama/ollama:latest without its accelerator runtimes (issue #394).
#
# The measurement that motivates this: the stock image costs 8.06 GB on disk
# (3.27 GB of content) to serve one 274 MB embedding model, nomic-embed-text,
# which is all SocratiCode asks of it. 4.0 GB of that is accelerator runtime —
#
#     cuda_v12       1.2 G
#     cuda_v13       831 M
#     mlx_cuda_v13   2.0 G
#     vulkan          47 M
#
# — on a VM with no accelerator. That was 35% of a 25 GB volume and the single
# largest item on the host, larger than every repo tier combined by a factor of
# eight. Stripping it took the volume from 98% to 57% with a byte-identical
# embedding response.
#
# Why a script and not a one-off: `docker pull ollama/ollama:latest` restores the
# fat image, and SocratiCode hardcodes that exact tag (`OLLAMA_IMAGE` in its
# constants.ts) while `isOllamaImagePresent()` gates the pull on the tag being
# present locally. So the rebuild keeps the tag, stamps a label, and
# scripts/disk-gc.sh reports the day a pull undoes it.
#
# The model itself lives in the `socraticode_ollama_data` volume, not the image,
# and is untouched.
#
# Usage: slim-ollama-image.sh [--force]
# Exit: 0 slimmed or already slim, 1 refused or verification failed, 2 tooling.
#
# Pinned by scripts/tests/test_slim_ollama_image.py.
set -uo pipefail

FORCE=0
for arg in "$@"; do
    case "$arg" in
        --force) FORCE=1 ;;
        -h | --help)
            # Derived, not hardcoded: the header runs to the first line that
            # is not a comment, so inserting one cannot silently truncate the
            # usage text or leak the `Pinned by` line into it.
            sed -n '2,/^[^#]/p' "$0" | sed '/^# Pinned by/,$d' | sed 's/^# \{0,1\}//'
            exit 0
            ;;
        *)
            echo "slim-ollama-image: unknown argument: $arg" >&2
            exit 2
            ;;
    esac
done

DOCKER=${SLIM_DOCKER:-docker}
CURL=${SLIM_CURL:-curl}
GPU_GLOB=${SLIM_GPU_GLOB:-/dev/nvidia*}
MOUNT=${SLIM_MOUNT:-/}
# export|import materialises the replacement while the original still exists.
# Measured: the rebuilt image occupies ~652 MB; 2 GiB is that with room for the
# transient tar stream and the unpacked snapshot.
MIN_FREE_BYTES=${SLIM_MIN_FREE_BYTES:-2147483648}

IMAGE=ollama/ollama:latest
CONTAINER=socraticode-ollama
VOLUME=socraticode_ollama_data
PORT=${SLIM_OLLAMA_PORT:-11435}
MODEL=${SLIM_OLLAMA_MODEL:-nomic-embed-text}
EXPECTED_DIMS=${SLIM_EXPECTED_DIMS:-768}
SLIM_LABEL="cpu-only-gpu-libs-stripped"

#: Every accelerator runtime, enumerated. NOT /usr/lib/ollama itself — the CPU
#: backends (libggml-cpu-*.so, libllama.so, the server) live directly under it
#: and are what actually serves embeddings here.
GPU_DIRS=(cuda_v12 cuda_v13 mlx_cuda_v13 vulkan)

die() {
    echo "slim-ollama-image: $1" >&2
    exit "${2:-1}"
}

command -v "$DOCKER" >/dev/null 2>&1 || die "docker not found ($DOCKER)" 2

# ── refusals, before anything is touched ──────────────────────────────────────

# shellcheck disable=SC2206  # deliberate glob expansion
gpu_devices=(${GPU_GLOB:-})
if [ "${#gpu_devices[@]}" -gt 0 ] && [ -e "${gpu_devices[0]}" ]; then
    die "this host has a GPU (${gpu_devices[0]}) — the accelerator runtimes are the fast path here, not dead weight; refusing"
fi

free_bytes=$(df -Pk "$MOUNT" | awk 'NR==2 {print $4 * 1024}')
[ -n "$free_bytes" ] || die "could not read free space for $MOUNT" 2
if [ "$free_bytes" -lt "$MIN_FREE_BYTES" ]; then
    die "not enough free space: $free_bytes bytes on $MOUNT, need $MIN_FREE_BYTES — the rebuild coexists with the original before the swap"
fi

label=$("$DOCKER" image inspect "$IMAGE" --format '{{index .Config.Labels "dev.usa-wa.slim"}}' 2>/dev/null) ||
    die "$IMAGE is not present locally — nothing to slim" 2
if [ "$label" = "$SLIM_LABEL" ] && [ "$FORCE" -eq 0 ]; then
    echo "slim-ollama-image: $IMAGE is already slim; nothing to do (--force to rebuild anyway)"
    exit 0
fi

# ── strip, in the running container's writable layer ──────────────────────────
#
# `docker export` reads the container's MERGED filesystem, so files deleted here
# are simply absent from the export — which is what flattens 8.06 GB into one
# ~652 MB layer. A `RUN rm -rf` in a Dockerfile would not: it adds a whiteout on
# top of the layers that still carry the bytes.

strip_cmd="rm -rf"
for directory in "${GPU_DIRS[@]}"; do
    strip_cmd+=" /usr/lib/ollama/$directory"
done

echo "slim-ollama-image: stripping accelerator runtimes from $CONTAINER"
"$DOCKER" exec "$CONTAINER" sh -c "$strip_cmd" || die "could not strip $CONTAINER" 2

echo "slim-ollama-image: flattening into a single-layer image"
"$DOCKER" export "$CONTAINER" | "$DOCKER" import \
    --change 'ENTRYPOINT ["/bin/ollama"]' \
    --change 'CMD ["serve"]' \
    --change 'ENV PATH=/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin' \
    --change 'ENV OLLAMA_HOST=0.0.0.0:11434' \
    --change 'EXPOSE 11434' \
    --change "LABEL dev.usa-wa.slim=$SLIM_LABEL" \
    - ollama/ollama:cpu-slim ||
    die "export|import failed. The IMAGE is untouched and still tagged $IMAGE, so nothing needs rolling back; the running container has had its accelerator runtimes stripped, which costs this GPU-less host nothing and is undone by \`docker rm -f $CONTAINER\` plus a re-run" 2

# ── swap ──────────────────────────────────────────────────────────────────────
#
# The fat image is the rollback, so it is removed LAST — only once the
# replacement is serving the model. Between here and the verification below the
# host holds both, which is what MIN_FREE_BYTES reserves for.

OLD_ID=$("$DOCKER" image inspect "$IMAGE" --format '{{.Id}}' 2>/dev/null)

"$DOCKER" stop "$CONTAINER" >/dev/null 2>&1
"$DOCKER" rm "$CONTAINER" >/dev/null 2>&1
"$DOCKER" tag ollama/ollama:cpu-slim "$IMAGE" || die "could not retag the slim image" 2

# Exactly SocratiCode's startOllama(): a plain `docker run`, no compose labels.
"$DOCKER" run -d \
    --name "$CONTAINER" \
    -p "$PORT:11434" \
    -v "$VOLUME:/root/.ollama" \
    --restart unless-stopped \
    "$IMAGE" >/dev/null || die "could not start the replacement container" 2

# ── verify before discarding the rollback ─────────────────────────────────────

for _ in $(seq 1 45); do
    "$CURL" -sf -m 5 "http://localhost:$PORT/api/tags" >/dev/null 2>&1 && break
    sleep 2
done

rollback_hint() {
    # The retained image is the rollback, so the failure that needs it is the
    # one place the command belongs. The tag has already moved to the slim
    # build by here, which is exactly why "the old image is still there" is not
    # on its own actionable.
    #
    # With no id in hand, say so. Interpolating an empty OLD_ID printed
    # `docker tag  ollama/ollama:latest` — a command missing an argument, which
    # is worse than no command at all because it still looks pasteable.
    if [ -z "$OLD_ID" ]; then
        printf 'the previous image id could not be read; find it with `%s images` (the untagged ollama entry) and retag it to %s before retrying\n' \
            "$DOCKER" "$IMAGE" >&2
        return
    fi
    printf 'roll back with:\n  %s tag %s %s\n  %s rm -f %s\n  %s run -d --name %s -p %s:11434 -v %s:/root/.ollama --restart unless-stopped %s\n' \
        "$DOCKER" "$OLD_ID" "$IMAGE" \
        "$DOCKER" "$CONTAINER" \
        "$DOCKER" "$CONTAINER" "$PORT" "$VOLUME" "$IMAGE" >&2
}

if ! "$DOCKER" exec "$CONTAINER" ollama list 2>/dev/null | grep -q "$MODEL"; then
    rollback_hint
    die "$MODEL is not present in the replacement container — the original image ($OLD_ID) is retained"
fi

dims=$("$CURL" -s -m 120 "http://localhost:$PORT/api/embed" \
    -d "{\"model\":\"$MODEL\",\"input\":\"slim-ollama-image verification\"}" 2>/dev/null |
    python3 -c 'import json,sys; print(len(json.load(sys.stdin)["embeddings"][0]))' 2>/dev/null)

if [ "${dims:-0}" != "$EXPECTED_DIMS" ]; then
    rollback_hint
    die "embedding check failed (got ${dims:-none} dimensions, expected $EXPECTED_DIMS) — the original image ($OLD_ID) is retained"
fi

echo "slim-ollama-image: verified — $MODEL serving $dims dimensions"

# ── the reclaim ───────────────────────────────────────────────────────────────

if [ -n "$OLD_ID" ]; then
    "$DOCKER" image rm "$OLD_ID" >/dev/null 2>&1 ||
        echo "slim-ollama-image: could not remove the old image $OLD_ID; remove it by hand to reclaim the space" >&2
fi

echo "slim-ollama-image: done — $IMAGE is now the CPU-only build"
