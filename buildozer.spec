[app]
title = FRIDAY
package.name = friday
package.domain = com.personal.friday
source.dir = .
source.include_exts = py,png,jpg,jpeg,kv,atlas,json,txt,ttf
source.exclude_patterns = tests/*,*.bak
version = 2.0.0
requirements = python3,kivy==2.3.0,requests,urllib3,chardet,idna,certifi,pyjnius,android
orientation = portrait
fullscreen = 0
android.permissions = INTERNET,RECORD_AUDIO
android.api = 33
android.minapi = 24
android.ndk = 25b
android.accept_sdk_license = True
android.enable_androidx = True
android.allow_backup = False
android.archs = arm64-v8a, armeabi-v7a
p4a.bootstrap = sdl2

[buildozer]
log_level = 2
warn_on_root = 1
