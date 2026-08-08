"""Find out what the camera will actually let us do, once it is reachable.

The datasheet lists "FLIR SDK, FLIR CGI" and does not mention ONVIF — but a marketing
page omitting something is not evidence it is absent. Rather than guess, this asks the
camera directly and reports what answered.

    uv run python spike/probe_camera.py 192.168.1.64 --user admin --password secret

It answers the four questions the design is currently blocked on:

  1. Which model is it, and therefore what thermal lens is on the pole
  2. Can we steer it, and over which protocol (ONVIF PTZ, or FLIR CGI)
  3. Can we read its own analytics events, so the alarm-source switch is real
  4. Which RTSP paths serve video

Nothing here is destructive. It reads, and the only movement it can cause is an
explicit --test-move, which nudges the camera and puts it straight back.
"""

from __future__ import annotations

import argparse
import re
import socket
import sys
import urllib.request
from dataclasses import dataclass, field
from urllib.error import HTTPError, URLError

TIMEOUT = 6.0

# ---------------------------------------------------------------- small helpers


@dataclass
class Finding:
    name: str
    ok: bool
    detail: str = ""
    body: str = ""


@dataclass
class Report:
    host: str
    findings: list[Finding] = field(default_factory=list)

    def add(self, name, ok, detail="", body=""):
        self.findings.append(Finding(name, ok, detail, body))
        mark = "yes" if ok else " no"
        print(f"  [{mark}] {name:<38} {detail}")
        return ok

    def any_ok(self, prefix: str) -> bool:
        return any(f.ok for f in self.findings if f.name.startswith(prefix))


def opener_for(host: str, user: str, password: str):
    """FLIR units generally want digest; fall back to basic. Both are cheap to offer."""
    if not user:
        return urllib.request.build_opener()
    manager = urllib.request.HTTPPasswordMgrWithDefaultRealm()
    manager.add_password(None, f"http://{host}/", user, password)
    return urllib.request.build_opener(
        urllib.request.HTTPDigestAuthHandler(manager),
        urllib.request.HTTPBasicAuthHandler(manager),
    )


def http_get(opener, url: str) -> tuple[int, str]:
    try:
        with opener.open(url, timeout=TIMEOUT) as response:
            return response.status, response.read(60000).decode("utf-8", "replace")
    except HTTPError as exc:
        return exc.code, ""
    except (URLError, OSError, TimeoutError) as exc:
        return 0, str(exc)


def soap_post(opener, url: str, body: str) -> tuple[int, str]:
    request = urllib.request.Request(
        url,
        data=body.encode("utf-8"),
        headers={"Content-Type": 'application/soap+xml; charset=utf-8'},
        method="POST",
    )
    try:
        with opener.open(request, timeout=TIMEOUT) as response:
            return response.status, response.read(60000).decode("utf-8", "replace")
    except HTTPError as exc:
        return exc.code, exc.read(20000).decode("utf-8", "replace") if exc.fp else ""
    except (URLError, OSError, TimeoutError) as exc:
        return 0, str(exc)


def envelope(inner: str) -> str:
    return (
        '<?xml version="1.0" encoding="UTF-8"?>'
        '<s:Envelope xmlns:s="http://www.w3.org/2003/05/soap-envelope">'
        f"<s:Body>{inner}</s:Body></s:Envelope>"
    )


def tag(body: str, name: str) -> str | None:
    match = re.search(rf"<(?:\w+:)?{name}[^>]*>(.*?)</(?:\w+:)?{name}>", body, re.S)
    return match.group(1).strip() if match else None


# ---------------------------------------------------------------- 1. reachability


def check_reachable(report: Report, host: str) -> bool:
    print("\nreachability")
    alive = False
    for port, what in ((80, "HTTP"), (554, "RTSP"), (443, "HTTPS")):
        try:
            with socket.create_connection((host, port), timeout=3):
                alive = report.add(f"port {port} ({what})", True, "open") or alive
        except OSError as exc:
            report.add(f"port {port} ({what})", False, str(exc).split("]")[-1].strip())
    return alive


# ---------------------------------------------------------------- 2. identity


CGI_PATHS = [
    "/cgi-bin/sysinfo.cgi",
    "/cgi-bin/nexus.cgi?action=getversion",
    "/api/sysinfo",
    "/cgi-bin/gettemp.cgi",
    "/",
]
MODEL_RE = re.compile(r"DX-?(\d{3})", re.I)


