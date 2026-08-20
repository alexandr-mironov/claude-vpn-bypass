#!/usr/bin/env python3
"""
Local HTTPS CONNECT proxy that pins selected destinations (Anthropic by
default) to a physical interface via IP_BOUND_IF, so their traffic egresses
your home ISP instead of the corp tunnel — beating Cisco's every-3s route
re-pinning, which lives in the routing table this proxy bypasses entirely.

TLS stays end-to-end: this proxy only splices bytes for CONNECT, it never
sees plaintext and never presents a certificate, so Claude Code validates the
real Anthropic cert (no MITM). Non-matching hosts are connected normally
(through whatever the routing table says, i.e. the tunnel), so corp traffic is
unaffected.

Point Claude Code at it:   export HTTPS_PROXY=http://127.0.0.1:8888
                           export HTTP_PROXY=http://127.0.0.1:8888
"""
import argparse
import ipaddress
import select
import socket
import subprocess
import sys
import threading
import time

# macOS <netinet/in.h>
IP_BOUND_IF = 25
IPV6_BOUND_IF = 125


def log(*a):
    print(*a, file=sys.stderr, flush=True)


_if_cache = {"name": None, "t": 0.0}


def physical_iface():
    """The non-tunnel interface with a default route, re-detected (cached 5 s)
    so switching Wi-Fi <-> Ethernet needs no restart."""
    now = time.monotonic()
    if _if_cache["name"] and now - _if_cache["t"] < 5:
        return _if_cache["name"]
    name = "en0"
    try:
        out = subprocess.run(
            ["netstat", "-rn", "-f", "inet"],
            capture_output=True, text=True, timeout=3,
        ).stdout
        for line in out.splitlines():
            f = line.split()
            if len(f) >= 4 and f[0] == "default" and f[-1].startswith("en"):
                name = f[-1]
                break
    except Exception:
        pass
    _if_cache.update(name=name, t=now)
    return name


class Proxy:
    def __init__(self, iface, bypass_suffixes, bypass_cidrs):
        self.iface = iface  # explicit name, or "auto" to detect dynamically
        self.bypass_suffixes = [s.lower().lstrip(".") for s in bypass_suffixes]
        self.bypass_cidrs = [ipaddress.ip_network(c) for c in bypass_cidrs]

    def bind_iface(self):
        return physical_iface() if self.iface == "auto" else self.iface

    def host_matches(self, host):
        h = host.lower().rstrip(".")
        for suf in self.bypass_suffixes:
            if h == suf or h.endswith("." + suf):
                return True
        return False

    def ip_matches(self, ip):
        try:
            addr = ipaddress.ip_address(ip)
        except ValueError:
            return False
        return any(addr in net for net in self.bypass_cidrs)

    def should_bypass(self, host, ip):
        return self.host_matches(host) or self.ip_matches(ip)

    def open_upstream(self, host, port):
        # Resolve, then decide per-address whether to pin to the physical NIC.
        infos = socket.getaddrinfo(host, port, proto=socket.IPPROTO_TCP)
        last = None
        for family, socktype, proto, _cn, sa in infos:
            ip = sa[0]
            bypass = self.should_bypass(host, ip)
            s = socket.socket(family, socktype, proto)
            try:
                if bypass:
                    ifidx = socket.if_nametoindex(self.bind_iface())
                    if family == socket.AF_INET:
                        s.setsockopt(socket.IPPROTO_IP, IP_BOUND_IF, ifidx)
                    elif family == socket.AF_INET6:
                        s.setsockopt(socket.IPPROTO_IPV6, IPV6_BOUND_IF, ifidx)
                s.settimeout(15)
                s.connect(sa)
                s.settimeout(None)
                return s, ip, bypass
            except OSError as e:
                last = e
                s.close()
        raise last or OSError("no address")

    def handle(self, client):
        try:
            req = b""
            client.settimeout(15)
            while b"\r\n\r\n" not in req:
                chunk = client.recv(4096)
                if not chunk:
                    return
                req += chunk
                if len(req) > 65536:
                    return
            client.settimeout(None)
            line = req.split(b"\r\n", 1)[0].decode("latin-1")
            method, target = line.split(" ")[:2]
            if method.upper() != "CONNECT":
                client.sendall(b"HTTP/1.1 405 Only CONNECT supported\r\n\r\n")
                return
            host, _, port_s = target.rpartition(":")
            port = int(port_s or "443")
            try:
                up, ip, bypass = self.open_upstream(host, port)
            except OSError as e:
                client.sendall(b"HTTP/1.1 502 Bad Gateway\r\n\r\n")
                log(f"[x] {host}:{port} -> {e}")
                return
            log(f"[{'BYPASS en0' if bypass else 'direct    '}] {host}:{port} ({ip})")
            client.sendall(b"HTTP/1.1 200 Connection established\r\n\r\n")
            self.splice(client, up)
        except Exception as e:
            log(f"[!] {e}")
        finally:
            client.close()

    @staticmethod
    def splice(a, b):
        socks = [a, b]
        try:
            while True:
                r, _, x = select.select(socks, [], socks, 300)
                if x or not r:
                    break
                for s in r:
                    data = s.recv(65536)
                    if not data:
                        return
                    (b if s is a else a).sendall(data)
        finally:
            for s in (a, b):
                try:
                    s.close()
                except OSError:
                    pass

    def serve(self, host, port):
        srv = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        srv.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        srv.bind((host, port))
        srv.listen(128)
        log(f">> proxy on {host}:{port}, pinning bypass hosts to iface '{self.iface}' (now: {self.bind_iface()})")
        log(f">> bypass suffixes: {self.bypass_suffixes}")
        log(f">> bypass cidrs:    {[str(c) for c in self.bypass_cidrs]}")
        while True:
            cli, _ = srv.accept()
            threading.Thread(target=self.handle, args=(cli,), daemon=True).start()


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--listen", default="127.0.0.1:8888")
    ap.add_argument("--iface", default="auto",
                    help="physical interface to pin bypass traffic to, or 'auto'")
    ap.add_argument("--bypass-host", action="append",
                    default=["anthropic.com", "claude.ai"])
    ap.add_argument("--bypass-cidr", action="append",
                    default=["160.79.104.0/23"])
    args = ap.parse_args()
    lhost, _, lport = args.listen.rpartition(":")
    Proxy(args.iface, args.bypass_host, args.bypass_cidr).serve(lhost or "127.0.0.1", int(lport))


if __name__ == "__main__":
    main()
