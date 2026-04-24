"""
临时字体检测脚本 - 在 AstrBot 环境中运行，找出可用的 CJK 字体路径
使用方法：在 AstrBot 插件目录执行 python3 find_fonts.py
"""
import os
import subprocess

print("=== 系统中所有字体文件 ===")
result = subprocess.run(["find", "/usr/share/fonts", "-name", "*.ttc", "-o", "-name", "*.ttf", "-o", "-name", "*.otf"], 
                       capture_output=True, text=True)
for line in result.stdout.strip().split("\n"):
    if any(k in line.lower() for k in ["noto", "cjk", "chinese", "hans", "wqy", "droid"]):
        print(line)

print("\n=== fc-list 中文字体 ===")
result2 = subprocess.run(["fc-list", ":lang=zh"], capture_output=True, text=True)
print(result2.stdout[:2000])