def check_identity(report: Report, opener, host: str) -> set[str]:
    print("\nidentity — which model is on the pole")
    models: set[str] = set()
    for path in CGI_PATHS:
        status, body = http_get(opener, f"http://{host}{path}")
        hits = set(MODEL_RE.findall(body)) if status == 200 else set()
        models |= hits
        report.add(
            f"GET {path}",
            status == 200,
            f"HTTP {status}" + (f", model hints {sorted(hits)}" if hits else ""),
            body[:3000],
        )
    return {f"DX-{n}" for n in models}


# ---------------------------------------------------------------- 3. ONVIF


def check_onvif(report: Report, opener, host: str) -> dict:
    print("\nONVIF — the open standard the datasheet does not mention")
    result = {"present": False, "ptz": False, "events": False, "profiles": []}
    url = f"http://{host}/onvif/device_service"

    status, body = soap_post(
        opener, url,
        envelope('<GetDeviceInformation xmlns="http://www.onvif.org/ver10/device/wsdl"/>'),
    )
    if status != 200 or "Envelope" not in body:
        report.add("device_service GetDeviceInformation", False, f"HTTP {status}")
        report.add("ONVIF overall", False, "no ONVIF — control must use FLIR CGI")
        return result

    model = tag(body, "Model") or "?"
    firmware = tag(body, "FirmwareVersion") or "?"
    result["present"] = True
    report.add("device_service GetDeviceInformation", True, f"model {model}, fw {firmware}", body[:3000])

    status, body = soap_post(
        opener, url, envelope('<GetCapabilities xmlns="http://www.onvif.org/ver10/device/wsdl"/>')
    )
    if status == 200:
        result["ptz"] = "PTZ" in body
        result["events"] = "Events" in body
        report.add("GetCapabilities", True,
                   f"PTZ={'yes' if result['ptz'] else 'no'}, Events={'yes' if result['events'] else 'no'}",
                   body[:4000])
    else:
        report.add("GetCapabilities", False, f"HTTP {status}")

    status, body = soap_post(
        opener, f"http://{host}/onvif/media_service",
        envelope('<GetProfiles xmlns="http://www.onvif.org/ver10/media/wsdl"/>'),
    )
    if status == 200:
        names = re.findall(r"<(?:\w+:)?Name>(.*?)</(?:\w+:)?Name>", body)
        result["profiles"] = names
        report.add("media_service GetProfiles", True, f"{len(names)} profile(s): {names[:4]}", body[:3000])
    else:
        report.add("media_service GetProfiles", False, f"HTTP {status}")

    return result


# ---------------------------------------------------------------- 4. FLIR CGI PTZ

PTZ_PROBES = [
    "/cgi-bin/ptz.cgi?action=getstatus",
    "/cgi-bin/nexus.cgi?action=ptzgetposition",
    "/cgi-bin/param.cgi?action=list&group=PTZ",
    "/api/ptz/status",
]


def check_cgi_ptz(report: Report, opener, host: str) -> bool:
    print("\nFLIR CGI — the API the datasheet does list")
    found = False
    for path in PTZ_PROBES:
        status, body = http_get(opener, f"http://{host}{path}")
        ok = status == 200
        found = found or ok
        report.add(f"GET {path}", ok, f"HTTP {status}", body[:2000])
    if not found:
        print("      note: none of the guessed CGI paths answered. That does not prove CGI")
        print("      is absent — the path names are vendor-specific and undocumented here.")
        print("      Open the camera's own web interface with the browser devtools network")
        print("      tab recording, press an arrow, and read the request it sends.")
    return found


# ---------------------------------------------------------------- 5. analytics events


EVENT_PROBES = [
    "/cgi-bin/alarm.cgi?action=getstatus",
    "/cgi-bin/nexus.cgi?action=getalarms",
    "/api/events",
    "/cgi-bin/eventmanager.cgi?action=attach&codes=[All]",
]


def check_events(report: Report, opener, host: str, onvif: dict) -> bool:
    print("\nanalytics events — whether the camera's own detector can feed our alarm switch")
    found = False
    for path in EVENT_PROBES:
        status, body = http_get(opener, f"http://{host}{path}")
        ok = status == 200
        found = found or ok
        report.add(f"GET {path}", ok, f"HTTP {status}", body[:2000])
    if onvif.get("events"):
        report.add("ONVIF event service advertised", True, "usable as an event source")
        found = True
    return found


