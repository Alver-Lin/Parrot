"""Validate screenshots without decoding or retaining expanded pixel buffers."""

import struct
import zlib

MAX_SCREENSHOT_BYTES = 25 * 1024 * 1024


def validate_png(data):
    if not data.startswith(b"\x89PNG\r\n\x1a\n"):
        raise ValueError("截图必须是 PNG 图片。")
    position = 8
    seen_header = False
    seen_pixels = False
    while position + 12 <= len(data):
        length = struct.unpack_from(">I", data, position)[0]
        end = position + 12 + length
        if end > len(data):
            break
        kind = data[position + 4:position + 8]
        payload = data[position + 8:position + 8 + length]
        checksum = struct.unpack_from(">I", data, position + 8 + length)[0]
        if zlib.crc32(kind + payload) & 0xFFFFFFFF != checksum:
            raise ValueError("截图文件已损坏，请重新截图。")
        if not seen_header:
            if kind != b"IHDR" or length != 13:
                break
            width, height = struct.unpack_from(">II", payload)
            if not width or not height or width * height > 100_000_000:
                raise ValueError("截图尺寸过大或无效，请缩小截图范围。")
            seen_header = True
        if kind == b"IDAT" and length:
            seen_pixels = True
        if kind == b"IEND" and length == 0 and end == len(data) and seen_pixels:
            return
        position = end
    raise ValueError("截图文件不完整，请重新截图。")
