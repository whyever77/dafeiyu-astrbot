#!/usr/bin/env python3
"""Forward a private Docker bridge address to the local SSH reverse tunnel.

Only the configured AstrBot container (and explicitly allowed IPs) may use the
gateway. The companion MCP performs account writes, so the proxy must not be
reachable by every container on the Docker bridge.
"""
import argparse
import asyncio
import ipaddress
import subprocess


def _container_ips(name):
    """Resolve a running container's bridge IPs; empty on any Docker error."""
    if not name:
        return set()
    try:
        output = subprocess.check_output(
            ["docker", "inspect", "-f",
             "{{range .NetworkSettings.Networks}}{{.IPAddress}} {{end}}", name],
            text=True, timeout=5, stderr=subprocess.DEVNULL,
        )
    except (OSError, subprocess.SubprocessError):
        return set()
    return {ip for ip in output.split() if ip}


def _peer_ip(peer):
    if not peer:
        return ""
    value = str(peer[0])
    if value.startswith("::ffff:"):
        value = value[7:]
    return value


def _peer_allowed(peer_ip, allowed):
    return bool(peer_ip) and peer_ip in allowed


async def pipe(reader, writer):
    try:
        while data := await reader.read(65536):
            writer.write(data)
            await writer.drain()
    finally:
        writer.close()
        try:
            await writer.wait_closed()
        except (OSError, ConnectionError):
            pass


async def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--listen-host", default="172.18.0.1")
    parser.add_argument("--port", type=int, default=8011)
    parser.add_argument("--allow-container", default="astrbot",
                        help="Docker container name allowed to connect; empty disables resolution")
    parser.add_argument("--allow-ip", action="append", default=[],
                        help="Additional peer IP allowed to connect (repeatable)")
    parser.add_argument("--refresh-seconds", type=float, default=15)
    args = parser.parse_args()
    host = ipaddress.ip_address(args.listen_host)
    if not host.is_private or host.is_loopback or host.is_unspecified or host.is_multicast:
        raise SystemExit("listen-host must be a specific private Docker bridge address")
    if not 1024 <= args.port <= 65535:
        raise SystemExit("port must be between 1024 and 65535")

    static = set(args.allow_ip)
    allowed = static | _container_ips(args.allow_container)
    if not allowed:
        raise SystemExit("no allowed peer: pass --allow-ip or start the --allow-container container")

    async def refresh():
        nonlocal allowed
        while True:
            await asyncio.sleep(max(5, args.refresh_seconds))
            resolved = _container_ips(args.allow_container)
            if resolved:
                allowed = static | resolved

    async def handle(reader, writer):
        peer = _peer_ip(writer.get_extra_info("peername"))
        if not _peer_allowed(peer, allowed):
            print(f"rejected peer {peer or 'unknown'}", flush=True)
            writer.close()
            try:
                await writer.wait_closed()
            except OSError:
                pass
            return
        try:
            remote_reader, remote_writer = await asyncio.wait_for(
                asyncio.open_connection("127.0.0.1", args.port), timeout=5)
            tasks = [asyncio.create_task(pipe(reader, remote_writer)),
                     asyncio.create_task(pipe(remote_reader, writer))]
            try:
                await asyncio.wait(tasks, return_when=asyncio.FIRST_COMPLETED)
            finally:
                for task in tasks:
                    task.cancel()
                await asyncio.gather(*tasks, return_exceptions=True)
        except (OSError, TimeoutError):
            writer.close()
            try:
                await writer.wait_closed()
            except OSError:
                pass

    refresher = asyncio.create_task(refresh())
    server = await asyncio.start_server(handle, args.listen_host, args.port, limit=131072)
    print(f"Bilibili MCP proxy {args.listen_host}:{args.port} -> loopback:{args.port}; "
          f"allowed={sorted(allowed)}", flush=True)
    try:
        async with server:
            await server.serve_forever()
    finally:
        refresher.cancel()
        await asyncio.gather(refresher, return_exceptions=True)


if __name__ == "__main__":
    asyncio.run(main())
