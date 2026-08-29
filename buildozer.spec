[app]
title = BlueBlood
package.name = blueblood
package.domain = org.blueblood
source.dir = .
source.include_exts = py,png,jpg,kv,atlas,json,txt,csv
version = 1.4
requirements = hostpython3==3.11.10,python3==3.11.10,kivy==2.3.0,kivymd==1.2.0,plyer,requests
orientation = portrait
fullscreen = 0
android.permissions = INTERNET,VIBRATE
android.api = 35
android.minapi = 26
android.ndk = 28c
android.archs = arm64-v8a
android.accept_sdk_license = True
icon.filename = icon.png
presplash.filename = icon.png
p4a.branch = develop

[buildozer]
log_level = 2
warn_on_root = 0
