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

def vb_encode_number(number: int) -> list[int]:
    if number < 0:
        raise ValueError("Variable-byte encoding requires non-negative integers")

    bytes_out = [number % 128]
    number //= 128

    while number > 0:
        bytes_out.insert(0, number % 128)
        number //= 128

    bytes_out[-1] += 128
    return bytes_out

def vb_encode(numbers: list[int]) -> bytes:
    encoded = []

    for number in numbers:
        encoded.extend(vb_encode_number(number))

    return bytes(encoded)

def vb_decode(data: bytes) -> list[int]:
    numbers = []
    current = 0

    for byte in data:
        if byte >= 128:
            current = current * 128 + (byte - 128)
            numbers.append(current)
            current = 0
        else:
            current = current * 128 + byte

    if current != 0:
        raise ValueError("Incomplete variable-byte sequence")

    return numbers

def rid_to_int(rid) -> int:
    return rid.page_id * 65536 + rid.slot_id


def int_to_rid(value: int):
    from dbmsi.storage import RID

    page_id = value // 65536
    slot_id = value % 65536

    return RID(page_id, slot_id)



def compress_postings(postings) -> bytes:
    posting_ids = sorted(rid_to_int(rid) for rid in postings)
    gaps = delta_encode(posting_ids)
    return vb_encode(gaps)

def decompress_postings(data: bytes):
    gaps = vb_decode(data)
    posting_ids = delta_decode(gaps)
    return [int_to_rid(value) for value in posting_ids]