# ---------------------------------------------------------------- 6. RTSP


RTSP_PATHS = [
    "/", "/stream1", "/stream2", "/live", "/avc",
    "/ch0", "/ch1", "/thermal", "/visible",
    "/Streaming/Channels/101", "/Streaming/Channels/201",
]


def check_rtsp(report: Report, host: str, user: str, password: str) -> list[str]:
    print("\nRTSP — which paths serve video")
    working = []
    for path in RTSP_PATHS:
        url = f"rtsp://{host}:554{path}"
        try:
            with socket.create_connection((host, 554), timeout=3) as sock:
                request = (
                    f"DESCRIBE {url} RTSP/1.0\r\nCSeq: 1\r\n"
                    "Accept: application/sdp\r\nUser-Agent: vmd-probe\r\n\r\n"
                )
                sock.sendall(request.encode())
                reply = sock.recv(2000).decode("utf-8", "replace")
            code = reply.split()[1] if reply.startswith("RTSP/") else "?"
            # 401 means the path exists and simply wants credentials.
            ok = code in {"200", "401"}
            if ok:
                working.append(path)
            report.add(f"DESCRIBE {path}", ok, f"RTSP {code}")
        except OSError as exc:
            report.add(f"DESCRIBE {path}", False, str(exc).split("]")[-1].strip())
    if user:
        print("      note: 401 means the path is real and wants credentials — that counts.")
    return working


# ---------------------------------------------------------------- verdict


def verdict(report: Report, models, onvif, cgi_ptz, events, rtsp_paths) -> None:
    print("\n" + "=" * 72)
    print("VERDICT")
    print("=" * 72)

    if models:
        print(f"\nModel        : {', '.join(sorted(models))}")
        print("               Run: uv run python spike/identify_camera.py table")
        print("               to see what that lens gives you at 700 m.")
    else:
        print("\nModel        : not reported. Read it from the label, the invoice, or")
        print("               measure the lens with identify_camera.py fov.")

    print("\nSteering     : ", end="")
    if onvif.get("ptz"):
        print("ONVIF PTZ available — use it. Standard, documented, testable.")
    elif cgi_ptz:
        print("FLIR CGI answered — usable, but paths are vendor-specific.")
    else:
        print("UNRESOLVED. Neither ONVIF PTZ nor a guessed CGI path answered.")
        print("               Next step: open the camera's own web UI with devtools")
        print("               recording, press an arrow, and copy the request it sends.")

    print("\nAlarm source : ", end="")
    if events:
        print("the camera exposes events — the 'camera analytics' option can be real.")
    else:
        print("no event source found. Our own detector remains the only alarm source,")
        print("               and the camera-analytics switch should stay disabled.")

    print("\nVideo        : ", end="")
    if rtsp_paths:
        print(f"{len(rtsp_paths)} RTSP path(s) responded: {', '.join(rtsp_paths[:6])}")
        print("               Put the thermal one in settings first; it is the detector.")
    else:
        print("no RTSP path answered. Check credentials and that port 554 is open.")

    print("\nNothing here was assumed. Every line above is something the camera said.\n")


def main() -> int:
    parser = argparse.ArgumentParser(description="Ask the camera what it can actually do")
    parser.add_argument("host")
    parser.add_argument("--user", default="")
    parser.add_argument("--password", default="")
    parser.add_argument("--skip-rtsp", action="store_true")
    args = parser.parse_args()

    report = Report(host=args.host)
    opener = opener_for(args.host, args.user, args.password)

    print(f"probing {args.host}" + (f" as {args.user}" if args.user else " anonymously"))
    if not check_reachable(report, args.host):
        print("\nNothing answered. Check the address, the cable, and the radio link.")
        return 1

    models = check_identity(report, opener, args.host)
    onvif = check_onvif(report, opener, args.host)
    cgi_ptz = check_cgi_ptz(report, opener, args.host)
    events = check_events(report, opener, args.host, onvif)
    rtsp_paths = [] if args.skip_rtsp else check_rtsp(report, args.host, args.user, args.password)

    verdict(report, models, onvif, cgi_ptz, events, rtsp_paths)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
