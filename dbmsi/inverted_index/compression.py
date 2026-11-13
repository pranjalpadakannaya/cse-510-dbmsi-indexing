def delta_encode(ids: list[int]) -> list[int]:
    if not ids:
        return []

    gaps = [ids[0]]

    for i in range(1, len(ids)):
        gaps.append(ids[i] - ids[i - 1])

    return gaps


def delta_decode(gaps: list[int]) -> list[int]:
    if not gaps:
        return []

    ids = [gaps[0]]

    for gap in gaps[1:]:
        ids.append(ids[-1] + gap)

    return ids