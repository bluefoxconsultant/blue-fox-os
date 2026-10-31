#!/usr/bin/env python3
"""Rafale de captures de la VM de tournage (#26870), par le VNC local que
egl-headless alimente (screendump rend « no surface » en affichage GL).

  ~/venvs/tournage/bin/python rafale.py <dossier> <secondes> [<images par s>]

Écrit <dossier>/NNNN.jpg et <dossier>/temps.json (instant de chaque image, en s
depuis le début) : le montage en tire la tenue de chaque image.
"""
import json
import os
import sys
import time

from vncdotool import api

dossier, duree = sys.argv[1], float(sys.argv[2])
ips = float(sys.argv[3]) if len(sys.argv) > 3 else 3.0
os.makedirs(dossier, exist_ok=True)
client = api.connect("127.0.0.1::5903", password=None)
temps, debut, n = [], time.monotonic(), 0
try:
    while time.monotonic() - debut < duree:
        t = time.monotonic()
        client.refreshScreen()
        nom = f"{n:04d}.jpg"
        client.screen.convert("RGB").save(os.path.join(dossier, nom), quality=92)
        temps.append([nom, round(t - debut, 3)])
        n += 1
        reste = 1 / ips - (time.monotonic() - t)
        if reste > 0:
            time.sleep(reste)
finally:
    client.disconnect()
    api.shutdown()
with open(os.path.join(dossier, "temps.json"), "w") as f:
    json.dump(temps, f)
print(f"{n} images en {temps[-1][1] if temps else 0} s")
