"""
Download the connectome graph.npz if it doesn't exist yet.

Sources (in order of preference):
1. The pre-built graph.npz from the project build directory
2. Download from Google Cloud Storage (slow, ~1.1GB raw data -> ~200MB npz)
3. Generate a minimal fallback graph for testing

The raw feather files are too large for Docker build-time download on
Railway's free tier, so we download at runtime instead.
"""
import os
import subprocess
import sys
import time
import numpy as np
from pathlib import Path

ROOT = Path(__file__).parent
DATA = ROOT / "data"
BUILD = ROOT / "build"
GRAPH = BUILD / "graph.npz"

# Minimal fallback: a tiny graph with just a few neurons for testing
MINIMAL_BODY_COUNT = 50

def download_feather(url, path):
    """Download a feather file with curl (avoids SSL issues on some systems)."""
    print(f"Downloading {path.name} from {url[:70]}...")
    path.parent.mkdir(parents=True, exist_ok=True)
    try:
        # Use curl for reliable download (bypasses Python SSL issues)
        result = subprocess.run(
            ["curl", "-L", "--progress-bar", "-o", str(path), url],
            capture_output=True, text=True, timeout=600
        )
        if result.returncode != 0:
            print(f"  curl failed: {result.stderr[:200]}")
            return False
        # Check file was created and has content
        if path.exists() and path.stat().st_size > 0:
            size_mb = path.stat().st_size // (1024*1024)
            print(f"  Downloaded {size_mb}MB")
            return True
        print(f"  File empty or missing after download")
        return False
    except subprocess.TimeoutExpired:
        print(f"  Download timeout (600s)")
        return False
    except Exception as e:
        print(f"  Download failed: {e}")
        return False


def fetch_connectome():
    """Download raw feather files and build graph.npz."""
    print("Fetching FlyEM connectome data from Google Cloud Storage...")

    # These are the correct filenames from the FlyEM project (flat-connectome/ subdir)
    # Source: GitHub Issue #2 - original README path was wrong, correct path is connectome-data/flat-connectome/
    BASE = "https://storage.googleapis.com/flyem-male-cns/v1.0/connectome-data/flat-connectome"

    weights_url = f"{BASE}/connectome-weights-male-cns-v1.0-minconf-0.5.feather"
    annot_url = f"{BASE}/body-annotations-male-cns-v1.0-minconf-0.5.feather"
    nt_url = f"{BASE}/body-neurotransmitters-male-cns-v1.0.feather"

    weights_path = DATA / "connectome-weights.feather"
    annot_path = DATA / "body-annotations.feather"
    nt_path = DATA / "body-neurotransmitters.feather"

    success = True
    for i, (url, path) in enumerate([
        (weights_url, weights_path),
        (annot_url, annot_path),
        (nt_url, nt_path)
    ]):
        print(f"  [{i+1}/3] Downloading {path.name} ({url[-60:]})...", flush=True)
        if not download_feather(url, path):
            print(f"  [{i+1}/3] FAILED: {path.name}", flush=True)
            success = False
            break
        else:
            print(f"  [{i+1}/3] OK: {path.name} ({path.stat().st_size // (1024*1024)}MB)", flush=True)

    if not success:
        print("Connectome download failed. Will try fallback.")
        return False

    print("Building graph.npz from downloaded data...", flush=True)
    try:
        result = subprocess.run(
            [sys.executable, "-u", str(ROOT / "build_graph.py")],
            capture_output=True, text=True, timeout=600
        )
        print(f"build_graph.py rc={result.returncode} stdout_len={len(result.stdout)} stderr_len={len(result.stderr)}", flush=True)
        print(result.stdout, end="", flush=True)
        if result.returncode != 0:
            print(f"build_graph.py failed (rc={result.returncode})", flush=True)
            print(f"  stderr: {result.stderr[:500]}", flush=True)
            return False
    except subprocess.TimeoutExpired:
        print("build_graph.py TIMED OUT after 600s", flush=True)
        return False
    except Exception as e:
        print(f"build_graph.py FAILED: {e!r}", flush=True)
        return False

    print(f"Graph built: {GRAPH} ({GRAPH.stat().st_size // (1024*1024)}MB, {len(np.load(str(GRAPH), allow_pickle=True)['bodies']):,} neurons)", flush=True)
    return True


def create_minimal_fallback():
    """Create a tiny test graph so the app can start without real data."""
    print("Creating minimal fallback graph (50 neurons)...")
    import numpy as np

    BUILD.mkdir(parents=True, exist_ok=True)

    n = MINIMAL_BODY_COUNT
    # Create a sparse random graph
    rng = np.random.default_rng(42)
    W = rng.standard_normal((n, n)).astype(np.float32) * 0.1
    W = np.triu(W)  # Make upper triangular (no self-loops)

    # Pack into npz format (simplified)
    coo = scipy_sparse_csr_to_npz_format(W)

    np.savez_compressed(
        GRAPH,
        data=coo["data"],
        indices=coo["indices"],
        indptr=coo["indptr"],
        shape=np.array([n, n]),
        bodies=np.arange(n),
        sign=np.zeros(n, dtype=np.float32),
        types=np.array(["test"] * n),
        superclass=np.array(["test"] * n),
        subclass=np.array(["test"] * n),
        receptor=np.array([""] * n),
        fru=np.array([""] * n),
        nt=np.array(["unknown"] * n),
    )
    print(f"Fallback graph created: {GRAPH}")
    return True


def scipy_sparse_csr_to_npz_format(W):
    """Convert a dense array to sparse CSR format compatible with FlyBrain."""
    from scipy import sparse
    W_sparse = sparse.csr_matrix(W)
    W_sparse.sum_duplicates()
    return {
        "data": W_sparse.data,
        "indices": W_sparse.indices,
        "indptr": W_sparse.indptr,
    }


if __name__ == "__main__":
    DATA.mkdir(parents=True, exist_ok=True)
    BUILD.mkdir(parents=True, exist_ok=True)

    # Check if existing graph is a real connectome (165k+ neurons) or a stale fallback
    if GRAPH.exists():
        try:
            z = np.load(str(GRAPH), allow_pickle=True)
            body_count = len(z["bodies"])
            if body_count >= 160000:
                size_mb = GRAPH.stat().st_size // (1024 * 1024)
                print(f"graph.npz exists ({body_count:,} neurons, {size_mb}MB), skipping")
                sys.exit(0)
            print(f"Found stale fallback graph ({body_count} neurons), rebuilding...")
            GRAPH.unlink()
        except Exception as e:
            print(f"Existing graph.npz is invalid ({e}), rebuilding...")
            if GRAPH.exists():
                GRAPH.unlink()

    # Try to fetch real data first
    if fetch_connectome():
        sys.exit(0)

    # Fallback
    print("Using minimal fallback graph for testing.")
    create_minimal_fallback()
