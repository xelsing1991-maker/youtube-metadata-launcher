from typing import Callable, Iterable, List, Sequence, Tuple


def normalize_text(s: str) -> str:
    """Lowercase, strip, collapse spaces, remove trivial punctuation padding."""
    if s is None:
        return ""
    s = s.strip().lower()
    # collapse multiple spaces
    s = " ".join(s.split())
    return s


def levenshtein_distance(a: str, b: str) -> int:
    """Compute Levenshtein edit distance (insertion, deletion, substitution).

    Pure Python, O(len(a)*len(b)). Optimized for short tags/phrases.
    """
    if a == b:
        return 0
    if not a:
        return len(b)
    if not b:
        return len(a)

    # Ensure a is the shorter string to reduce memory
    if len(a) > len(b):
        a, b = b, a

    previous_row = list(range(len(b) + 1))
    for i, ca in enumerate(a, start=1):
        current_row = [i]
        for j, cb in enumerate(b, start=1):
            insertions = previous_row[j] + 1
            deletions = current_row[j - 1] + 1
            substitutions = previous_row[j - 1] + (ca != cb)
            current_row.append(min(insertions, deletions, substitutions))
        previous_row = current_row
    return previous_row[-1]


def similarity(a: str, b: str) -> float:
    """Return normalized similarity in [0,1] based on Levenshtein distance."""
    na, nb = normalize_text(a), normalize_text(b)
    if not na and not nb:
        return 1.0
    if not na or not nb:
        return 0.0
    dist = levenshtein_distance(na, nb)
    denom = max(len(na), len(nb))
    return 1.0 - (dist / denom if denom else 0.0)


def dedupe_by_similarity(
    items: Sequence[str],
    threshold: float = 0.85,
    key: Callable[[str], str] = normalize_text,
    keep: str = "first",
) -> List[str]:
    """Deduplicate a list of strings by Levenshtein similarity.

    - threshold: two items with similarity >= threshold are considered duplicates
    - key: normalization function used for comparison
    - keep: 'first' keeps first occurrence; 'shortest' keeps shortest variant
    """
    seen: List[str] = []
    canon: List[str] = []

    for item in items:
        if item is None:
            continue
        candidate = item.strip()
        if not candidate:
            continue
        if not seen:
            seen.append(key(candidate))
            canon.append(candidate)
            continue
        sim_hit_idx: int = -1
        best_sim: float = 0.0
        kcandidate = key(candidate)
        for idx, sk in enumerate(seen):
            s = similarity(kcandidate, sk)
            if s > best_sim:
                best_sim = s
                sim_hit_idx = idx
            if s >= threshold:
                sim_hit_idx = idx
                break

        if best_sim >= threshold and sim_hit_idx >= 0:
            if keep == "shortest":
                # Replace canonical with shortest textual variant
                if len(candidate) < len(canon[sim_hit_idx]):
                    canon[sim_hit_idx] = candidate
                    seen[sim_hit_idx] = key(candidate)
            # else keep 'first' -> do nothing
        else:
            seen.append(kcandidate)
            canon.append(candidate)
    return canon


def cluster_by_similarity(
    items: Sequence[str],
    threshold: float = 0.85,
    key: Callable[[str], str] = normalize_text,
) -> List[List[str]]:
    """Group items into similarity clusters based on Levenshtein similarity."""
    clusters: List[List[str]] = []
    reps: List[str] = []  # representative normalized key per cluster
    for item in items:
        if not item:
            continue
        k = key(item)
        if not reps:
            clusters.append([item])
            reps.append(k)
            continue
        placed = False
        for i, r in enumerate(reps):
            if similarity(k, r) >= threshold:
                clusters[i].append(item)
                placed = True
                break
        if not placed:
            clusters.append([item])
            reps.append(k)
    return clusters

