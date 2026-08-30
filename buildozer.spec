[app]
title = BlueBlood
package.name = blueblood
package.domain = org.blueblood
source.dir = .
source.include_exts = py,png,jpg,kv,atlas,json,txt,csv
version = 1.1
requirements = python3==3.10.12,cython==0.29.36,kivy==2.2.1,kivymd==1.1.1,plyer,requests
orientation = portrait
fullscreen = 0
android.permissions = INTERNET,VIBRATE
android.api = 33
android.minapi = 24
android.archs = arm64-v8a
android.accept_sdk_license = True
icon.filename = icon.png
presplash.filename = icon.png
p4a.branch = v2023.09.16

[buildozer]
log_level = 2
warn_on_root = 0
