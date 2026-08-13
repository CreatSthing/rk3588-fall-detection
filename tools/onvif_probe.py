#!/usr/bin/env python3
import socket
import time
import uuid

message = f"""<?xml version="1.0" encoding="UTF-8"?>
<e:Envelope xmlns:e="http://www.w3.org/2003/05/soap-envelope"
            xmlns:w="http://schemas.xmlsoap.org/ws/2004/08/addressing"
            xmlns:d="http://schemas.xmlsoap.org/ws/2005/04/discovery"
            xmlns:dn="http://www.onvif.org/ver10/network/wsdl">
  <e:Header>
    <w:MessageID>uuid:{uuid.uuid4()}</w:MessageID>
    <w:To>urn:schemas-xmlsoap-org:ws:2005:04:discovery</w:To>
    <w:Action>http://schemas.xmlsoap.org/ws/2005/04/discovery/Probe</w:Action>
  </e:Header>
  <e:Body>
    <d:Probe>
      <d:Types>dn:NetworkVideoTransmitter</d:Types>
    </d:Probe>
  </e:Body>
</e:Envelope>""".encode()

sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM, socket.IPPROTO_UDP)
sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
sock.settimeout(5)
sock.sendto(message, ("239.255.255.250", 3702))

start = time.time()
seen = set()
while time.time() - start < 6:
    try:
        data, addr = sock.recvfrom(65535)
    except socket.timeout:
        break
    if addr in seen:
        continue
    seen.add(addr)
    text = data.decode(errors="ignore")
    print(f"FROM {addr[0]}:{addr[1]}")
    for key in ("XAddrs", "Scopes", "Types"):
        open_tag = f"<{key}>"
        close_tag = f"</{key}>"
        if open_tag in text and close_tag in text:
            print(f"{key}: {text.split(open_tag, 1)[1].split(close_tag, 1)[0]}")
    print(text[:1200].replace("\n", " "))

print(f"responses {len(seen)}")
