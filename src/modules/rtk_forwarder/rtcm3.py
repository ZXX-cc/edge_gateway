"""
RTCM3 (Radio Technical Commission for Maritime Services, version 3) frame
detection, parsing, and validation.

RTCM3 message structure (as used by u-blox ZED-F9P):
┌──────────┬──────────┬───────────────────────┬─────────────────┬─────────────┐
│ Preamble │ Reserved │ Message Length (10bit) │ Variable Payload │ CRC-24Q     │
│  0xD3    │  6 bits  │                       │  (0-1023 bytes)  │  (3 bytes)   │
│ (1 byte) │          │                       │                  │             │
└──────────┴──────────┴───────────────────────┴─────────────────┴─────────────┘
  Byte 0     Byte 1-2                          Bytes 3..(N-3)     Last 3 bytes

Total frame length = 3 (header) + payload_length + 3 (CRC) = payload_length + 6

Reference:
  RTCM Standard 10403.3, "Differential GNSS Services – Version 3"
  QGroundControl RTCMMavlink implementation
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from enum import Enum, auto
from typing import Optional

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# CRC-24Q (Qualcomm) — used by RTCM3
# ---------------------------------------------------------------------------

CRC24_POLY = 0x1864CFB  # RTCM 10403.3 CRC-24Q generator polynomial
CRC24_INIT = 0x000000   # Initial value

# Precomputed CRC-24Q lookup table for faster calculation
_CRC24_TABLE: list[int] = []


def _build_crc24_table() -> None:
    """Build the CRC-24Q lookup table (called once at module load)."""
    global _CRC24_TABLE
    if _CRC24_TABLE:
        return
    for i in range(256):
        crc = i << 16
        for _ in range(8):
            crc <<= 1
            if crc & 0x1000000:
                crc ^= CRC24_POLY
        _CRC24_TABLE.append(crc & 0xFFFFFF)


_build_crc24_table()


def crc24q(data: bytes, offset: int = 0, length: Optional[int] = None) -> int:
    """
    Calculate CRC-24Q checksum over bytes.

    Uses precomputed lookup table for speed.

    Args:
        data: Input bytes.
        offset: Start offset in data.
        length: Number of bytes to process (None = remaining).

    Returns:
        24-bit CRC value (0x000000 – 0xFFFFFF).
    """
    end = len(data) if length is None else offset + length
    crc = CRC24_INIT
    for i in range(offset, end):
        idx = ((crc >> 16) ^ data[i]) & 0xFF
        crc = ((crc << 8) & 0xFFFFFF) ^ _CRC24_TABLE[idx]
    return crc


def crc24q_verify(frame: bytes) -> bool:
    """
    Verify CRC-24Q of a complete RTCM3 frame.

    The CRC is computed over the entire frame including the 3-byte CRC field,
    and the result must be zero for a valid frame.

    Args:
        frame: Complete RTCM3 frame bytes (preamble + header + payload + CRC).

    Returns:
        True if CRC check passes.
    """
    return crc24q(frame) == 0


# ---------------------------------------------------------------------------
# RTCM3 Frame parsing
# ---------------------------------------------------------------------------

RTCM3_PREAMBLE = 0xD3

# RTCM3 header size: 1 (preamble) + 2 (reserved + message length)
RTCM3_HEADER_SIZE = 3
RTCM3_CRC_SIZE = 3
RTCM3_MIN_FRAME_SIZE = RTCM3_HEADER_SIZE + RTCM3_CRC_SIZE  # 6 bytes minimum
RTCM3_MAX_PAYLOAD = 1023  # 10-bit length field
RTCM3_MAX_FRAME_SIZE = RTCM3_HEADER_SIZE + RTCM3_MAX_PAYLOAD + RTCM3_CRC_SIZE


class FrameStatus(Enum):
    """RTCM3 frame decoding status."""
    INCOMPLETE = auto()   # Need more bytes
    INVALID_PREAMBLE = auto()
    INVALID_LENGTH = auto()
    CRC_MISMATCH = auto()
    OK = auto()


@dataclass
class Rtcm3Frame:
    """A parsed RTCM3 frame."""
    raw: bytes              # Complete raw frame (preamble to CRC inclusive)
    payload: bytes          # Payload only (without header and CRC)
    message_type: int       # Message type number (upper 12 bits of payload[0:2]? actually first 12 bits)
    payload_length: int     # Payload length in bytes

    @property
    def total_length(self) -> int:
        """Total frame length including header and CRC."""
        return len(self.raw)

    def __repr__(self) -> str:
        return (
            f"Rtcm3Frame(type={self.message_type}, "
            f"payload={self.payload_length}B, "
            f"total={self.total_length}B)"
        )


# ---------------------------------------------------------------------------
# RTCM3 Stream Parser
# ---------------------------------------------------------------------------

class Rtcm3Parser:
    """
    Incremental RTCM3 frame parser for byte streams.

    Maintains an internal buffer, synchronizes on 0xD3 preamble,
    validates CRC-24Q, and emits complete frames.

    Usage::

        parser = Rtcm3Parser()
        # Feed bytes as they arrive...
        for frame in parser.feed(data_chunk):
            process(frame)
    """

    def __init__(self, validate_crc: bool = True):
        """
        Args:
            validate_crc: If True (default), only return frames with valid CRC.
        """
        self._validate_crc = validate_crc
        self._buffer = bytearray()
        self._sync_state = "SEARCH"    # SEARCH | HEADER | PAYLOAD

        # Statistics
        self.frames_found: int = 0
        self.frames_valid: int = 0
        self.frames_crc_fail: int = 0
        self.bytes_received: int = 0
        self.bytes_discarded: int = 0  # Bytes discarded during sync search

    def reset(self) -> None:
        """Reset parser state (e.g. after reconnection)."""
        self._buffer.clear()
        self._sync_state = "SEARCH"

    def feed(self, data: bytes) -> list[Rtcm3Frame]:
        """
        Feed raw bytes from the stream. Returns list of complete, valid frames.

        Args:
            data: Raw bytes from the TCP stream.

        Returns:
            List of successfully parsed and CRC-validated Rtcm3Frame objects.
        """
        self.bytes_received += len(data)
        self._buffer.extend(data)
        frames: list[Rtcm3Frame] = []

        while True:
            frame = self._try_extract_frame()
            if frame is None:
                break
            frames.append(frame)

        # Prevent unbounded buffer growth
        if len(self._buffer) > RTCM3_MAX_FRAME_SIZE * 4:
            logger.warning(
                "Parser buffer oversized (%d bytes), flushing to find sync",
                len(self._buffer),
            )
            self._buffer.clear()
            self._sync_state = "SEARCH"

        return frames

    def _try_extract_frame(self) -> Optional[Rtcm3Frame]:
        """Try to extract one complete frame from the buffer. Returns None if incomplete."""
        # --- Step 1: Synchronize on preamble ---
        if self._sync_state == "SEARCH":
            idx = self._buffer.find(RTCM3_PREAMBLE)
            if idx < 0:
                # No preamble found — discard all
                self.bytes_discarded += len(self._buffer)
                self._buffer.clear()
                return None
            if idx > 0:
                # Discard bytes before the preamble
                self.bytes_discarded += idx
                del self._buffer[:idx]
            self._sync_state = "HEADER"

        # --- Step 2: Read header (3 bytes) ---
        if self._sync_state == "HEADER":
            if len(self._buffer) < RTCM3_HEADER_SIZE:
                return None

            # Parse the 10-bit message length from bytes 1-2
            # Byte 1: upper 6 bits are reserved (must be 0), lower 2 bits are bits 9:8 of length
            # Byte 2: bits 7:0 of length
            reserved = (self._buffer[1] >> 2) & 0x3F
            if reserved != 0:
                # Invalid reserved bits — out of sync
                logger.debug(
                    "Reserved bits non-zero (0x%02X), re-synchronizing", reserved
                )
                self.bytes_discarded += 1
                del self._buffer[:1]  # Discard bad preamble
                self._sync_state = "SEARCH"
                return None

            payload_len = ((self._buffer[1] & 0x03) << 8) | self._buffer[2]
            if payload_len > RTCM3_MAX_PAYLOAD:
                logger.debug(
                    "Invalid payload length %d, re-synchronizing", payload_len
                )
                self.bytes_discarded += 1
                del self._buffer[:1]
                self._sync_state = "SEARCH"
                return None

            self._expected_payload_len = payload_len
            self._sync_state = "PAYLOAD"

        # --- Step 3: Wait for complete frame ---
        total_needed = RTCM3_HEADER_SIZE + self._expected_payload_len + RTCM3_CRC_SIZE
        if len(self._buffer) < total_needed:
            return None

        # --- Step 4: Extract and validate ---
        frame_raw = bytes(self._buffer[:total_needed])

        if self._validate_crc:
            if not crc24q_verify(frame_raw):
                self.frames_crc_fail += 1
                logger.debug("CRC-24Q mismatch, discarding frame")
                # Discard the bad preamble and re-sync
                self.bytes_discarded += 1
                del self._buffer[:1]
                self._sync_state = "SEARCH"
                return None

        # Success
        self.frames_found += 1
        self.frames_valid += 1

        # Extract message type from first 12 bits of payload
        # (or from the first field of the message header)
        payload = frame_raw[RTCM3_HEADER_SIZE:-RTCM3_CRC_SIZE]
        msg_type = 0
        if len(payload) >= 2:
            msg_type = ((payload[0] << 4) | (payload[1] >> 4)) & 0xFFF

        frame = Rtcm3Frame(
            raw=frame_raw,
            payload=payload,
            message_type=msg_type,
            payload_length=self._expected_payload_len,
        )

        # Remove frame from buffer
        del self._buffer[:total_needed]
        self._sync_state = "SEARCH"
        return frame


# ---------------------------------------------------------------------------
# Well-known RTCM3 message types for ZED-F9P
# ---------------------------------------------------------------------------

# Common RTCM3 message types output by u-blox ZED-F9P in base station mode:
KNOWN_MESSAGE_TYPES = {
    1005: "Stationary RTK Reference Station ARP",
    1006: "Stationary RTK Reference Station ARP with antenna height",
    1074: "GPS MSM4 (Full PRs & Phases + CNR)",
    1075: "GPS MSM5 (Full PRs & Phases + CNR + Doppler)",
    1077: "GPS MSM7 (Full PRs & Phases + CNR + Doppler)",
    1084: "GLONASS MSM4",
    1085: "GLONASS MSM5",
    1087: "GLONASS MSM7",
    1094: "Galileo MSM4",
    1095: "Galileo MSM5",
    1097: "Galileo MSM7",
    1114: "QZSS MSM4",
    1115: "QZSS MSM5",
    1117: "QZSS MSM7",
    1124: "BeiDou MSM4",
    1125: "BeiDou MSM5",
    1127: "BeiDou MSM7",
    1230: "GLONASS code-phase biases",
    4072: "Additional RTCM reference station information (u-blox proprietary)",
}


def describe_message_type(msg_type: int) -> str:
    """Return human-readable description of an RTCM3 message type."""
    return KNOWN_MESSAGE_TYPES.get(msg_type, f"Unknown ({msg_type})")
