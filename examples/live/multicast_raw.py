#!/usr/bin/env python3
import argparse
import socket
import struct
import time

def hexdump(b: bytes) -> str:
    width = 16
    lines = []
    for i in range(0, len(b), width):
        chunk = b[i:i+width]
        hexpart = " ".join(f"{x:02x}" for x in chunk)
        asciipart = "".join(chr(x) if 32 <= x < 127 else "." for x in chunk)
        lines.append(f"{i:04x}  {hexpart:<{width*3}}  {asciipart}")
    return "\n".join(lines)

def main():
    ap = argparse.ArgumentParser(description="Simple IPv4 multicast receiver")
    ap.add_argument("--group", default="239.255.90.67", help="Multicast group (default: 239.255.90.67)")
    ap.add_argument("--port", type=int, default=6969, help="UDP port (default: 6969)")
    ap.add_argument("--iface-ip", default="0.0.0.0",
                    help="Local interface IP to join on (e.g., your en0 address). "
                         "Use 0.0.0.0 for system default.")
    ap.add_argument("--buf", type=int, default=262144, help="Receive buffer size (bytes)")
    ap.add_argument("--hex", action="store_true", help="Hexdump payloads")
    args = ap.parse_args()

    group = socket.inet_aton(args.group)
    iface = socket.inet_aton(args.iface_ip)

    # UDP socket
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM, socket.IPPROTO_UDP)

    # Helpful on macOS/BSD when multiple listeners exist
    try:
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEPORT, 1)
    except OSError:
        pass  # Not available on all platforms
    sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)

    # Bigger RX buffer helps with bursts
    try:
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_RCVBUF, args.buf)
    except OSError:
        pass

    # Bind to port on all interfaces; do NOT bind to the group address
    sock.bind(("", args.port))

    # Join the multicast group on the specified interface IP
    # struct ip_mreq: { struct in_addr imr_multiaddr; struct in_addr imr_interface; }
    mreq = struct.pack("=4s4s", group, iface)
    sock.setsockopt(socket.IPPROTO_IP, socket.IP_ADD_MEMBERSHIP, mreq)

    # Optional: disable loopback if you don’t want to see your own sends
    # sock.setsockopt(socket.IPPROTO_IP, socket.IP_MULTICAST_LOOP, 0)

    print(f"Joined {args.group}:{args.port} on interface {args.iface_ip}")
    print("Waiting for packets… Ctrl+C to stop.")

    try:
        while True:
            data, addr = sock.recvfrom(65535)
            ts = time.strftime("%Y-%m-%d %H:%M:%S")
            print(f"[{ts}] from {addr[0]}:{addr[1]}  len={len(data)}")
            if args.hex:
                print(hexdump(data))
    except KeyboardInterrupt:
        pass
    finally:
        try:
            sock.setsockopt(socket.IPPROTO_IP, socket.IP_DROP_MEMBERSHIP, mreq)
        except OSError:
            pass
        sock.close()

if __name__ == "__main__":
    main()
